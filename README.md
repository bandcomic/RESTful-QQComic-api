# 腾讯动漫 API - EdgeOne Pages

基于 Node.js (Hono) 的腾讯动漫 RESTful API，部署在腾讯云 EdgeOne Pages。

## 特性

- 搜索漫画：中文关键词分页搜索
- 漫画详情：标题 / 封面 / 评分 / 章节数
- 章节图片：章节加密数据（DATA + nonce）自动解密
- 图片处理：缩放 / JPEG 质量 / PNG 量化 / LVGL 预解码
- Cookie 支持：转发请求方 Cookie，支持已购付费章节
- 图片代理域名白名单，防 SSRF 滥用

## 项目结构

```
├── cloud-functions/
│   ├── index.js          # 入口（路由 /）
│   ├── [[default]].js    # 入口（路由 /*）
│   └── lib/              # 业务模块
│       ├── app.js        # Hono 应用与路由
│       ├── parser.js     # 详情/章节解析 + node:vm nonce 解密
│       ├── search.js     # 搜索
│       ├── image.js      # 图片管线（sharp）
│       ├── cache.js      # TTL 内存缓存
│       └── util.js       # 工具函数
├── local-server.js       # 本地开发服务器
├── package.json
└── edgeone.json
```

## 本地开发

```bash
npm install
npm run dev        # http://localhost:8000
```

## EdgeOne Pages 部署

1. Fork 或克隆此仓库
2. 在 EdgeOne Makers 控制台导入 Git 仓库
3. 区域使用默认（广州）即可，`edgeone.json` 已配置 `maxDuration: 120`
4. 公网地址经 `Eo-Pages-Host` 请求头自动识别；若有异常可配置环境变量 `PUBLIC_URL=https://your-domain` 强制指定

## API

基础 URL：`https://your-project.edgeone.app`

| 端点 | 说明 |
| --- | --- |
| `GET /config` | 漫画源配置 |
| `GET /search/<keyword>/<page>` | 搜索漫画，`page` 从 1 开始 |
| `GET /comic/<comic_id>` | 漫画详情 |
| `GET /photo/<comic_id>/chapter/<chapter>` | 章节图片列表，`chapter` 从 1 开始（正序） |
| `GET /image/proxy?url=<图片地址>` | 图片代理（白名单域名） |

### 图片参数

| 参数 | 说明 |
| --- | --- |
| `width` | 目标宽度（仅缩小），1-2047，默认 600 |
| `quality` | JPEG 质量 / PNG 量化，1-100，默认 50 |
| `ifPNG` | 为 `1` 时返回 PNG |
| `ifLVGL` | 为 `1` 时返回 LVGL 预解码二进制（优先级高于 ifPNG） |

### 错误返回

```json
{ "code": 404, "message": "Comic not found" }
```

## Cookie

免费章节无需登录；付费章节需购买后由请求方携带 Cookie 访问，服务端自动转发上游。带 Cookie 的请求不读写缓存。

## 免责声明

本项目仅用于学习和研究目的，请勿用于商业用途。漫画内容版权归腾讯动漫及原作者所有，请支持正版。

## 许可证

MIT License
