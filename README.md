# 腾讯动漫 RESTful API (EdgeOne / Vercel / VPS)

基于 Python (Flask) 的腾讯动漫 RESTful API，同时支持 **Tencent EdgeOne Makers**、**Vercel** 以及 **本地 VPS (Gunicorn + PM2)** 部署。

## 特性

- **三平台统一部署**：单份业务核心，支持 EdgeOne Makers 云函数、Vercel Serverless 与 Linux VPS。
- **搜索漫画**：中文关键词分页搜索。
- **漫画详情**：标题 / 封面 / 评分 / 章节数。
- **章节图片**：章节加密数据（DATA + nonce）自动解密。
- **图片处理与缓存**：缩放 / JPEG 质量 / PNG 量化 / LVGL 8 预解码，具备 ETag/304 条件请求与多级缓存。
- **API 直出图片**：无开放代理，内置域名白名单，防 SSRF。
- **Cookie 支持**：转发请求方 Cookie，支持已购付费章节。带 Cookie 请求自动私有化。

## 项目结构

```text
├── cloud-functions/
│   ├── index.py          # EdgeOne 云函数入口（路由 /）
│   ├── [[default]].py    # EdgeOne 云函数入口（路由 /*）
│   ├── backend.py        # 业务核心逻辑
│   └── runtime.py        # 缓存、图片处理与并发协调管线
├── docs/
│   └── DEPLOYMENT.md     # 三平台部署指南与环境变量说明
├── ecosystem.config.js   # 本地 VPS PM2 进程配置文件（默认端口 3004）
├── gunicorn.conf.py      # 本地 VPS Gunicorn 生产配置
├── edgeone.json          # EdgeOne Makers 云函数配置
├── vercel.json           # Vercel Serverless 全局重写配置
├── index.py              # Vercel 与 Gunicorn 入口
└── requirements.txt      # Python 依赖
```

## 部署方式

详细部署步骤和生产反向代理示例请见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)。

### 1. EdgeOne Makers 部署
1. 在 EdgeOne Makers 控制台导入 Git 仓库；
2. 运行环境选择 Python 3.10，根目录部署；
3. `cloud-functions/` 下的函数会自动被识别为云函数。

### 2. Vercel 部署
1. 导入 GitHub 仓库到 Vercel；
2. Framework Preset 选择 Other；
3. `vercel.json` 已配置全局重写至 `index.py`。

### 3. 本地 Linux VPS 部署 (PM2 + Gunicorn)
```bash
pip install -r requirements.txt
pm2 start ecosystem.config.js
```

## API 接口

| 端点 | 说明 |
| --- | --- |
| `GET /config` | 漫画源配置 |
| `GET /health` | 健康检查探针 |
| `GET /search/<keyword>/<page>` | 搜索漫画，`page` 从 1 开始 |
| `GET /comic/<comic_id>` | 漫画详情 |
| `GET /comic/<comic_id>/cover` | 漫画封面 |
| `GET /photo/<comic_id>/chapter/<chapter>` | 章节图片列表，`chapter` 从 1 开始（正序） |
| `GET /photo/<comic_id>/chapter/<chapter>/<page>.jpg` | 单页图片，`page` 从 1 开始 |

### 图片参数（封面 / 单页图片通用）

| 参数 | 说明 |
| --- | --- |
| `width` / `w` | 目标宽度（仅缩小），封面默认 200，正文默认 600，上限 2047 |
| `quality` / `q`| 质量参数，1-100，默认 50 |
| `ifPNG` | 为 `1` 时返回 PNG |
| `ifLVGL` | 为 `1` 时返回 LVGL 预解码二进制（indexed-8 结构，上限 2047x2047） |

## 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 免责声明

本项目仅用于学习和研究目的，请勿用于商业用途。漫画内容版权归腾讯动漫及原作者所有，请支持正版。
