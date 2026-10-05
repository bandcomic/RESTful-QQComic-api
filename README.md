# QQComic API - Vercel Flask 解决方案

一个基于 Flask 的 QQComic API 服务，部署在 Vercel 平台上，支持作为自定义漫画源使用。

## 🚀 项目简介

这是一个为 QQComic 提供的 RESTful API 服务，使用 Flask 框架开发，并部署在 Vercel 无服务器平台上。项目提供了对 QQComic 内容的程序化访问接口。

## ✨ 特性

- 🌐 **RESTful API** - 标准的 REST API 设计
- ⚡ **Vercel 部署** - 无服务器架构，快速响应
- 🔒 **安全可靠** - 基于 Flask 的安全框架
- 📚 **QQComic 集成** - 专为 QQComic 内容优化
- 🐍 **Python 驱动** - 使用 Flask 轻量级框架
- 🎯 **自定义漫画源** - 完全符合自定义漫画源规范，支持设备互联
- 🍪 **Cookie 支持** - 支持通过 Cookie 认证访问受限内容
- 🖼️ **图片代理** - 支持按宽度缩放、质量压缩、PNG 输出和 LVGL 预解码二进制输出

## 🛠️ 技术栈

- **后端框架**: Flask
- **部署平台**: Vercel
- **编程语言**: Python 3.x
- **API 风格**: RESTful

## 📦 安装与部署

### 前提条件

- Python 3.7+
- Vercel 账户
- Git

### 本地开发

1. **克隆项目**
```bash
git clone https://github.com/sf-yuzifu/vercel-flask-QQComic-api.git
cd vercel-flask-QQComic-api
```

2. **安装 Vercel CLI**
```bash
npm i -g vercel
```

3. **运行开发服务器**
```bash
vercel dev
```

### Vercel 部署

1. **Fork 或克隆此仓库**

2. **安装 Vercel CLI**

3. **部署到 Vercel**
```bash
vercel
```

或者通过 Vercel 控制台直接导入 GitHub 仓库进行部署。

## 📖 API 文档

### 基础信息

- **基础URL**: `https://your-app.vercel.app`
- **默认端口**: 3000 (Vercel)
- **支持协议**: HTTPS (必须)

### 可用端点

#### 获取漫画源配置
```
GET /config
```
返回漫画源配置信息，用于客户端自动识别和配置。

**响应示例**:
```json
{
  "QQComic": {
    "name": "腾讯动漫",
    "apiUrl": "https://your-app.vercel.app",
    "detailPath": "/comic/<id>",
    "photoPath": "/photo/<id>/chapter/<chapter>",
    "searchPath": "/search/<text>/<page>",
    "type": "qqcomic"
  }
}
```

#### 获取漫画详情 (detailPath)
```
GET /comic/<comic_id>
```
参数:
- `comic_id`: 漫画ID

**响应示例**:
```json
{
  "item_id": 114514,
  "name": "漫画名称",
  "page_count": 0,
  "views": "1919810",
  "rate": 9.0,
  "cover": "https://your-app.vercel.app/image/proxy?url=...&page=cover",
  "tags": [],
  "total_chapters": 10
}
```

#### 获取章节图片 (photoPath)
```
GET /photo/<comic_id>/chapter/<chapter_number>
```
参数:
- `comic_id`: 漫画ID
- `chapter_number`: 章节号

**请求头**:
- `Cookie`: 可选，用于访问受限内容

**响应示例**:
```json
{
  "title": "章节名称",
  "images": [
    {"url": "https://your-app.vercel.app/image/proxy?url=...&page=1"},
    {"url": "https://your-app.vercel.app/image/proxy?url=...&page=2"}
  ]
}
```

#### 搜索漫画 (searchPath)
```
GET /search/<keyword>/<page>
```
参数:
- `keyword`: 搜索关键词 (支持 URL 编码)
- `page`: 页码 (从 1 开始)

**响应示例**:
```json
{
  "page": 1,
  "has_more": true,
  "results": [
    {
      "comic_id": 114514,
      "title": "漫画名称",
      "cover_url": "https://your-app.vercel.app/image/proxy?url=...&page=cover",
      "pages": 0
    }
  ]
}
```

#### 图片代理
```
GET /image/proxy?url=<image_url>&width=<width>&quality=<quality>
```
参数:
- `url`: 原始图片 URL (必须)
- `width` 或 `w`: 目标宽度，默认 600
- `quality` 或 `q`: 图片质量 (1-100)，默认 50
- `ifPNG`: 为 `1`、`true`、`True`、`yes`、`on` 时返回 PNG
- `ifLVGL`: 为 `1`、`true`、`True`、`yes`、`on` 时返回 LVGL 预解码二进制

输出优先级：

```text
ifLVGL=1 > ifPNG=1 > 默认 JPEG
```

返回内容:
- 默认返回 `Content-Type: image/jpeg`
- 开启 `ifPNG` 返回 `Content-Type: image/png`
- 开启 `ifLVGL` 返回 `Content-Type: application/octet-stream`

腕上漫画会在请求封面和正文图片时自动追加 `width`、`quality`、`ifPNG`、`ifLVGL` 等参数，图片列表接口不需要提前拼好这些参数。

## 🍪 Cookie 认证支持

本项目支持通过 Cookie 认证访问 QQComic 的受限内容。

### 使用方法

1. **获取 Cookie**
   - 在浏览器中登录 QQComic
   - 打开开发者工具 (F12)
   - 在 Network 标签中找到任意请求
   - 复制 Request Headers 中的 Cookie 值

2. **在请求中添加 Cookie**
   - 在请求头中添加 `Cookie` 字段
   - Cookie 会自动附加到所有需要认证的请求中

3. **设备互联功能**
   - 如果使用支持设备互联的客户端，可以通过客户端的 Cookie 上传功能配置
   - Cookie 会以 JSON 格式存储，格式为 `{"QQComic": "cookie_value"}`
   - `QQComic` 对应 `/config` 返回对象中的漫画源键名

### 注意事项

- Cookie 可能会过期，需要定期更新
- 请妥善保管您的 Cookie，不要泄露给他人
- Cookie 认证主要用于访问 VIP 或受限内容

## 📱 作为自定义漫画源使用

本项目完全符合自定义漫画源配置规范，可以直接作为自定义漫画源部署使用。

### 部署步骤

1. **部署到 Vercel**
   - 按照 [Vercel 部署](#vercel-部署) 章节的步骤进行部署
   - 确保使用自定义域名并配置 HTTPS

2. **配置自定义域名**
   - 在 Vercel 控制台中添加自定义域名
   - 配置 DNS 记录指向 Vercel
   - 等待 SSL 证书自动生成

3. **在客户端中添加源**
   - 在支持自定义漫画源的客户端中
   - 输入你的自定义域名作为 API 地址
   - 客户端会自动通过 `/config` 路由获取配置信息

### 支持的功能

- ✅ 漫画搜索
- ✅ 漫画详情获取
- ✅ 章节图片获取
- ✅ 图片尺寸和质量自适应
- ✅ Cookie 认证
- ✅ 设备互联支持

## 🗂️ 项目结构

```
vercel-flask-QQComic-api/
├── api/
│   └── index.py          # Vercel Serverless Function 入口
├── requirements.txt      # Python 依赖
├── vercel.json          # Vercel 配置文件
└── README.md            # 项目说明文档
```

### 核心模块说明

- **ComicParser**: 漫画信息解析器，负责解析 QQComic 页面数据
- **ComicSearch**: 漫画搜索模块，提供搜索功能
- **图片代理**: 根据设备性能自动调整图片尺寸和质量

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## ⚠️ 免责声明

本项目仅用于学习和研究目的，请勿用于商业用途。使用者应对其行为负责，作者不承担任何法律责任。

### 特别说明

- 本项目仅提供 API 接口服务，不存储任何漫画内容
- 所有漫画内容均来自 QQComic 官方网站
- 请遵守 QQComic 的使用条款和相关法律法规
- 使用 Cookie 认证功能时，请确保你有权访问相应内容

## 📞 联系

- GitHub: [@sf-yuzifu](https://github.com/sf-yuzifu)
- 项目地址: [https://github.com/sf-yuzifu/vercel-flask-QQComic-api](https://github.com/sf-yuzifu/vercel-flask-QQComic-api)

---

如果这个项目对你有帮助，请给个 ⭐ Star 支持一下！
# 三平台部署更新

统一核心、章节缓存、MiniRacer 与图片预算见 [部署说明](docs/DEPLOYMENT.md)。
