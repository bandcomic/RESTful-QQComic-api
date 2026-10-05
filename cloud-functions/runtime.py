"""Portable cache/pipeline; all state is bounded and credential scoped."""
import contextlib, hashlib, io, json, os, sqlite3, struct, threading, time, uuid
from collections import OrderedDict
from functools import wraps
import requests as network
from flask import Flask, Response, g, has_request_context, jsonify, request
from PIL import Image

class ApiError(Exception):
    def __init__(self,status,message,retry_after=2):
        super().__init__(message)
        self.status,self.message,self.retry_after=status,message,retry_after

def budget():
    left=getattr(g,"deadline",time.monotonic()+16)-time.monotonic() if has_request_context() else 16
    if left<=0: raise ApiError(503,"请求预算已耗尽")
    return left

def public_url():
    if os.environ.get("PUBLIC_URL"): return os.environ["PUBLIC_URL"].rstrip("/")
    host=(request.headers.get("Eo-Pages-Host") or request.headers.get("X-Forwarded-Host") or request.host).split(",")[0].strip()
    proto="https" if request.headers.get("Eo-Pages-Host") else (request.headers.get("X-Forwarded-Proto") or request.scheme).split(",")[0].strip()
    return f"{proto}://{host}"+os.environ.get("PATH_PREFIX",request.script_root).rstrip("/")

def scope():
    return hashlib.sha256((request.headers.get("Cookie","")+"\0"+request.headers.get("Authorization","")).encode()).hexdigest() if has_request_context() else "anonymous"

class Cache:
    def __init__(self):
        self.guard,self.items,self.leases,self.size=threading.RLock(),OrderedDict(),{},0
        self.path,self.redis=os.environ.get("CACHE_DB",""),None
        if os.environ.get("REDIS_URL"):
            import redis
            self.redis=redis.Redis.from_url(os.environ["REDIS_URL"],socket_timeout=.5,socket_connect_timeout=.5)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)),exist_ok=True)
            deadline=time.monotonic()+3
            while True:
                try:
                    with self.db() as db:
                        db.execute("PRAGMA journal_mode=WAL")
                        db.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY,v BLOB,expires REAL,size INT,touched REAL)")
                        db.execute("CREATE TABLE IF NOT EXISTS lease(k TEXT PRIMARY KEY,owner TEXT,expires REAL)")
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower() or time.monotonic()>=deadline: raise
                    time.sleep(.05)
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=.5)
        try:
            with db: yield db
        finally: db.close()
    def key(self,value):
        return os.environ.get("CACHE_NAMESPACE","qqcomic")+":"+hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    def get(self,key):
        with self.guard:
            item=self.items.pop(key,None)
            if item and item[1]>time.time(): self.items[key]=item; return item[0]
            if item: self.size-=len(item[0])
        try:
            if self.path:
                with self.db() as db: row=db.execute("SELECT v FROM cache WHERE k=? AND expires>?",(key,time.time())).fetchone()
                if row: return row[0]
            if self.redis: return self.redis.get(key)
        except Exception: pass
        return None
    def put(self,key,body,ttl):
        now=time.time(); limit=int(os.environ.get("MEMORY_CACHE_BYTES",67108864))
        with self.guard:
            old=self.items.pop(key,None)
            if old: self.size-=len(old[0])
            if len(body)<=limit: self.items[key]=(body,now+ttl); self.size+=len(body)
            while self.size>limit: self.size-=len(self.items.popitem(last=False)[1][0])
        try:
            if self.path and len(body)<=int(os.environ.get("DISK_CACHE_BYTES",536870912)):
                with self.db() as db:
                    db.execute("DELETE FROM cache WHERE expires<=?",(now,))
                    db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?)",(key,body,now+ttl,len(body),now))
                    size=db.execute("SELECT COALESCE(SUM(size),0) FROM cache").fetchone()[0]
                    for victim,n in db.execute("SELECT k,size FROM cache ORDER BY touched").fetchall():
                        if size<=int(os.environ.get("DISK_CACHE_BYTES",536870912)): break
                        db.execute("DELETE FROM cache WHERE k=?",(victim,)); size-=n
            if self.redis: self.redis.set(key,body,ex=max(1,int(ttl)))
        except Exception: pass
    def claim(self,key,owner):
        try:
            if self.redis: return self.redis.set("lease:"+key,owner,nx=True,ex=90)
            if self.path:
                with self.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("DELETE FROM lease WHERE expires<=?",(time.time(),))
                    return db.execute("INSERT OR IGNORE INTO lease VALUES(?,?,?)",(key,owner,time.time()+90)).rowcount==1
            with self.guard:
                if key in self.leases and self.leases[key][1]>time.time(): return False
                self.leases[key]=(owner,time.time()+90); return True
        except Exception as e: raise ApiError(503,"共享协调不可用") from e
    def release(self,key,owner):
        try:
            if self.redis: self.redis.eval("if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) end return 0",1,"lease:"+key,owner)
            elif self.path:
                with self.db() as db: db.execute("DELETE FROM lease WHERE k=? AND owner=?",(key,owner))
            else:
                with self.guard:
                    if self.leases.get(key,(None,))[0]==owner: self.leases.pop(key,None)
        except Exception: pass
    def load(self,key,loader,ttl):
        owner,start=uuid.uuid4().hex,time.monotonic()
        while True:
            body=self.get(key)
            if body is not None: return body,True
            if self.claim(key,owner): break
            if time.monotonic()-start>min(8,budget()): raise ApiError(503,"相同请求正在处理")
            time.sleep(.04)
        try:
            body=self.get(key)
            if body is not None: return body,True
            body=loader(); self.put(key,body,ttl); return body,False
        finally: self.release(key,owner)

cache=Cache(); local=threading.local(); cold=threading.BoundedSemaphore(int(os.environ.get("IMAGE_CONCURRENCY","1")))
class Http:
    RequestException=network.RequestException
    def request(self,method,url,**kwargs):
        if not hasattr(local,"session"): local.session=network.Session()
        local.session.cookies.clear(); kwargs.pop("timeout",None); kwargs.pop("stream",None)
        response=local.session.request(method,url,stream=True,timeout=(min(3,budget()),min(4,budget())),**kwargs)
        try:
            body=bytearray(); limit=int(os.environ.get("MAX_DOWNLOAD_BYTES",20971520))
            for chunk in response.iter_content(65536):
                budget()
                if len(body)+len(chunk)>limit: raise ApiError(413,"下载超过字节预算")
                body.extend(chunk)
            response._content,response._content_consumed=bytes(body),True
            return response
        finally: response.close()
    def get(self,url,**kwargs): return self.request("GET",url,**kwargs)
    def post(self,url,**kwargs): return self.request("POST",url,**kwargs)
http=Http()
def params(default=600):
    def number(name,alias,fallback,maximum):
        raw=request.args.get(name,request.args.get(alias,str(fallback)))
        if not raw or not raw.isascii() or not raw.isdecimal() or not 1<=int(raw)<=maximum: raise ApiError(400,"无效 "+name)
        return int(raw)
    def flag(name):
        value=request.args.get(name,"0").lower()
        if value not in ("0","false","no","off","1","true","yes","on"): raise ApiError(400,"无效格式参数")
        return value in ("1","true","yes","on")
    lvgl,png=flag("ifLVGL"),flag("ifPNG")
    return number("width","w",default,2047),number("quality","q",50,100),"lvgl" if lvgl else "png" if png else "jpeg"
def process(image,options):
    width,quality,fmt=options; ow,oh=image.size
    if ow*oh>int(os.environ.get("MAX_IMAGE_PIXELS",32000000)): raise ApiError(413,"超过像素预算")
    with image.convert("RGBA") as rgba,Image.new("RGB",image.size,"white") as rgb:
        with rgba.getchannel("A") as alpha: rgb.paste(rgba,mask=alpha)
        ratio=min(1,width/ow,(2047 if fmt=="lvgl" else 8192)/oh); size=(max(1,int(ow*ratio)),max(1,int(oh*ratio)))
        with rgb.resize(size,Image.Resampling.LANCZOS) as resized:
            out=io.BytesIO()
            if fmt=="lvgl":
                with resized.quantize(colors=256) as indexed:
                    w,h=size; out.write(struct.pack("<I",10|w<<10|h<<21)); palette=indexed.getpalette() or []
                    for i in range(256):
                        r,green,b=palette[i*3:i*3+3] if i*3+2<len(palette) else (0,0,0)
                        out.write(bytes((b,green,r,255)))
                    out.write(indexed.tobytes())
            elif fmt=="png":
                with resized.quantize(colors=max(16,min(256,int(16+quality*2.4)))) as indexed: indexed.save(out,"PNG",compress_level=9)
            else: resized.save(out,"JPEG",quality=quality,optimize=True)
    body=out.getvalue()
    if len(body)>int(os.environ.get("MAX_OUTPUT_BYTES",4194304)): raise ApiError(413,"成品超过响应预算")
    return body,{"type":{"jpeg":"image/jpeg","png":"image/png","lvgl":"application/octet-stream"}[fmt],"original":f"{ow}x{oh}","actual":f"{size[0]}x{size[1]}"}
def serve(value,loader,default=600,ttl=86400):
    options=params(default); key=cache.key(["indexed8-v2",value,scope(),options])
    def generate():
        if not cold.acquire(timeout=min(1,budget())): raise ApiError(503,"图片处理繁忙")
        owner,slot=uuid.uuid4().hex,None
        try:
            for i in range(int(os.environ.get("IMAGE_GLOBAL_CONCURRENCY","2"))):
                candidate=cache.key(["slot",i])
                if cache.claim(candidate,owner): slot=candidate; break
            if slot is None: raise ApiError(503,"图片处理繁忙")
            source=loader()
            with (Image.open(io.BytesIO(source)) if isinstance(source,bytes) else source) as image: body,info=process(image,options)
            header=json.dumps(info).encode(); return struct.pack("<I",len(header))+header+body
        finally:
            if slot: cache.release(slot,owner)
            cold.release()
    packed,hit=cache.load(key,generate,ttl); n=struct.unpack("<I",packed[:4])[0]; info=json.loads(packed[4:4+n]); body=packed[4+n:]
    etag=f'"{hashlib.sha256(body).hexdigest()[:32]}"'; if_none_match=request.headers.get("If-None-Match")
    if if_none_match and etag in [tag.strip() for tag in if_none_match.split(",")]:
        res=Response(status=304); res.headers["ETag"]=etag; res.headers["Cache-Control"]="public, max-age=86400"; res.headers["CDN-Cache-Control"]="public, max-age=86400"; return res
    headers={"Content-Length":str(len(body)),"ETag":etag,"X-Cache":"HIT" if hit else "MISS","X-Image-Original-Size":info["original"],"X-Image-Actual-Size":info["actual"]}
    return Response(body,content_type=info["type"],headers=headers)
def memoize(ttl=300,key=None):
    def decorate(function):
        @wraps(function)
        def wrapped(*args,**kwargs):
            value=key(*args,**kwargs) if key else [args,kwargs]
            body,_=cache.load(cache.key([function.__name__,value,scope()]),lambda:json.dumps(function(*args,**kwargs),ensure_ascii=False).encode(),ttl)
            return json.loads(body)
        return wrapped
    return decorate
def _flask(name): return Flask(name)
def create_app(name):
    app=_flask(name); app.json.ensure_ascii=False; Image.MAX_IMAGE_PIXELS=int(os.environ.get("MAX_IMAGE_PIXELS",32000000))
    original_wsgi=app.wsgi_app
    def path_fix(environ,start_response):
        for key in ("PATH_INFO","SCRIPT_NAME","QUERY_STRING"):
            value=environ.get(key,"")
            try: value.encode("latin1")
            except UnicodeEncodeError: environ[key]=value.encode("utf8").decode("latin1")
        return original_wsgi(environ,start_response)
    app.wsgi_app=path_fix
    @app.before_request
    def begin(): g.deadline=time.monotonic()+int(os.environ.get("REQUEST_BUDGET_SECONDS",16))
    @app.after_request
    def finish(response):
        policy="private, no-store" if request.headers.get("Cookie") or request.headers.get("Authorization") else "no-store" if response.status_code>=400 else "public, max-age=86400" if response.mimetype in ("image/jpeg","image/png","application/octet-stream") else "private, no-cache"
        response.headers["Cache-Control"]=policy; response.headers["CDN-Cache-Control"]=policy
        if response.status_code>=400 and response.is_json:
            data=response.get_json(silent=True) or {}
            response.set_data(json.dumps({"code":response.status_code,"message":data.get("message") or data.get("error") or "请求失败"},ensure_ascii=False).encode())
        if response.status_code in (429,503): response.headers.setdefault("Retry-After","2")
        return response
    @app.errorhandler(ApiError)
    def error(e): return jsonify(code=e.status,message=e.message),e.status,{"Retry-After":str(e.retry_after)}
    @app.get("/health/runtime")
    def runtime_health(): return jsonify(status="ok",processor="indexed8-v2",memory_bytes=cache.size,cache="sqlite" if cache.path else "memory",coordination="redis" if cache.redis else "sqlite" if cache.path else "process")
    @app.errorhandler(Exception)
    def unexpected(e):
        status=getattr(e,"status",getattr(e,"code",502))
        return jsonify(code=status,message="上游请求或解析失败"),status
    return app
