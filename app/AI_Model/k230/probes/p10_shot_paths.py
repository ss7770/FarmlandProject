# -*- coding: utf-8 -*-
# 探针 P10：抓拍转换路径逐变体二分
#
# ================= 纯图像：不联网 / 不推理 / 不写库 =================
#
# 【为什么要跑】
#   P9 实测：跑完 66s 推理后，结论 POST 正常（0.08s 连上、309B 发出），
#   snapshot(CHN0) 正常（640x480），**然后死在转图那一步**（无 Python 回溯、板子直接掉电）：
#       [shot] ② to_rgb 缩放 0.50 中…（mem_free=3847328）   ← 之后什么都没了
#   对应代码：k230_classify.py:988 → _to_rgb_scaled(img, 0.5) → img.to_rgb888(x_scale=0.5, y_scale=0.5)
#
# 【官方源码怎么写的】（PipeLine.py / AIBase.py / Ai2d.py / Utils.py 都读过了）
#   · 缩放一律走**硬件 2D 引擎** Ai2d，从不碰 image 模块的缩放参数；
#   · image 模块上官方只出现过**无参数**的 to_rgb888()（Utils.read_image）。
#   => 我们那个 to_rgb888(x_scale=..., y_scale=...) 在官方源码里**没有先例**。
#
# 【怎么跑】
#   改 SOURCE / VARIANT 后**单独跑一次**；每个变体之间建议断电重上电。
#   ⚠️ VARIANT=3 / 4 是已知会让板子掉电的两条 —— 心里有数再跑。
#
# 【判读】
#   VARIANT=1 或 2 成功  => 定案：不要缩放，靠 display_size 把 CHN0 一开始就设小
#                           （PipeLine(display_size=[320,240]) + to_rgb888() 无参）
#   VARIANT=1、2 也崩    => 换 SOURCE="sensor"（官方式 Sensor 直出 RGB888，全程零转换）
#   VARIANT=5 的输出     => to_rgb888 的真实签名 / 可用方法，用来停止猜测

import sys, time, gc

# --- 让 k230_classify 能被 import（板子从 /sdcard 跑；IDE 里 __file__ 可能没有）---
for _p in ("/sdcard", "/sdcard/probes", ".", ".."):
    if _p not in sys.path:
        sys.path.append(_p)

import image
from media.sensor import *
from media.media import *
from media.display import *      # 官方式采集要 Display.init（camera.py:19 的顺序：Display → Media）
from libs.PipeLine import PipeLine
import k230_classify as K        # 复用存量：K._say / K._mem_free / K._encode_jpeg

TAG = "P10"

# ========================= 开关（一次只动一个）=========================
# "pipeline" = 旧现状：PipeLine → snapshot(chn=CHN0) → 转换 → 编码
#              ❌ 2026-10-02 实测：VARIANT=1（无参 to_rgb888）**也直接下电**
#                 ⇒ 不是缩放参数的锅，是"对 CHN0 的 YUV420SP 活帧做转换"这条路本身不成立。
#                 佐证：官方源码里 to_rgb888() 只出现在**读文件**处
#                 （face_registration.py:265 对 image.Image(文件)），活帧从不用它。
# "sensor"   = 官方式：Sensor 直出 RGB888（全程零转换），只测"采集 + 编码能不能出字节"。
#              官方出处：rgb888_find_blobs.py:45 set_pixformat(Sensor.RGB888) / :80-81
#                        snapshot() → to_numpy_ref()（零拷贝，不做任何转换）
#              ★ 现行：pipeline 路已判死，改跑这条。
SOURCE = "sensor"

# 仅 SOURCE="pipeline" 时生效，1~5：
#   1 = img.to_rgb888()                          无参（官方写法，最可能成功）
#   2 = img.to_rgb565()                          无参，内存更省
#   3 = img.to_rgb888(x_scale=0.5, y_scale=0.5)  复现现状（已知会掉电）
#   4 = img.to_rgb888(0.5, 0.5)                  位置参数（怀疑真凶：0.5 被当成别的参数）
#   5 = 内省：不转换，只打印 img 的能力与 to_rgb888 的文档
VARIANT = 1

# None = 与正式脚本一致（实测 CHN0 = 640x480）；[320,240] = 小图对照（验证"不缩放也能很小"）
PIPE_DISPLAY_SIZE = None

ENCODE_AFTER = True          # 转换成功后接着测三种编码，看能不能拿到 JPEG 字节
SHOT_FAIL_SCALE = 0.5        # 变体 3/4 用的缩放比例
SHOT_QUALITY = 60
SENSOR_W, SENSOR_H = 640, 480    # 仅 SOURCE="sensor" 用
SENSOR_FORMAT = "RGB888"         # 官方 rgb888_find_blobs.py:45 用的就是这个


# ------------------------------------------------------------------
# 收尾（两个 source 分支共用）
# ------------------------------------------------------------------
def cleanup(pl=None, sensor=None):
    if pl is not None:
        try:
            pl.destroy()
            K._say("[%s] PipeLine 已销毁" % TAG)
        except Exception as e:
            K._say("[%s] PipeLine 销毁失败（照旧按 reset）: %s" % (TAG, e))
    if sensor is not None:
        try:
            sensor.stop()
            K._say("[%s] sensor 已停止" % TAG)
        except Exception as e:
            K._say("[%s] sensor 停止失败（照旧按 reset）: %s" % (TAG, e))
    try:
        Display.deinit()
        K._say("[%s] Display 已反初始化" % TAG)
    except Exception as e:
        K._say("[%s] Display.deinit 失败（照旧按 reset）: %s" % (TAG, e))
    try:
        MediaManager.deinit()
        K._say("[%s] MediaManager 已反初始化" % TAG)
    except Exception as e:
        K._say("[%s] MediaManager.deinit 失败（照旧按 reset）: %s" % (TAG, e))


# ------------------------------------------------------------------
# 编码：先用正式脚本的 _encode_jpeg（真路），失败再逐个探因
# ------------------------------------------------------------------
def try_encode(rgb):
    t0 = time.ticks_ms()
    raw = K._encode_jpeg(rgb, SHOT_QUALITY)
    dt = time.ticks_diff(time.ticks_ms(), t0) / 1000.0
    if raw:
        K._say("[%s] ④ 编码成功 %d 字节（%.2fs，K._encode_jpeg / q%d）"
               % (TAG, len(raw), dt, SHOT_QUALITY))
        return raw
    K._say("[%s] ④ K._encode_jpeg 返回 None（%.2fs）—— 逐个探因：" % (TAG, dt))
    for how in ("to_jpeg", "compress", "save"):
        try:
            if how == "save":
                rgb.save("/sdcard/_p10.jpg", quality=SHOT_QUALITY)
                K._say("[%s]     save() ✔（存到 /sdcard/_p10.jpg）" % TAG)
            else:
                out = getattr(rgb, how)(quality=SHOT_QUALITY)
                K._say("[%s]     %s() ✔ %d 字节" % (TAG, how, len(bytes(out))))
        except Exception as e:
            K._say("[%s]     %s() ✘ %s: %s" % (TAG, how, type(e).__name__, e))
    return None


# ------------------------------------------------------------------
# 分支二：官方式 Sensor 直出 RGB888（不做任何转换）
# ------------------------------------------------------------------
def run_sensor_source():
    K._say("[%s] 分支 SOURCE=sensor：Sensor 直出 %s %dx%d（官方式，零转换）"
           % (TAG, SENSOR_FORMAT, SENSOR_W, SENSOR_H))
    K._say("[%s]   mem_free=%s" % (TAG, K._mem_free()))
    # 顺序照抄官方 camera.py:13-23 —— Sensor 配置 -> Display.init -> MediaManager.init -> sensor.run
    sensor = Sensor()
    sensor.reset()
    sensor.set_framesize(width=SENSOR_W, height=SENSOR_H)
    sensor.set_pixformat(Sensor.RGB888)
    try:
        Display.init(Display.VIRT, SENSOR_W, SENSOR_H, to_ide=True)
        K._say("[%s] Display.init(VIRT %dx%d, to_ide=True) ✔（IDE 里能看图）"
               % (TAG, SENSOR_W, SENSOR_H))
    except Exception as e:
        K._say("[%s] ⚠️ Display.init 失败（不影响本探针结论）: %s: %s"
               % (TAG, type(e).__name__, e))
    MediaManager.init()
    sensor.run()
    K._say("[%s] ① sensor 就绪，snapshot 中…" % TAG)

    t0 = time.ticks_ms()
    img = sensor.snapshot()
    K._say("[%s] ① 完成 %sx%s（%.2fs，mem_free=%s）"
           % (TAG, getattr(img, "width", lambda: "?")(),
              getattr(img, "height", lambda: "?")(),
              time.ticks_diff(time.ticks_ms(), t0) / 1000.0, K._mem_free()))

    # ③ 顺便验 to_numpy_ref（正式脚本的 AI 输入要吃这一路；官方出处 rgb888_find_blobs.py:81）
    try:
        arr = img.to_numpy_ref()
        K._say("[%s] ③ to_numpy_ref() ✔ shape=%s" % (TAG, arr.shape))
    except Exception as e:
        K._say("[%s] ③ to_numpy_ref() ✘ %s: %s" % (TAG, type(e).__name__, e))

    try:
        Display.show_image(img)      # 只为了让你在 IDE 里肉眼确认画面
        K._say("[%s] Display.show_image() ✔" % TAG)
    except Exception as e:
        K._say("[%s] Display.show_image() ✘ %s: %s" % (TAG, type(e).__name__, e))

    if ENCODE_AFTER:
        try_encode(img)
    cleanup(sensor=sensor)
    K._say("[%s] 结论见上面：① 出图 + ④ 出字节 = 官方式采集可用（图片不需要转换）" % TAG)


# ------------------------------------------------------------------
# 分支一：PipeLine → CHN0 → 逐变体转换
# ------------------------------------------------------------------
def convert(img, variant):
    """返回 (rgb, 描述)。variant=5 只做内省，返回 (None, 'introspect')。"""
    if variant == 1:
        return img.to_rgb888(), "to_rgb888()"
    if variant == 2:
        return img.to_rgb565(), "to_rgb565()"
    if variant == 3:
        return (img.to_rgb888(x_scale=SHOT_FAIL_SCALE, y_scale=SHOT_FAIL_SCALE),
                "to_rgb888(x_scale=%.2f, y_scale=%.2f)" % (SHOT_FAIL_SCALE, SHOT_FAIL_SCALE))
    if variant == 4:
        return (img.to_rgb888(SHOT_FAIL_SCALE, SHOT_FAIL_SCALE),
                "to_rgb888(%.2f, %.2f)" % (SHOT_FAIL_SCALE, SHOT_FAIL_SCALE))
    return None, "introspect"


def introspect(img):
    """变体 5：不调用转换，只查真实能力（查档求证，别猜签名）"""
    K._say("[%s] ⑤ 内省 img：type=%s" % (TAG, type(img).__name__))
    for name in ("width", "height", "size", "format", "to_rgb888", "to_rgb565",
                 "copy", "save", "compress", "to_jpeg", "to_grayscale", "resize"):
        K._say("[%s]     hasattr(img, %-12s) = %s" % (TAG, name, hasattr(img, name)))
    for name in ("width", "height", "size", "format"):
        try:
            K._say("[%s]     img.%s() = %s" % (TAG, name, getattr(img, name)()))
        except Exception as e:
            K._say("[%s]     img.%s() 调用失败: %s: %s" % (TAG, name, type(e).__name__, e))
    for name in ("to_rgb888", "to_rgb565", "copy"):
        fn = getattr(img, name, None)
        if fn is None:
            continue
        try:
            K._say("[%s]     %s.__doc__ = %r" % (TAG, name, getattr(fn, "__doc__", None)))
        except Exception as e:
            K._say("[%s]     %s.__doc__ 取不到: %s" % (TAG, name, e))


def run_pipeline_source():
    K._say("[%s] 分支 SOURCE=pipeline：PipeLine(display_size=%s) → snapshot(CHN0)"
           % (TAG, PIPE_DISPLAY_SIZE))
    K._say("[%s]   mem_free=%s" % (TAG, K._mem_free()))
    pl = PipeLine(rgb888p_size=[K.IMG_SIZE, K.IMG_SIZE],
                  display_size=PIPE_DISPLAY_SIZE, display_mode=K.DISPLAY_MODE)
    pl.create()
    K._say("[%s] PipeLine 就绪（display_size=%s）" % (TAG, getattr(pl, "display_size", "?")))

    K._say("[%s] ① snapshot(chn=CHN0) 中…（mem_free=%s）" % (TAG, K._mem_free()))
    t0 = time.ticks_ms()
    img = pl.sensor.snapshot(chn=CAM_CHN_ID_0)
    K._say("[%s] ① 完成 %sx%s（%.2fs，mem_free=%s）"
           % (TAG, getattr(img, "width", lambda: "?")(),
              getattr(img, "height", lambda: "?")(),
              time.ticks_diff(time.ticks_ms(), t0) / 1000.0, K._mem_free()))

    if VARIANT == 5:
        introspect(img)
        cleanup(pl=pl)
        K._say("[%s] 结论 = 上面就是 img 的真实能力和 to_rgb888 的文档（用来停止猜签名）" % TAG)
        return

    desc = "?"
    K._say("[%s] ② 转换 VARIANT=%d 开始（mem_free=%s）" % (TAG, VARIANT, K._mem_free()))
    t0 = time.ticks_ms()
    rgb, desc = convert(img, VARIANT)
    dt = time.ticks_diff(time.ticks_ms(), t0) / 1000.0
    if rgb is None:
        K._say("[%s] ② %s 返回 None —— 该转换不可用" % (TAG, desc))
        cleanup(pl=pl)
        return
    K._say("[%s] ② 成功 %s → %sx%s（%.2fs，mem_free=%s）"
           % (TAG, desc,
              getattr(rgb, "width", lambda: "?")(),
              getattr(rgb, "height", lambda: "?")(), dt, K._mem_free()))

    if ENCODE_AFTER:
        try_encode(rgb)

    del rgb
    gc.collect()
    cleanup(pl=pl)
    K._say("[%s] 结论 = VARIANT=%d（%s）**没崩**；崩没崩只看这一条" % (TAG, VARIANT, desc))


# ------------------------------------------------------------------
print("=" * 60)
print(" 探针 P10：抓拍转换路径逐变体二分（纯图像，不联网/不推理）")
print(" SOURCE=%s   VARIANT=%s   PIPE_DISPLAY_SIZE=%s"
      % (SOURCE, VARIANT if SOURCE == "pipeline" else "-", PIPE_DISPLAY_SIZE))
print(" ⚠️ VARIANT=3 / 4 是已知会让板子掉电的两条；跑完必须断电重上电")
print("=" * 60)
K._say("[%s] mem_free(启动) = %s" % (TAG, K._mem_free()))

try:
    if SOURCE == "sensor":
        run_sensor_source()
    else:
        run_pipeline_source()
except Exception as e:
    K._say("[%s] 异常（Python 层，不是掉电）: %s: %s" % (TAG, type(e).__name__, e))
    try:
        import sys as _s
        _s.print_exception(e)
    except Exception:
        pass

print("-" * 60)
print("[%s] 判读：" % TAG)
print("   VARIANT=1/2 没崩 + 编码出字节  => 定案：不缩放，用 display_size 把 CHN0 直接设小")
print("   VARIANT=1/2 也崩             => 改 SOURCE=\"sensor\"（官方式直出 RGB888，零转换）")
print("   VARIANT=3/4 崩                => 坐实：带缩放的 to_rgb888 就是凶手")
print("   VARIANT=5                    => 把内省结果发我，用来停止猜签名")
print("[%s] 把上面全部日志贴回来" % TAG)
