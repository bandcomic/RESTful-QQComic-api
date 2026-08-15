// 通用工具：公网地址解析、图片地址白名单、参数校验、统一错误

/** 业务错误，status 为返回给客户端的 HTTP 状态码 */
export class HttpError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

/**
 * 获取对外可访问的 API 基础地址。
 * EdgeOne Pages 会将 Host 改写为内部域名，原始访问域名通过 Eo-Pages-Host 请求头透传（平台强制 HTTPS）。
 * 优先级：PUBLIC_URL 环境变量 > Eo-Pages-Host > X-Forwarded-* > 请求 URL。
 */
export function getApiUrl(c) {
  const envUrl =
    c.env?.PUBLIC_URL ||
    (typeof process !== 'undefined' ? process.env?.PUBLIC_URL : undefined)
  if (envUrl) return envUrl.replace(/\/+$/, '')

  const eoHost = c.req.header('eo-pages-host')?.trim()
  if (eoHost) return `https://${eoHost}`

  const xfHost = c.req.header('x-forwarded-host')?.split(',')[0]?.trim()
  const xfProto = c.req.header('x-forwarded-proto')?.split(',')[0]?.trim()
  if (xfHost && xfProto) return `${xfProto}://${xfHost}`

  return new URL(c.req.url).origin
}

// 图片地址白名单：仅允许腾讯动漫相关域名，防止本服务被当作开放代理（SSRF）
const ALLOWED_IMAGE_HOSTS = ['ac.qq.com', 'acimg.cn', 'qpic.cn', 'gtimg.cn']

export function isAllowedImageUrl(urlStr) {
  let u
  try {
    u = new URL(urlStr)
  } catch {
    return false
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return false
  const host = u.hostname
  return ALLOWED_IMAGE_HOSTS.some((d) => host === d || host.endsWith('.' + d))
}

/** 解析整数参数，非法时抛 400；结果 clamp 到 [min, max] */
export function clampInt(raw, fallback, min, max, name) {
  if (raw === undefined || raw === null || raw === '') return fallback
  const n = Number(raw)
  if (!Number.isFinite(n)) throw new HttpError(400, `参数 ${name} 非法`)
  return Math.max(min, Math.min(max, Math.trunc(n)))
}

/** truthy 查询参数判断：1/true/yes/on */
export function isTruthyArg(c, name) {
  const v = c.req.query(name)
  return v !== undefined && ['1', 'true', 'yes', 'on'].includes(v.toLowerCase())
}

export function jsonError(c, status, message) {
  return c.json({ code: status, message }, status)
}
