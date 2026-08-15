// TTL 内存缓存：serverless 暖实例间复用，冷启动后自动重建。
// 注意：带用户 Cookie 的请求一律不读不写，防止付费内容泄露给无 Cookie 的用户。
export class TTLCache {
  constructor(ttlMs, maxSize = 500) {
    this.ttl = ttlMs
    this.maxSize = maxSize
    this.map = new Map()
  }

  get(key) {
    const entry = this.map.get(key)
    if (!entry) return undefined
    if (entry.exp <= Date.now()) {
      this.map.delete(key)
      return undefined
    }
    return entry.value
  }

  set(key, value) {
    if (this.map.size >= this.maxSize) {
      const now = Date.now()
      for (const [k, e] of this.map) {
        if (e.exp <= now) this.map.delete(k)
      }
      // 清理过期项后仍超限则整体清空，防内存膨胀
      if (this.map.size >= this.maxSize) this.map.clear()
    }
    this.map.set(key, { value, exp: Date.now() + this.ttl })
  }
}
