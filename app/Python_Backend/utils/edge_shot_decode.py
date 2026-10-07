# -*- coding: utf-8 -*-
"""K230 现场图反解器：把板端"错打包"的 JPEG 还原回正常彩色图。

背景（2026-10-02 定案，别删）：
    板端抓拍 = `snapshot(chn=CHN0)`（YUV420SP）直接 `to_jpeg()`。这块固件的软件
    编码器拿到 YUV420SP **不报错**，却把整块 buffer 当 **RGB565** 逐像素打包：
        640x480 RGB565 需要 614400 字节，NV12 buffer 只有 460800
        ⇒ 图的上 50% = Y 平面、随后 25% = UV 交错、底部 25% = 越界读。
    板端没有任何能正确编码 CHN0 的 API：
        `save` → OSError: current format not support save function!
        `to_jpeg` → 上述错打包（元凶）
        `compress` / `to_rgb888` → 板子掉电（P9/P10 实测）
    所以只能在服务端把这笔"确定性的错"反解回来，板子一个字不用改。

原理：
    JPEG 里存的是"把 NV12 字节流当 RGB565 读出来的画面"。反解 = 把画面量化回
    RGB565 → 还原 16 位值 → 拆回小端字节流（= 原始 NV12 buffer）→ YUV 转 RGB。
    JPEG 压缩不可避免地损伤了位段（Y 字节被拆进 R/G/B 三个通道，其中 5/8 位
    落在被 4:2:0 半分辨率采样的色度上），所以结果"结构对、颜色对、亮度有
    2x2 块状噪点"——看叶斑足够，不追求画质。

用法（命令行自检）：
    python edge_shot_decode.py <in.jpg> <out.jpg> [uv|vu]
"""
import io
import sys


def decode_edge_jpeg(raw, quality=85, uv_order='UV'):
    """把板端错打包的 JPEG 反解回正常彩色 JPEG。

    :param raw: 板端传来的原始 JPEG 字节（花屏）
    :param quality: 输出 JPEG 质量
    :param uv_order: 'UV' = NV12（U 在前，K230 CHN0 的口径）；'VU' = NV21
    :returns: 修正后的 JPEG bytes；任何一步失败返回 None（调用方应回退存原图）
    """
    try:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert('RGB')
        w, h = img.size
        if w < 2 or h < 2 or w % 2 or h % 2:
            return None

        # ① 每像素量化回 RGB565（模拟固件的错打包）
        px = np.asarray(img, dtype=np.uint16)              # (h, w, 3)
        r, g, b = px[..., 0], px[..., 1], px[..., 2]
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

        # ② 16 位值按小端展开成字节流 —— 这就是固件当年"读出来编码"的那块 buffer
        stream = v.astype('<u2').tobytes()

        # ③ 只取真实 NV12 buffer 的长度（w*h*1.5），后面那截是固件越界读的垃圾
        buf = np.frombuffer(stream[: w * h * 3 // 2], dtype=np.uint8)

        # ④ 拆 Y 平面 + UV 交错平面
        Y = buf[: w * h].reshape(h, w).astype(np.float32)
        uv = buf[w * h:].reshape(h // 2, w).astype(np.float32)
        U, V = (uv[:, 0::2], uv[:, 1::2]) if uv_order == 'UV' else (uv[:, 1::2], uv[:, 0::2])

        # ⑤ UV 上采样 x2（2x2 共享一组色度）+ BT.601 YUV -> RGB
        U = np.repeat(np.repeat(U, 2, axis=0), 2, axis=1)
        V = np.repeat(np.repeat(V, 2, axis=0), 2, axis=1)
        R = Y + 1.402 * (V - 128.0)
        G = Y - 0.344136 * (U - 128.0) - 0.714136 * (V - 128.0)
        B = Y + 1.772 * (U - 128.0)
        rgb = np.stack([R, G, B], axis=-1)
        np.clip(rgb, 0.0, 255.0, out=rgb)

        out = Image.fromarray(rgb.astype(np.uint8), 'RGB')
        _io = io.BytesIO()
        out.save(_io, 'JPEG', quality=quality)
        return _io.getvalue()
    except Exception:
        return None


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法: python edge_shot_decode.py <in.jpg> <out.jpg> [uv|vu]')
        sys.exit(1)
    order = sys.argv[3] if len(sys.argv) > 3 else 'uv'
    with open(sys.argv[1], 'rb') as f:
        raw = f.read()
    out = decode_edge_jpeg(raw, uv_order=order.upper())
    if out is None:
        print('[X] 反解失败')
        sys.exit(2)
    with open(sys.argv[2], 'wb') as f:
        f.write(out)
    print('[OK] %s -> %s (%d 字节, uv=%s)' % (sys.argv[1], sys.argv[2], len(out), order))
