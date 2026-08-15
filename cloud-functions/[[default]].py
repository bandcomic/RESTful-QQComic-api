# 腾讯动漫 RESTful API - EdgeOne Pages Cloud Functions (Python/Flask)
#
# 路由：/config、/comic/<id>、/photo/<id>/chapter/<n>、/search/<text>/<page>、/image/proxy
# 注意：cloud-functions/index.py 与 [[default]].py 内容需保持一致（EdgeOne 双入口）
import base64
import json
import logging
import re
import struct
import time
from io import BytesIO
from threading import Lock
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request
from PIL import Image

app = Flask(__name__)
app.debug = False
app.json.ensure_ascii = False

Image.MAX_IMAGE_PIXELS = 40_000_000  # 防解压炸弹


class WSGIPathFixMiddleware:
    """修复部分 Serverless 运行时（如 EdgeOne）将 URL 解码为 Unicode 后
    直接放入 PATH_INFO/QUERY_STRING 的问题。

    PEP 3333 要求 PATH_INFO 为 latin-1 范围内的 str（表示原始字节），
    Werkzeug 会 encode('latin1') 还原字节。若运行时放入的是真正的
    Unicode 字符串（如 /search/航海王/1），需要重新按 UTF-8 编码
    再按 latin-1 解码，恢复 WSGI 标准格式。
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        for key in ("PATH_INFO", "QUERY_STRING", "SCRIPT_NAME"):
            value = environ.get(key)
            if not value:
                continue
            try:
                value.encode("latin1")
            except UnicodeEncodeError:
                environ[key] = value.encode("utf-8").decode("latin1")
        return self.wsgi_app(environ, start_response)


app.wsgi_app = WSGIPathFixMiddleware(app.wsgi_app)


class ApiError(Exception):
    """业务错误，status 为返回给客户端的 HTTP 状态码"""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@app.errorhandler(ApiError)
def handle_api_error(e):
    if e.status >= 500:
        logging.warning(f"上游错误 {e.status}: {e.message}")
    return jsonify({"code": e.status, "message": e.message}), e.status


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    logging.exception("未处理异常")
    return jsonify({"code": 500, "message": "服务器内部错误"}), 500


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def get_api_url():
    """获取对外可访问的 API 基础地址。

    EdgeOne Pages 会将 Host 改写为内部域名，原始访问域名通过 Eo-Pages-Host
    请求头透传（平台强制 HTTPS）。优先级：PUBLIC_URL 环境变量 > Eo-Pages-Host
    > X-Forwarded-* > Host。
    """
    import os

    public = os.environ.get("PUBLIC_URL")
    if public:
        return public.rstrip("/")

    eo_host = request.headers.get("Eo-Pages-Host", "").strip()
    if eo_host:
        return f"https://{eo_host}"

    host = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    if host and proto:
        return f"{proto}://{host}"
    if not host:
        host = request.host
    if not proto:
        proto = request.scheme
    return f"{proto}://{host}"


# 图片地址白名单：仅允许腾讯动漫相关域名，防止本服务被当作开放代理（SSRF）
ALLOWED_IMAGE_HOSTS = ("ac.qq.com", "acimg.cn", "qpic.cn", "gtimg.cn")


def is_allowed_image_url(url):
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = u.hostname or ""
    return any(host == d or host.endswith("." + d) for d in ALLOWED_IMAGE_HOSTS)


def clamp_int(raw, fallback, min_v, max_v, name):
    if raw is None or raw == "":
        return fallback
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"参数 {name} 非法")
    return max(min_v, min(max_v, n))


def is_truthy_arg(name):
    return request.args.get(name, "0").lower() in ("1", "true", "yes", "on")


class TTLCache:
    """TTL 内存缓存：serverless 暖实例间复用，冷启动后自动重建。
    注意：带用户 Cookie 的请求一律不读不写，防止付费内容泄露。"""

    def __init__(self, ttl_seconds, max_size=500):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._data = {}
        self._lock = Lock()

    def get(self, key):
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            if entry[0] <= now:
                del self._data[key]
                return None
            return entry[1]

    def set(self, key, value):
        now = time.time()
        with self._lock:
            if len(self._data) >= self.max_size:
                for k in [k for k, v in self._data.items() if v[0] <= now]:
                    del self._data[k]
                if len(self._data) >= self.max_size:
                    self._data.clear()
            self._data[key] = (now + self.ttl, value)


info_cache = TTLCache(10 * 60)  # 漫画详情 10 分钟
chapter_cache = TTLCache(30 * 60)  # 章节图片数据 30 分钟


# ---------------------------------------------------------------------------
# 上游抓取
# ---------------------------------------------------------------------------

DESKTOP_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}
# 不声明 accept-encoding：requests 自动处理 gzip/deflate，
# 手动声明 br 而未安装 brotli 会导致响应乱码

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch_text(url, cookie=None, timeout=10, mobile=False, retries=1):
    """抓取文本，统一 UTF-8 解码。仅对网络异常重试，非 200 状态码属确定性错误不重试。"""
    headers = dict(MOBILE_HEADERS if mobile else DESKTOP_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    last_exc = None
    for _ in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            continue
        if resp.status_code != 200:
            raise ApiError(502, f"上游请求失败，状态码: {resp.status_code}")
        resp.encoding = "utf-8"  # 统一 UTF-8，避免缺 charset 时中文乱码
        return resp.text
    raise ApiError(502, f"上游请求失败: {last_exc}")


def get_comic_info(comic_id, cookie=None):
    """获取漫画基本信息（标题/封面/人气/评分/章节列表）"""
    html = fetch_text(f"https://ac.qq.com/Comic/comicInfo/id/{comic_id}", cookie)

    # 标题：优先详情页专用标题元素，回退到 <title>（形如《航海王》漫画_...，取书名号内文本）
    title = None
    m = re.search(r'class="works-intro-title[^"]*"[^>]*>[\s\S]*?<strong>([\s\S]*?)</strong>', html)
    if m:
        title = m.group(1).strip()
    else:
        m = re.search(r"<title>([\s\S]*?)</title>", html)
        if m:
            book = re.search(r"《([^》]+)》", m.group(1))
            title = (book.group(1) if book else m.group(1).split("-")[0]).strip()
    title = title or "未知标题"

    # 封面
    cover_url = None
    m = re.search(r'<div class="works-cover[^"]*">\s*<a[^>]*>\s*<img src="([^"]*)"[^>]*>', html)
    if m:
        cover_url = m.group(1)
        if cover_url.startswith("//"):
            cover_url = "https:" + cover_url
        elif cover_url.startswith("/"):
            cover_url = "https://ac.qq.com" + cover_url

    # 人气
    m = re.search(r"<span>人气：<em>([\s\S]*?)</em></span>", html)
    popularity = m.group(1).strip() if m else "未知"

    # 评分
    m = re.search(r"评分：<strong[^>]*>([\s\S]*?)</strong>\s*\(<span>(\d+)</span>人评分\)", html)
    rating_score = m.group(1).strip() if m else "未知"

    # 章节列表（跳过"开始阅读"等链接），按 cid 去重
    chapter_re = re.compile(
        r'<a(?!.*?开始阅读)[^>]*?title="([^"]+?)"[^>]*?href="(/ComicView/index/id/\d+/cid/\d+)"[^>]*?>([\s\S]*?)</a>'
    )
    chapters = []
    seen_cids = set()
    for m in chapter_re.finditer(html):
        title_attr = m.group(1).strip()
        link = "https://ac.qq.com" + m.group(2)
        link_text = re.sub(r"\s+", " ", m.group(3)).strip()

        cid_match = re.search(r"/cid/(\d+)", link)
        cid = cid_match.group(1) if cid_match else link
        if cid in seen_cids:
            continue
        seen_cids.add(cid)

        # 章节名：title 属性形如"王牌御史：01，缘起"，没有全角冒号时回退链接文本
        parts = title_attr.split("：")
        name = "：".join(parts[1:]).strip() if len(parts) > 1 else (link_text or title_attr)
        chapters.append({"cid": int(cid_match.group(1)) if cid_match else 0, "link": link, "title": name})

    # 详情页章节列表可能为倒序（最新在前）：用首尾 cid 大小判断，倒序则反转
    if len(chapters) >= 2 and chapters[0]["cid"] > chapters[-1]["cid"]:
        chapters.reverse()
    # 去重排序后统一重新编号（旧版先编号后去重会导致章节号空洞）
    for i, ch in enumerate(chapters):
        ch["number"] = i + 1
        del ch["cid"]

    return {
        "title": title,
        "popularity": popularity,
        "rating_score": rating_score,
        "cover_url": cover_url,
        "chapters": chapters,
        "total_chapters": len(chapters),
    }


# ---------------------------------------------------------------------------
# nonce 求值：页面混淆 JS 的文法极其有限（字符串拼接 + eval 内微小算术），
# 用零依赖的微型求值器处理，无需任何 JS 引擎
# ---------------------------------------------------------------------------


def _split_top_level_plus(expr):
    """按顶层 + 号切分（忽略引号/括号内的 +）"""
    parts, buf = [], []
    depth, quote, esc = 0, None, False
    for ch in expr:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if quote:
            buf.append(ch)
            if ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "+" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _js_str(token):
    """解析 JS 字符串字面量（仅处理常见转义）"""
    body = token[1:-1]
    return (
        body.replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def _eval_inner_arith(expr):
    """求值 eval("...") 内的微小算术表达式。

    混淆模板为固定家族：数字四则/位运算、! 链、document.xxx 取真值、
    字符串的 substring/charCodeAt/length（结果均被当作数字使用）。
    策略：把字符串方法与 document 桩内联替换为数值，再对剩余纯数字运算安全 eval。
    """
    expr = expr.strip()
    # 去平衡外层括号
    while expr.startswith("(") and expr.endswith(")"):
        inner = expr[1:-1]
        if inner.count("(") == inner.count(")"):
            expr = inner.strip()
        else:
            break

    # 'str'.substring(a[, b]) / 'str'.charCodeAt([n]) / 'str'.length 内联为数值
    def _str_method(mm):
        s, method, args = mm.group(2), mm.group(3), mm.group("args").strip()
        try:
            if method == "substring":
                parts = [int(a) for a in args.split(",") if a.strip()]
                return str(int(s[parts[0] : parts[1] if len(parts) > 1 else None]))
            if method == "charCodeAt":
                return str(ord(s[int(args)] if args else s[0]))
        except (ValueError, IndexError):
            pass
        raise ValueError(f"不支持的字符串方法: {mm.group(0)[:40]}")

    expr = re.sub(
        r"""(["'])(.*?)\1\.(?P<method>substring|charCodeAt)\((?P<args>[^)]*)\)""",
        _str_method,
        expr,
        flags=re.S,
    )
    expr = re.sub(r"""(["'])(.*?)\1\.length""", lambda mm: str(len(mm.group(2))), expr, flags=re.S)

    # 三元表达式：数字比较 ? 数字 : 数字（逐轮消除）
    def _ternary(mm):
        a, op, b, t, f = mm.groups()
        cond = eval(f"{a} {op} {b}", {"__builtins__": {}}, {})
        return t if cond else f

    while "?" in expr:
        prev = expr
        expr = re.sub(
            r"([\d.]+)\s*(<=|>=|==|!=|<|>)\s*([\d.]+)\s*\?\s*([\d.]+)\s*:\s*([\d.]+)",
            _ternary,
            expr,
        )
        if expr == prev:
            break

    # parseInt / Math.*（逐轮消除最内层）
    def _func(mm):
        name, inner = mm.group(1), mm.group(2)
        if name == "Math.pow":
            a_s, b_s = [p.strip() for p in inner.split(",", 1)]
            val = float(eval(a_s, {"__builtins__": {}}, {})) ** float(
                eval(b_s, {"__builtins__": {}}, {})
            )
            return _js_num_str(val)
        val = eval(inner, {"__builtins__": {}}, {})
        if name == "parseInt":
            return str(int(val))  # JS parseInt 向零取整
        if name == "Math.floor":
            import math

            return str(math.floor(val))
        if name == "Math.ceil":
            import math

            return str(math.ceil(val))
        if name == "Math.round":
            import math

            return str(math.floor(val + 0.5))  # JS round 半值向上
        if name == "Math.abs":
            return str(abs(val))
        raise ValueError(f"不支持的函数: {name}")

    while re.search(r"(?:parseInt|Math\.\w+)\([^()]*\)", expr):
        prev = expr
        expr = re.sub(r"(parseInt|Math\.\w+)\(([^()]*)\)", _func, expr)
        if expr == prev:
            break

    # 浏览器桩：window/document 的任意属性/方法调用均视为 truthy
    expr = re.sub(r"(?:window|document)\.[A-Za-z]+(\([^)]*\))?", "1", expr)
    # ~x / ~~x 按位取反（JS 先向零截断为 int32；~~ 即截断取整）
    def _tilde(mm):
        v = int(float(mm.group(2)))
        for _ in mm.group(1):
            v = ~v
        return str(v)

    while re.search(r"~+\s*\(?\s*\d", expr):
        prev = expr
        expr = re.sub(r"(~+)\s*\(?\s*(\d+(?:\.\d+)?)\s*\)?", _tilde, expr)
        if expr == prev:
            break
    # 逐轮消除 ! 链（奇偶个 ! 决定结果）
    while re.search(r"!+\s*\d", expr):
        def _bang(mm):
            ops, num = mm.group(1), float(mm.group(2))
            truth = bool(num)
            if len(ops) % 2 == 1:
                truth = not truth
            return "1" if truth else "0"

        expr = re.sub(r"(!+)\s*(\d+(?:\.\d+)?)", _bang, expr)
    # 只允许纯数字四则/位运算（+-*/%&|^<<>> 与 JS 语义及优先级一致），可安全求值
    if not re.fullmatch(r"[\d\s+\-*/%&|^<>().]+", expr):
        raise ValueError(f"不支持的内部表达式: {expr[:50]}")
    return eval(expr, {"__builtins__": {}}, {})


def _js_num_str(val):
    """JS Number.toString()：整数不带小数点"""
    if isinstance(val, str):
        f = float(val)
    else:
        f = float(val)
    return str(int(f)) if f == int(f) else str(f)


def eval_nonce_expr(expr):
    """求值 window["nonce"] = 右侧表达式，形如：
    "f4" + (+eval("1 - !!document.children")).toString() + "81a32" + ...
    """
    expr = expr.strip().rstrip(";")
    if len(expr) > 2000:
        raise ValueError("nonce 表达式过长")
    out = []
    for term in _split_top_level_plus(expr):
        term = term.strip()
        if not term:
            continue
        if len(term) >= 2 and term[0] in ('"', "'") and term[-1] == term[0]:
            out.append(_js_str(term))
            continue
        m = re.fullmatch(
            r"""\(\s*\+\s*eval\(\s*(["'])(.*?)\1\s*\)\s*\)\s*\.toString\(\s*\)""", term, re.S
        )
        if m:
            out.append(_js_num_str(_eval_inner_arith(m.group(2))))
            continue
        raise ValueError(f"不支持的表达式项: {term[:50]}")
    return "".join(out)


def decode_chapter_data(data, nonce):
    """用 nonce 从加密 DATA 中删除干扰字符并 base64 解码为 JSON"""
    chars = list(data)
    segments = re.findall(r"\d+[a-zA-Z]+", nonce)
    for seg in reversed(segments):
        locate = int(re.findall(r"\d+", seg)[0]) & 255
        length = len(re.sub(r"\d+", "", seg))
        del chars[locate : locate + length]
    decoded = base64.b64decode("".join(chars)).decode("utf-8")
    obj = json.loads(decoded)
    if not isinstance(obj.get("picture"), list):
        raise ValueError("解密结果缺少 picture 数组")
    return obj


def get_chapter_images(chapter_url, cookie=None):
    """提取章节图片数据。页面内有多个 window["nonce"] 赋值（含诱饵），
    逐个求值并实际尝试解密，能解出合法 picture 数组的才是正确 nonce。"""
    html = fetch_text(chapter_url, cookie)

    m = re.search(r"<title>《[^》]*》([\s\S]*?)-[\s\S]*?</title>", html)
    chapter_title = m.group(1).strip() if m else None

    m = re.search(r"var DATA = '([^']+)'", html)
    if not m:
        raise ApiError(502, "未找到章节加密数据（页面结构可能已变更）")
    data = m.group(1)

    window_matches = re.findall(r'window\[".+?;', html)
    if not window_matches:
        raise ApiError(502, "未找到 nonce 数据（页面结构可能已变更）")

    # 倒序尝试（真实 nonce 通常在最后一个），求值成功即验证解密
    last_error = None
    for raw in reversed(window_matches):
        rhs = raw.partition("=")[2]
        try:
            nonce = eval_nonce_expr(rhs)
        except Exception as e:
            last_error = e
            continue
        if not re.fullmatch(r"[0-9a-zA-Z]{16,64}", nonce):
            continue
        try:
            return {"chapter_data": decode_chapter_data(data, nonce), "chapter_title": chapter_title}
        except Exception as e:
            last_error = e
            continue

    raise ApiError(502, f"nonce 解析失败: {last_error or '无可用候选'}")


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

SEARCH_PAGE_SIZE = 10


def search_comics(keyword, page):
    from urllib.parse import urlencode

    params = urlencode(
        {"_t": int(time.time() * 1000), "word": keyword, "page": page, "pageSize": SEARCH_PAGE_SIZE, "style": "items"}
    )
    html = fetch_text(f"https://m.ac.qq.com/search/result?{params}", timeout=8, mobile=True)

    results = []
    for m in re.finditer(r'<li class="comic-item">([\s\S]*?)</li>', html):
        item = m.group(1)
        link = re.search(r'href="/comic/index/id/(\d+)"', item)
        if not link:
            continue
        title_m = re.search(r'<strong class="comic-title">([\s\S]*?)</strong>', item)
        cover_m = re.search(r'<img class="cover-image" src="([^"]*)"', item)
        cover_url = cover_m.group(1) if cover_m else None
        if cover_url and cover_url.startswith("//"):
            cover_url = "https:" + cover_url
        results.append(
            {
                "comic_id": link.group(1),
                "title": title_m.group(1).strip() if title_m else "未知标题",
                "cover_url": cover_url,
            }
        )

    # 启发式判断是否有下一页：本页结果不足一页即没有下一页
    return {"results": results, "has_more": len(results) >= SEARCH_PAGE_SIZE}


# ---------------------------------------------------------------------------
# 图片处理管线
# ---------------------------------------------------------------------------

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # 20MB 下载上限
LVGL_MAX_DIM = 2047  # LVGL 头部宽高各占 11 bit

IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://ac.qq.com/",
}


def fetch_image(image_url, retries=1):
    last_exc = None
    for _ in range(retries + 1):
        try:
            resp = requests.get(image_url, headers=IMAGE_HEADERS, timeout=20)
        except requests.RequestException as e:
            last_exc = e
            continue
        if resp.status_code != 200:
            raise ApiError(502, f"图片下载失败: {resp.status_code}")
        declared = int(resp.headers.get("content-length") or 0)
        if declared > MAX_DOWNLOAD_BYTES:
            raise ApiError(413, "图片体积过大")
        data = resp.content
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ApiError(413, "图片体积过大")
        try:
            image = Image.open(BytesIO(data))
            image.load()
        except Exception:
            raise ApiError(502, "图片解码失败")
        return image
    raise ApiError(502, f"图片下载失败: {last_exc}")


def normalize_rgb(image):
    """透明通道铺白底转 RGB"""
    if image.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode in ("RGBA", "LA"):
            background.paste(image, mask=image.split()[-1])
        else:
            background.paste(image)
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def convert_to_lvgl8(image):
    """转换为 LVGL indexed-8 二进制：4 字节头 + 256 项 BGRA 调色板 + 每像素 1 字节索引"""
    image = normalize_rgb(image).quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    w, h = image.size
    if w > LVGL_MAX_DIM or h > LVGL_MAX_DIM:
        raise ApiError(413, "图片尺寸超出 LVGL 编码上限")

    raw_palette = image.getpalette()
    out = BytesIO()
    out.write(struct.pack("<I", 10 | (w << 10) | (h << 21)))
    for i in range(256):
        idx = i * 3
        if raw_palette and idx + 2 < len(raw_palette):
            r, g, b = raw_palette[idx], raw_palette[idx + 1], raw_palette[idx + 2]
        else:
            r = g = b = 0
        out.write(bytes([b, g, r, 0xFF]))
    out.write(image.tobytes())
    return out.getvalue()


def process_image(image, width, quality, if_png, if_lvgl):
    """等比缩放（仅缩小）后输出 LVGL / PNG / JPEG，返回 (bytes, content_type)"""
    if if_lvgl:
        w = min(width, image.width)
        h = round(image.height * w / image.width)
        if h > LVGL_MAX_DIM:  # LVGL 头部宽高仅 11 bit，保证缩放后高度也不超限
            h = LVGL_MAX_DIM
            w = max(1, int(image.width * h / image.height))
        image = normalize_rgb(image).resize((w, h), Image.Resampling.LANCZOS)
        return convert_to_lvgl8(image), "application/octet-stream"

    if image.width > width:
        image = image.resize(
            (width, round(image.height * width / image.width)), Image.Resampling.LANCZOS
        )

    out = BytesIO()
    if if_png:
        colors = max(16, min(256, int(16 + quality * 2.4)))
        image = normalize_rgb(image).quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        image.save(out, "PNG", optimize=True, compress_level=9)
        return out.getvalue(), "image/png"

    normalize_rgb(image).save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue(), "image/jpeg"


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def proxy_image_url(api_url, original_url, page):
    return f"{api_url}/image/proxy?url={quote(original_url, safe='')}&page={page}"


def get_comic_info_cached(comic_id, cookie):
    """详情缓存：带 Cookie 的请求不读不写"""
    if cookie:
        return get_comic_info(comic_id, cookie)
    info = info_cache.get(comic_id)
    if info is None:
        info = get_comic_info(comic_id, None)
        info_cache.set(comic_id, info)
    return info


@app.get("/")
def read_root():
    return "it works!"


@app.get("/config")
@app.get("/config/")
def config():
    api_url = get_api_url()
    return jsonify(
        {
            "QQComic": {
                "name": "腾讯动漫",
                "apiUrl": api_url,
                "detailPath": "/comic/<id>",
                "photoPath": "/photo/<id>/chapter/<chapter>",
                "searchPath": "/search/<text>/<page>",
                "type": "qqcomic",
            }
        }
    )


@app.get("/comic/<comic_id>")
@app.get("/comic/<comic_id>/")
def comic_info(comic_id):
    if not comic_id.isdigit():
        raise ApiError(400, "无效的漫画ID")
    cookie = request.headers.get("Cookie") or None
    info = get_comic_info_cached(comic_id, cookie)
    if info["total_chapters"] == 0:
        raise ApiError(404, "Comic not found")

    api_url = get_api_url()
    try:
        rate = float(info["rating_score"])
    except (TypeError, ValueError):
        rate = 0

    return jsonify(
        {
            "item_id": int(comic_id),
            "name": info["title"],
            "page_count": 0,
            "views": info["popularity"],
            "rate": rate,
            "cover": proxy_image_url(api_url, info["cover_url"], "cover") if info["cover_url"] else "",
            "tags": [],
            "total_chapters": info["total_chapters"],
        }
    )


@app.get("/photo/<comic_id>/chapter/<int:chapter_number>")
def chapter_images(comic_id, chapter_number):
    if not comic_id.isdigit():
        raise ApiError(400, "无效的漫画ID")
    if chapter_number < 1:
        raise ApiError(400, "章节号必须从 1 开始")

    cookie = request.headers.get("Cookie") or None
    info = get_comic_info_cached(comic_id, cookie)

    # 章节列表已按正序重新编号 1..N，直接按下标取
    if chapter_number > len(info["chapters"]):
        raise ApiError(404, f"未找到第 {chapter_number} 章")
    target = info["chapters"][chapter_number - 1]

    data = None if cookie else chapter_cache.get(target["link"])
    if data is None:
        data = get_chapter_images(target["link"], cookie)
        if not cookie:
            chapter_cache.set(target["link"], data)

    api_url = get_api_url()
    pictures = data["chapter_data"].get("picture") or []
    images = [
        {"url": proxy_image_url(api_url, pic["url"], i + 1)}
        for i, pic in enumerate(pictures)
        if pic.get("url")
    ]

    return jsonify({"title": data["chapter_title"] or target["title"], "images": images})


@app.get("/search/<value>")
@app.get("/search/<value>/")
@app.get("/search/<value>/<page>")
def search(value, page="1"):
    # Flask 已对路径参数做过一次 URL 解码，直接使用
    if not page.isdigit() or int(page) < 1:
        raise ApiError(400, "页码必须大于0")
    page = int(page)
    if not value:
        raise ApiError(400, "缺少搜索关键词")

    result = search_comics(value, page)
    api_url = get_api_url()

    return jsonify(
        {
            "page": page,
            "has_more": result["has_more"],
            "results": [
                {
                    "comic_id": int(r["comic_id"]),
                    "title": r["title"],
                    "cover_url": proxy_image_url(api_url, r["cover_url"], "cover") if r["cover_url"] else "",
                    "pages": 0,
                }
                for r in result["results"]
            ],
        }
    )


@app.get("/image/proxy")
def image_proxy():
    image_url = request.args.get("url")
    if not image_url:
        raise ApiError(400, "缺少url参数")
    if not is_allowed_image_url(image_url):
        raise ApiError(403, "不允许的图片地址")

    width = clamp_int(request.args.get("width") or request.args.get("w"), 600, 1, 2047, "width")
    quality = clamp_int(request.args.get("quality") or request.args.get("q"), 50, 1, 100, "quality")

    image = fetch_image(image_url)
    data, content_type = process_image(
        image,
        width,
        quality,
        if_png=is_truthy_arg("ifPNG"),
        if_lvgl=is_truthy_arg("ifLVGL"),
    )
    return Response(
        data,
        mimetype=content_type,
        headers={"Cache-Control": "public, max-age=86400", "Content-Type": content_type},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
