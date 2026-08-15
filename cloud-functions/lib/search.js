// 腾讯动漫移动站搜索
import { HttpError } from './util.js'

const MOBILE_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'zh-CN,zh;q=0.9',
}

const PAGE_SIZE = 10

/** 搜索漫画，返回 { results, hasMore } */
export async function searchComics(keyword, page) {
  const params = new URLSearchParams({
    _t: String(Date.now()),
    word: keyword,
    page: String(page),
    pageSize: String(PAGE_SIZE),
    style: 'items',
  })
  const url = `https://m.ac.qq.com/search/result?${params}`

  let resp
  try {
    resp = await fetch(url, {
      headers: MOBILE_HEADERS,
      signal: AbortSignal.timeout(8000),
    })
  } catch (e) {
    throw new HttpError(502, `搜索请求失败: ${e.message}`)
  }
  if (resp.status !== 200) {
    throw new HttpError(502, `搜索请求失败，状态码: ${resp.status}`)
  }
  const html = new TextDecoder('utf-8').decode(await resp.arrayBuffer())

  const results = []
  const itemRe = /<li class="comic-item">([\s\S]*?)<\/li>/g
  for (const m of html.matchAll(itemRe)) {
    const itemHtml = m[1]
    const linkMatch = itemHtml.match(/href="\/comic\/index\/id\/(\d+)"/)
    if (!linkMatch) continue

    const titleMatch = itemHtml.match(/<strong class="comic-title">([\s\S]*?)<\/strong>/)
    const coverMatch = itemHtml.match(/<img class="cover-image" src="([^"]*)"/)
    let coverUrl = coverMatch ? coverMatch[1] : null
    if (coverUrl && coverUrl.startsWith('//')) coverUrl = 'https:' + coverUrl

    results.push({
      comicId: linkMatch[1],
      title: titleMatch ? titleMatch[1].trim() : '未知标题',
      coverUrl,
    })
  }

  // 启发式判断是否有下一页：本页结果不足一页即没有下一页
  // （旧版为判断 has_more 每次搜索多发一个请求，延迟翻倍）
  return { results, hasMore: results.length >= PAGE_SIZE }
}
