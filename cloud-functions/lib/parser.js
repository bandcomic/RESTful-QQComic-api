// 腾讯动漫详情页 / 章节页解析
// 注意：不得导入任何 node: 内置模块——EdgeOne 打包器会对 node: 导入注入 polyfill，
// 会导致 vm 沙箱失效并产生运行时错误（stdin_default is not defined）
import { HttpError } from './util.js'

const DESKTOP_HEADERS = {
  accept:
    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
  'accept-language': 'zh-CN,zh;q=0.9',
  'user-agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
}

// 不声明 accept-encoding：undici 自动处理 gzip/deflate/br，
// 手动声明且不支持的编码会导致响应乱码（旧 Python 版的 br 乱码 bug）

async function fetchText(url, cookie, timeoutMs = 10000) {
  const headers = { ...DESKTOP_HEADERS }
  if (cookie) headers.cookie = cookie
  let resp
  try {
    resp = await fetch(url, { headers, signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    throw new HttpError(502, `上游请求失败: ${e.message}`)
  }
  if (resp.status !== 200) {
    throw new HttpError(502, `上游请求失败，状态码: ${resp.status}`)
  }
  // 统一按 UTF-8 解码，避免响应头缺 charset 时中文乱码
  return new TextDecoder('utf-8').decode(await resp.arrayBuffer())
}

/** 提取漫画基本信息（标题/封面/人气/评分/章节列表） */
export async function getComicInfo(comicId, cookie) {
  const url = `https://ac.qq.com/Comic/comicInfo/id/${comicId}`
  const html = await fetchText(url, cookie)

  // 提取标题：优先详情页专用标题元素，回退到 <title>（形如《航海王》漫画_..._腾讯动漫，取书名号内文本）
  let title = null
  const introMatch = html.match(/class="works-intro-title[^"]*"[^>]*>[\s\S]*?<strong>([\s\S]*?)<\/strong>/)
  if (introMatch) {
    title = introMatch[1].trim()
  } else {
    const titleMatch = html.match(/<title>([\s\S]*?)<\/title>/)
    if (titleMatch) {
      const bookMatch = titleMatch[1].match(/《([^》]+)》/)
      title = (bookMatch ? bookMatch[1] : titleMatch[1].split('-')[0]).trim()
    }
  }
  title = title || '未知标题'

  // 提取封面图片
  const coverMatch = html.match(
    /<div class="works-cover[^"]*">\s*<a[^>]*>\s*<img src="([^"]*)"[^>]*>/
  )
  let coverUrl = null
  if (coverMatch) {
    coverUrl = coverMatch[1]
    if (coverUrl.startsWith('//')) coverUrl = 'https:' + coverUrl
    else if (coverUrl.startsWith('/')) coverUrl = 'https://ac.qq.com' + coverUrl
  }

  // 提取人气
  const popularityMatch = html.match(/<span>人气：<em>([\s\S]*?)<\/em><\/span>/)
  const popularity = popularityMatch ? popularityMatch[1].trim() : '未知'

  // 提取评分
  const ratingMatch = html.match(
    /评分：<strong[^>]*>([\s\S]*?)<\/strong>\s*\(<span>(\d+)<\/span>人评分\)/
  )
  const rating = ratingMatch
    ? { score: ratingMatch[1].trim(), ratingCount: ratingMatch[2].trim() }
    : { score: '未知', ratingCount: '0' }

  // 提取章节列表（跳过"开始阅读"等链接）
  const chapterRe =
    /<a(?!.*?开始阅读)[^>]*?title="([^"]+?)"[^>]*?href="(\/ComicView\/index\/id\/\d+\/cid\/\d+)"[^>]*?>([\s\S]*?)<\/a>/g
  const chapters = []
  const seenCids = new Set()
  for (const m of html.matchAll(chapterRe)) {
    const titleAttr = m[1].trim()
    const link = 'https://ac.qq.com' + m[2]
    const linkText = m[3].replace(/\s+/g, ' ').trim()

    // 按 cid 先去重，再统一编号（旧版先编号后去重会导致章节号空洞）
    const cidMatch = link.match(/\/cid\/(\d+)/)
    const cid = cidMatch ? cidMatch[1] : link
    if (seenCids.has(cid)) continue
    seenCids.add(cid)

    // 章节名：title 属性形如"王牌御史：01，缘起"，没有全角冒号时回退到链接文本
    const parts = titleAttr.split('：')
    const name =
      parts.length > 1 ? parts.slice(1).join('：').trim() : linkText || titleAttr

    chapters.push({ cid: Number(cidMatch ? cidMatch[1] : 0), link, title: name })
  }

  // 详情页章节列表可能为倒序（最新在前）：用首尾 cid 大小判断，倒序则反转
  if (chapters.length >= 2 && chapters[0].cid > chapters[chapters.length - 1].cid) {
    chapters.reverse()
  }
  // 去重排序后统一重新编号
  chapters.forEach((ch, i) => {
    ch.number = i + 1
    delete ch.cid
  })

  return {
    comicId,
    title,
    popularity,
    rating,
    chapters,
    coverUrl,
    totalChapters: chapters.length,
  }
}

/** 在受控作用域内执行页面混淆 JS，求出 nonce 字符串。
 *  页面表达式仅为字符串拼接与算术（如 "f4" + (+eval("1 - !!document.children")).toString() + ...），
 *  用 new Function 注入 document 桩即可求值；黑名单校验防注入。 */
function evalNonceExpr(expr) {
  if (expr.length > 2000) throw new Error('nonce 表达式过长')
  if (
    /\b(require|import|export|process|global|globalThis|Function|constructor|__proto__|prototype|while|for|new|this|fetch|Promise)\b/.test(
      expr
    )
  ) {
    throw new Error('nonce 表达式包含非法内容')
  }
  const fn = new Function('document', `"use strict"; return (${expr});`)
  const result = fn({ children: [1] })
  return typeof result === 'string' ? result : String(result)
}

/** 用 nonce 从加密 DATA 中删除干扰字符并 base64 解码为 JSON */
function decodeChapterData(data, nonce) {
  const chars = data.split('')
  const segments = nonce.match(/\d+[a-zA-Z]+/g) || []
  for (let i = segments.length - 1; i >= 0; i--) {
    const locate = parseInt(segments[i].match(/\d+/)[0], 10) & 255
    const str = segments[i].replace(/\d+/g, '')
    chars.splice(locate, str.length)
  }
  // base64 → UTF-8 JSON（atob 需补齐 padding）
  let b64 = chars.join('')
  const rem = b64.length % 4
  if (rem) b64 += '='.repeat(4 - rem)
  const bin = atob(b64)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  const decoded = new TextDecoder('utf-8').decode(bytes)
  const json = JSON.parse(decoded)
  if (!Array.isArray(json.picture)) throw new Error('解密结果缺少 picture 数组')
  return json
}

/**
 * 提取章节图片数据。
 * 页面内有多个 window["nonce"] 赋值（含诱饵），逐个求值并实际尝试解密，
 * 能解出合法 picture 数组的才是正确 nonce。
 */
export async function getChapterImages(chapterUrl, cookie) {
  const html = await fetchText(chapterUrl, cookie)

  // 提取章节标题
  const titleMatch = html.match(/<title>《[^》]*》([\s\S]*?)-[\s\S]*?<\/title>/)
  const chapterTitle = titleMatch ? titleMatch[1].trim() : null

  // 提取加密数据
  const dataMatch = html.match(/var DATA = '([^']+)'/)
  if (!dataMatch) throw new HttpError(502, '未找到章节加密数据（页面结构可能已变更）')
  const data = dataMatch[1]

  // 提取全部 window[...] 赋值表达式
  const windowMatches = html.match(/window\[".+?;/g) || []
  if (windowMatches.length === 0) {
    throw new HttpError(502, '未找到 nonce 数据（页面结构可能已变更）')
  }

  // 倒序尝试（真实 nonce 通常在最后一个），求值成功即验证解密
  let lastError = null
  for (const raw of [...windowMatches].reverse()) {
    const eq = raw.indexOf('=')
    if (eq < 0) continue
    const expr = raw.substring(eq + 1).replace(/;\s*$/, '')
    let nonce
    try {
      nonce = evalNonceExpr(expr)
    } catch (e) {
      lastError = e
      continue
    }
    if (!/^[0-9a-f]+$/i.test(nonce)) continue
    try {
      const chapterData = decodeChapterData(data, nonce)
      return { chapterData, chapterTitle }
    } catch (e) {
      lastError = e
      continue
    }
  }

  throw new HttpError(502, `nonce 解析失败: ${lastError ? lastError.message : '无可用候选'}`)
}
