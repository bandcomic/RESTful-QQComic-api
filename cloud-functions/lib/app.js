// Hono 应用：全部 API 路由。不导出框架实例/Handler，仅导出普通函数，
// 避免被 EdgeOne 构建器识别为独立路由入口。
import { Hono } from 'hono'
import { getComicInfo, getChapterImages } from './parser.js'
import { searchComics } from './search.js'
import { fetchImageBuffer, processImage } from './image.js'
import { TTLCache } from './cache.js'
import {
  HttpError,
  getApiUrl,
  isAllowedImageUrl,
  clampInt,
  isTruthyArg,
  jsonError,
} from './util.js'

const app = new Hono({ strict: false })

const infoCache = new TTLCache(10 * 60 * 1000) // 漫画详情 10 分钟
const chapterCache = new TTLCache(30 * 60 * 1000) // 章节图片数据 30 分钟

app.onError((err, c) => {
  if (err instanceof HttpError) return jsonError(c, err.status, err.message)
  console.error('未处理异常:', err)
  return jsonError(c, 500, '服务器内部错误')
})

app.notFound((c) => jsonError(c, 404, 'Not Found'))

/** 获取漫画详情（带缓存；带 Cookie 的请求不读不写缓存） */
async function getComicInfoCached(comicId, cookie) {
  if (cookie) return getComicInfo(comicId, cookie)
  let info = infoCache.get(comicId)
  if (!info) {
    info = await getComicInfo(comicId, null)
    infoCache.set(comicId, info)
  }
  return info
}

function proxyImageUrl(apiUrl, originalUrl, page) {
  return `${apiUrl}/image/proxy?url=${encodeURIComponent(originalUrl)}&page=${page}`
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------

app.get('/', (c) => c.text('it works!'))

// 漫画源配置（腕上漫画同步器请求此接口识别漫画源）
app.get('/config', (c) => {
  const apiUrl = getApiUrl(c)
  return c.json({
    QQComic: {
      name: '腾讯动漫',
      apiUrl,
      detailPath: '/comic/<id>',
      photoPath: '/photo/<id>/chapter/<chapter>',
      searchPath: '/search/<text>/<page>',
      type: 'qqcomic',
    },
  })
})

// 漫画详情
app.get('/comic/:id', async (c) => {
  const id = c.req.param('id')
  if (!/^\d+$/.test(id)) return jsonError(c, 400, '无效的漫画ID')

  const cookie = c.req.header('cookie') || null
  const info = await getComicInfoCached(id, cookie)
  if (info.totalChapters === 0) return jsonError(c, 404, 'Comic not found')

  const apiUrl = getApiUrl(c)
  const rate = parseFloat(info.rating.score)

  return c.json({
    item_id: Number(id),
    name: info.title,
    page_count: 0,
    views: info.popularity,
    rate: Number.isFinite(rate) ? rate : 0,
    cover: info.coverUrl ? proxyImageUrl(apiUrl, info.coverUrl, 'cover') : '',
    tags: [],
    total_chapters: info.totalChapters,
  })
})

// 章节图片列表
app.get('/photo/:id/chapter/:chapter', async (c) => {
  const id = c.req.param('id')
  if (!/^\d+$/.test(id)) return jsonError(c, 400, '无效的漫画ID')
  const chapterNum = Number(c.req.param('chapter'))
  if (!Number.isInteger(chapterNum) || chapterNum < 1) {
    return jsonError(c, 400, '章节号必须从 1 开始')
  }

  const cookie = c.req.header('cookie') || null
  const info = await getComicInfoCached(id, cookie)

  // 章节列表已按正序重新编号 1..N，直接按下标取
  const target = info.chapters[chapterNum - 1]
  if (!target) return jsonError(c, 404, `未找到第 ${chapterNum} 章`)

  const cacheKey = target.link
  let data = cookie ? null : chapterCache.get(cacheKey)
  if (!data) {
    data = await getChapterImages(target.link, cookie)
    if (!cookie) chapterCache.set(cacheKey, data)
  }

  const apiUrl = getApiUrl(c)
  const pictures = data.chapterData.picture || []
  const images = pictures
    .filter((p) => p && p.url)
    .map((p, i) => ({ url: proxyImageUrl(apiUrl, p.url, i + 1) }))

  return c.json({
    title: data.chapterTitle || target.title,
    images,
  })
})

// 搜索（Hono 自动对路径参数做 URL 解码，无需二次解码）
async function handleSearch(c, page) {
  const keyword = c.req.param('value')
  if (!keyword) return jsonError(c, 400, '缺少搜索关键词')
  if (!Number.isInteger(page) || page < 1) return jsonError(c, 400, '页码必须大于0')

  const { results, hasMore } = await searchComics(keyword, page)
  const apiUrl = getApiUrl(c)

  return c.json({
    page,
    has_more: hasMore,
    results: results.map((r) => ({
      comic_id: Number(r.comicId),
      title: r.title,
      cover_url: r.coverUrl ? proxyImageUrl(apiUrl, r.coverUrl, 'cover') : '',
      pages: 0,
    })),
  })
}

app.get('/search/:value', (c) => handleSearch(c, 1))
app.get('/search/:value/:page', (c) => {
  const page = Number(c.req.param('page'))
  if (!Number.isInteger(page)) return jsonError(c, 400, '页码必须大于0')
  return handleSearch(c, page)
})

// 图片代理：缩放 / JPEG / PNG / LVGL 预解码（白名单域名，防 SSRF）
app.get('/image/proxy', async (c) => {
  const imageUrl = c.req.query('url')
  if (!imageUrl) return jsonError(c, 400, '缺少url参数')
  if (!isAllowedImageUrl(imageUrl)) return jsonError(c, 403, '不允许的图片地址')

  const width = clampInt(c.req.query('width') ?? c.req.query('w'), 600, 1, 2047, 'width')
  const quality = clampInt(c.req.query('quality') ?? c.req.query('q'), 50, 1, 100, 'quality')

  const buffer = await fetchImageBuffer(imageUrl)
  const { data, contentType } = await processImage(buffer, {
    width,
    quality,
    ifPNG: isTruthyArg(c, 'ifPNG'),
    ifLVGL: isTruthyArg(c, 'ifLVGL'),
  })

  return c.body(new Uint8Array(data), 200, {
    'Content-Type': contentType,
    'Cache-Control': 'public, max-age=86400',
  })
})

/** EdgeOne 入口调用：onRequest(context) → app.fetch */
export function handleRequest(request, env) {
  return app.fetch(request, env)
}
