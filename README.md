# 腾讯动漫 API - EdgeOne Pages

基于 Python (Flask) 的腾讯动漫 RESTful API，部署在腾讯云 EdgeOne Pages。

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
│   ├── index.py          # Flask 入口（路由 /）
│   └── [[default]].py    # Flask 入口（路由 /*），与 index.py 内容一致
├── requirements.txt
└── edgeone.json
```

> 注意：`cloud-functions/` 下两个入口文件内容一致，修改代码时需保持两份同步。

## 本地开发

```bash
pip install -r requirements.txt
python cloud-functions/index.py        # http://localhost:8000
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

## 实现说明

- **章节解密**：章节页的 `var DATA` 为插入干扰字符的 base64，干扰位置由页面 `window["nonce"]` 混淆 JS 决定。混淆表达式属固定模板家族（字符串拼接、数字四则/位运算、`!`/`~~` 链、`window`/`document` 取真值、三元、`parseInt`/`Math.*`、`substring`/`charCodeAt`），本服务用零依赖的微型求值器求值，逐个候选实际试解密验证，免疫页面中的诱饵 nonce 与模板轮换。
- **章节顺序**：详情页章节先去重，再按首尾 cid 自动识别正倒序并纠正，最后统一重新编号，客户端按 `chapter=1..N` 正序访问。
- **缓存**：详情 10 分钟、章节数据 30 分钟（模块级内存缓存，暖实例间共享）。

## 免责声明

本项目仅用于学习和研究目的，请勿用于商业用途。漫画内容版权归腾讯动漫及原作者所有，请支持正版。

## 许可证

MIT License
