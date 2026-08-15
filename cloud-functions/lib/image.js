// 图片处理管线：下载 → 缩放 → JPEG / PNG 量化 / LVGL 预解码
// 纯 JS 实现（vendored jpeg-js + UPNG.js + 手写双线性缩放），
// 全链路零 node: 内置模块 / Buffer / process 引用，兼容 EdgeOne 打包器
import jpegDecode from './vendor/jpeg-decoder.js'
import jpegEncode from './vendor/jpeg-encoder.js'
import UPNG from './vendor/upng.js'
import { HttpError } from './util.js'

const IMAGE_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  Referer: 'https://ac.qq.com/',
}

const MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024 // 20MB 下载上限
const MAX_PIXELS = 40_000_000 // 防解压炸弹
const LVGL_MAX_DIM = 2047 // LVGL 头部宽高各占 11 bit

/** 下载图片为 Buffer（带大小限制） */
export async function fetchImageBuffer(imageUrl) {
  let resp
  try {
    resp = await fetch(imageUrl, {
      headers: IMAGE_HEADERS,
      signal: AbortSignal.timeout(20000),
    })
  } catch (e) {
    throw new HttpError(502, `图片下载失败: ${e.message}`)
  }
  if (resp.status !== 200) {
    throw new HttpError(502, `图片下载失败: ${resp.status}`)
  }
  const declared = Number(resp.headers.get('content-length') || 0)
  if (declared > MAX_DOWNLOAD_BYTES) throw new HttpError(413, '图片体积过大')
  const buf = new Uint8Array(await resp.arrayBuffer())
  if (buf.length > MAX_DOWNLOAD_BYTES) throw new HttpError(413, '图片体积过大')
  return buf
}

/** 按 magic bytes 解码为 RGBA 位图 { data: Uint8Array, width, height } */
function decodeImage(buf) {
  try {
    if (buf[0] === 0xff && buf[1] === 0xd8) {
      const raw = jpegDecode(buf, { maxMemoryUsageInMB: 512 })
      return { data: raw.data, width: raw.width, height: raw.height }
    }
    if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) {
      const rgbaBuf = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
      const png = UPNG.decode(rgbaBuf)
      const rgba = UPNG.toRGBA8(png)[0]
      return { data: new Uint8Array(rgba), width: png.width, height: png.height }
    }
  } catch {
    // fallthrough
  }
  throw new HttpError(502, '图片解码失败（仅支持 JPEG/PNG）')
}

/** RGBA 原位 alpha 混合到白底 */
function flattenAlpha(data) {
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3]
    if (a !== 255) {
      const inv = 255 - a
      data[i] = (data[i] * a + 255 * inv) / 255
      data[i + 1] = (data[i + 1] * a + 255 * inv) / 255
      data[i + 2] = (data[i + 2] * a + 255 * inv) / 255
      data[i + 3] = 255
    }
  }
}

/** RGBA 双线性缩放（仅用于缩小），返回新 Uint8Array */
function resizeBilinear(src, sw, sh, dw, dh) {
  const dst = new Uint8Array(dw * dh * 4)
  const xRatio = sw / dw
  const yRatio = sh / dh
  for (let y = 0; y < dh; y++) {
    const fy = (y + 0.5) * yRatio - 0.5
    const y0 = Math.max(0, Math.floor(fy))
    const y1 = Math.min(sh - 1, y0 + 1)
    const wy = fy - y0
    for (let x = 0; x < dw; x++) {
      const fx = (x + 0.5) * xRatio - 0.5
      const x0 = Math.max(0, Math.floor(fx))
      const x1 = Math.min(sw - 1, x0 + 1)
      const wx = fx - x0
      const i00 = (y0 * sw + x0) * 4
      const i01 = (y0 * sw + x1) * 4
      const i10 = (y1 * sw + x0) * 4
      const i11 = (y1 * sw + x1) * 4
      const di = (y * dw + x) * 4
      for (let c = 0; c < 4; c++) {
        const top = src[i00 + c] * (1 - wx) + src[i01 + c] * wx
        const bottom = src[i10 + c] * (1 - wx) + src[i11 + c] * wx
        dst[di + c] = top * (1 - wy) + bottom * wy
      }
    }
  }
  return dst
}

/**
 * 处理图片：等比缩放（仅缩小）后按参数输出 JPEG / PNG / LVGL 二进制。
 * 返回 { data: Buffer, contentType: string }
 */
export async function processImage(input, { width, quality, ifPNG, ifLVGL }) {
  const image = decodeImage(input)
  if (image.width * image.height > MAX_PIXELS) {
    throw new HttpError(413, '图片尺寸过大')
  }

  // 透明通道铺白底
  flattenAlpha(image.data)

  let w = image.width
  let h = image.height
  let pixels = image.data

  if (ifLVGL) {
    // LVGL 头部宽高仅 11 bit（≤2047），需保证缩放后高度也不超限
    let tw = Math.min(width, w)
    let th = Math.round(h * (tw / w))
    if (th > LVGL_MAX_DIM) {
      th = LVGL_MAX_DIM
      tw = Math.max(1, Math.floor(w * (th / h)))
    }
    if (tw !== w) {
      pixels = resizeBilinear(pixels, w, h, tw, th)
      w = tw
      h = th
    }
    return { data: packLvgl8(pixels, w, h), contentType: 'application/octet-stream' }
  }

  if (w > width) {
    const th = Math.round(h * (width / w))
    pixels = resizeBilinear(pixels, w, h, width, th)
    w = width
    h = th
  }

  if (ifPNG) {
    const colors = Math.max(16, Math.min(256, Math.trunc(16 + quality * 2.4)))
    const rgba = pixels.buffer.slice(pixels.byteOffset, pixels.byteOffset + pixels.byteLength)
    return {
      data: new Uint8Array(UPNG.encode([rgba], w, h, colors)),
      contentType: 'image/png',
    }
  }

  const encoded = jpegEncode({ data: pixels, width: w, height: h }, quality)
  return { data: encoded.data, contentType: 'image/jpeg' }
}

// ---------------------------------------------------------------------------
// LVGL indexed-8 二进制：4 字节头 + 256 项 BGRA 调色板 + 每像素 1 字节索引
// 量化算法：RGB555 直方图 + median-cut 到 256 色（零依赖，替代 PIL MEDIANCUT）
// ---------------------------------------------------------------------------

function packLvgl8(rgbaPixels, width, height) {
  const { palette, indices } = quantizeMedianCut(rgbaPixels, width * height)

  const out = new Uint8Array(4 + 256 * 4 + width * height)
  const header = (10 | (width << 10) | (height << 21)) >>> 0
  out[0] = header & 0xff
  out[1] = (header >> 8) & 0xff
  out[2] = (header >> 16) & 0xff
  out[3] = (header >> 24) & 0xff

  let offset = 4
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = palette[i] || [0, 0, 0]
    out[offset++] = b
    out[offset++] = g
    out[offset++] = r
    out[offset++] = 0xff
  }
  out.set(indices, offset)
  return out
}

function quantizeMedianCut(rgbaPixels, pixelCount) {
  // 1. RGB555 直方图（alpha 已铺白底，直接丢弃）
  const hist = new Uint32Array(32768)
  for (let i = 0; i < rgbaPixels.length; i += 4) {
    hist[
      ((rgbaPixels[i] >> 3) << 10) | ((rgbaPixels[i + 1] >> 3) << 5) | (rgbaPixels[i + 2] >> 3)
    ]++
  }
  const allBins = []
  for (let b = 0; b < 32768; b++) {
    if (hist[b]) allBins.push(b)
  }

  // 2. median-cut 分盒
  let boxes = [allBins]
  while (boxes.length < 256) {
    // 选像素最多且可分的盒子
    let target = -1
    let targetCount = -1
    for (let i = 0; i < boxes.length; i++) {
      if (boxes[i].length < 2) continue
      let count = 0
      for (const b of boxes[i]) count += hist[b]
      if (count > targetCount) {
        targetCount = count
        target = i
      }
    }
    if (target < 0) break

    const box = boxes[target]
    // 找范围最大的通道：0=r 1=g 2=b
    let mins = [31, 31, 31]
    let maxs = [0, 0, 0]
    for (const b of box) {
      const v = [(b >> 10) & 31, (b >> 5) & 31, b & 31]
      for (let ch = 0; ch < 3; ch++) {
        if (v[ch] < mins[ch]) mins[ch] = v[ch]
        if (v[ch] > maxs[ch]) maxs[ch] = v[ch]
      }
    }
    const ranges = [maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]]
    const channel = ranges.indexOf(Math.max(...ranges))
    if (ranges[channel] === 0) break

    const shift = channel === 0 ? 10 : channel === 1 ? 5 : 0
    box.sort((a, b) => ((a >> shift) & 31) - ((b >> shift) & 31))

    const half = targetCount / 2
    let acc = 0
    let splitAt = 1
    for (let i = 0; i < box.length; i++) {
      acc += hist[box[i]]
      if (acc >= half) {
        splitAt = i + 1
        break
      }
    }
    // clamp 到 [1, length-1]，保证两个子盒都非空，盒子数严格递增（防死循环）
    splitAt = Math.max(1, Math.min(box.length - 1, splitAt))
    boxes.splice(target, 1, box.slice(0, splitAt), box.slice(splitAt))
    boxes = boxes.filter((b) => b.length > 0)
  }

  // 3. 每个盒子的加权平均色作为调色板颜色
  const palette = boxes.map((box) => {
    let r = 0
    let g = 0
    let b = 0
    let total = 0
    for (const bin of box) {
      const count = hist[bin]
      total += count
      r += (((bin >> 10) & 31) << 3) * count
      g += (((bin >> 5) & 31) << 3) * count
      b += ((bin & 31) << 3) * count
    }
    if (total === 0) return [0, 0, 0]
    return [Math.round(r / total), Math.round(g / total), Math.round(b / total)]
  })

  // 4. 每个非空 bin 映射到最近调色板颜色
  const binToIndex = new Uint8Array(32768)
  for (const bin of allBins) {
    const r = ((bin >> 10) & 31) << 3
    const g = ((bin >> 5) & 31) << 3
    const b = (bin & 31) << 3
    let best = 0
    let bestDist = Infinity
    for (let i = 0; i < palette.length; i++) {
      const dr = r - palette[i][0]
      const dg = g - palette[i][1]
      const db = b - palette[i][2]
      const dist = dr * dr + dg * dg + db * db
      if (dist < bestDist) {
        bestDist = dist
        best = i
      }
    }
    binToIndex[bin] = best
  }

  // 5. 逐像素查表输出索引
  const indices = new Uint8Array(pixelCount)
  for (let p = 0, i = 0; p < pixelCount; p++, i += 4) {
    indices[p] =
      binToIndex[
        ((rgbaPixels[i] >> 3) << 10) | ((rgbaPixels[i + 1] >> 3) << 5) | (rgbaPixels[i + 2] >> 3)
      ]
  }

  return { palette, indices }
}
