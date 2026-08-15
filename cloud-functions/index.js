// EdgeOne Pages Cloud Functions 入口：根路由 /（[[default]] 不匹配零路径段）
import { handleRequest } from './lib/app.js'

export default function onRequest(context) {
  return handleRequest(context.request, context.env)
}
