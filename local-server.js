// 本地开发服务器（EdgeOne 部署不依赖此文件）
import { serve } from '@hono/node-server'
import { handleRequest } from './cloud-functions/lib/app.js'

const port = Number(process.env.PORT || 8000)
serve({ fetch: handleRequest, port }, (info) => {
  console.log(`本地服务已启动: http://localhost:${info.port}`)
})
