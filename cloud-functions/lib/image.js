// 图片处理管线：下载 → 缩放 → JPEG / PNG 量化 / LVGL 预解码
import sharp from 'sharp'
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
  const buf = Buffer.from(await resp.arrayBuffer())
  if (buf.length > MAX_DOWNLOAD_BYTES) throw new HttpError(413, '图片体积过大')
  return buf
}

/**
 * 处理图片：等比缩放（仅缩小）后按参数输出 JPEG / PNG / LVGL 二进制。
 * 返回 { data: Buffer, contentType: string }
 */
export async function processImage(input, { width, quality, ifPNG, ifLVGL }) {
  const base = sharp(input, { limitInputPixels: MAX_PIXELS }).flatten({
    background: { r: 255, g: 255, b: 255 },
  })
  const meta = await base.metadata()
  const origWidth = meta.width || width

  if (ifLVGL) {
    // LVGL 头部宽高仅 11 bit（≤2047），需保证缩放后高度也不超限
    let w = Math.min(width, origWidth)
    let h = Math.round((meta.height || w) * (w / origWidth))
    if (h > LVGL_MAX_DIM) {
      h = LVGL_MAX_DIM
      w = Math.max(1, Math.floor(origWidth * (h / (meta.height || h))))
    }
    const { data, info } = await base
      .resize({ width: w, height: h, fit: 'fill' })
      .raw()
      .toBuffer({ resolveWithObject: true })
    return { data: packLvgl8(data, info.width, info.height), contentType: 'application/octet-stream' }
  }

  let pipeline = base
  if (origWidth > width) {
    pipeline = pipeline.resize({ width, withoutEnlargement: true })
  }

  if (ifPNG) {
    const colors = Math.max(16, Math.min(256, Math.trunc(16 + quality * 2.4)))
    const data = await pipeline
      .png({ palette: true, colors, quality, compressionLevel: 9 })
      .toBuffer()
    return { data, contentType: 'image/png' }
  }

  const data = await pipeline.jpeg({ quality, mozjpeg: true }).toBuffer()
  return { data, contentType: 'image/jpeg' }
}

// ---------------------------------------------------------------------------
// LVGL indexed-8 二进制：4 字节头 + 256 项 BGRA 调色板 + 每像素 1 字节索引
// 量化算法：RGB555 直方图 + median-cut 到 256 色（零依赖，替代 PIL MEDIANCUT）
// ---------------------------------------------------------------------------

function packLvgl8(rgbPixels, width, height) {
  const { palette, indices } = quantizeMedianCut(rgbPixels, width * height)

  const out = Buffer.alloc(4 + 256 * 4 + width * height)
  out.writeUInt32LE((10 | (width << 10) | (height << 21)) >>> 0, 0)

  let offset = 4
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = palette[i] || [0, 0, 0]
    out[offset++] = b
    out[offset++] = g
    out[offset++] = r
    out[offset++] = 0xff
  }
  Buffer.from(indices).copy(out, offset)
  return out
}

function quantizeMedianCut(pixels, pixelCount) {
  // 1. RGB555 直方图
  const hist = new Uint32Array(32768)
  for (let i = 0; i < pixels.length; i += 3) {
    hist[((pixels[i] >> 3) << 10) | ((pixels[i + 1] >> 3) << 5) | (pixels[i + 2] >> 3)]++
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
  for (let p = 0, i = 0; p < pixelCount; p++, i += 3) {
    indices[p] =
      binToIndex[((pixels[i] >> 3) << 10) | ((pixels[i + 1] >> 3) << 5) | (pixels[i + 2] >> 3)]
  }

  return { palette, indices }
}
