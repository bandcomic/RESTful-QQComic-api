// EdgeOne Pages Cloud Functions 入口：catch-all 路由（/config、/comic、/photo、/search、/image/proxy）
import { handleRequest } from './lib/app.js'

export default function onRequest(context) {
  return handleRequest(context.request, context.env)
}
