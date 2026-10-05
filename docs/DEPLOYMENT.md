# 三平台部署

核心为cloud-functions/backend.py，平台只导出WSGI app。章节去重后重新编号，
无中文冒号的标题也能处理；MiniRacer每次独立创建，eval有1秒预算。
上游统一线程本地Session、连接/读取短超时、下载中限制字节；Cookie不输出日志。
解析数据在缓存中保留原图URL，公网代理URL在响应时生成。

VPS：Python3.14 venv安装requirements，加载 `.env.example` 对应的 `.env`，
`gunicorn -c gunicorn.conf.py index:app`。Vercel使用根index.py和Python3.12。
EdgeOne使用Python云函数薄入口；需验证Linux py-mini-racer原生库进入产物并执行。
本机Windows已执行V8表达式，不代替Linux平台验证。

VPS设置CACHE_DB用于SQLite共享缓存/所有者租约，默认2worker×2thread，冷图片
单worker1、全局2；内存64MiB/worker、磁盘内容512MiB。Serverless留空仅暖实例协调。
REDIS_URL可选共享成品/元数据/租约，短连接超时，协调不可用503；配置noeviction。
缓存事务原子提交、TTL/容量淘汰，磁盘实际占用还有WAL与空闲页。

图片width/w 1–2047、quality/q 1–100；LVGL>PNG>JPEG，白底透明，长图完整等比缩小。
下载20MiB、解码3200万像素、响应4MiB；超限JSON413，不切换格式。
公共图24h，鉴权private,no-store，错误no-store。CDN鉴权跳过缓存读写、保留全部参数。
Nginx转发127.0.0.1:3004，传Host与X-Forwarded-Host/Proto，读超时25秒。
PUBLIC_URL可含路径前缀。诊断 `/health/runtime`、X-Cache、原始/实际尺寸。
