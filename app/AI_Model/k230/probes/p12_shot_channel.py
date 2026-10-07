# -*- coding: utf-8 -*-
# 探针 P12：抓拍通道选型（一次相机 init 回答 4 个问题）
#
# ============ 只用相机 + 编码：不联网 / 不推理 / 不写库 ============
#
# 【为什么要跑】
#   2026-10-02 两组实测把两件事钉死了：
#     · P11：我们的新 kmodel（uint8 输入）板上 kpu.run() = 0.003s；
#            66 秒的根因 = 旧 kmodel 是 float32 口径编的，**里面没有 KPU 段**。
#            ⇒ AI 那条路（pl.get_frame() → preprocess → kpu.run）**不用动**。
#     · P10：`SOURCE="sensor"` 全绿 —— Sensor 直出 RGB888 640x480 → to_numpy_ref()
#            shape=(480,640,3) → K._encode_jpeg(q60) = 13398 字节。
#            ⇒ 抓拍图**不需要任何转换**，也**不需要缩放**（13398 ≪ 上限 45000）。
#
#   唯一还挂在死路上的就是正式脚本的抓拍（行号为当时的 v11.2，v12 已改掉）：
#       k230_classify.py  _pl.sensor.snapshot(chn=CAM_CHN_ID_0)   # YUV420SP
#       k230_classify.py  _to_rgb_scaled(img, scale)              # → to_rgb888 → 【掉电】
#   ⇒ 2026-10-02 H1 实测成立后，v12 已改成「snapshot → 直接 _encode_jpeg」，
#     `_to_rgb_scaled` 与 `IMAGE_SCALE_LADDER` 都已删除。本探针保留备查。
#
# 【本探针要回答的 4 个问题】
#   H0  本固件 /sdcard/libs/PipeLine.py 里 `create()` 的**真实签名**是什么？
#       它能不能配置 CHN1（官方另一套 SDK 的写法：pl.create(ch1_frame_size=[W,H])）？
#       而且顺便把 PipeLine 给 CHN0 / CHN1 / CHN2 各配了什么 **pixformat** 打出来。
#       —— 这是"查档求证"，看完就不用再猜了。
#   H3  pl.get_frame()（CHN2，AI 的输入）在本改动下是否照旧？（回归，别把好的弄坏）
#   H1  CHN0（YUV420SP）**不做任何转换**，直接 _encode_jpeg 能不能出 JPEG 字节？
#       能 → 最省的改法（路线乙：只删掉 _to_rgb_scaled 那一行）
#   H2  CHN1（PipeLine 空闲通道）snapshot 出来是什么格式、能不能直接编码？
#       能 → 路线甲（PipeLine 一行不改 AI 路，只把抓拍换成 CHN1）
#
# ⚠️ 本探针**全程不调用 to_rgb888**（P9/P10 已实测：对 CHN0 活帧调它必掉电）。
#
# 【怎么跑】
#   CanMV IDE 里直接跑（探针文件不必拷进 /sdcard）。跑完把**全部日志**贴回来。
#   跑完**断电重上电**一次再跑别的（相机流水线不释放会让下个脚本假卡在 A4）。
#   顺序是固定的 H0 → create → H3 → H1 → H2；H2 放最后，因为它依赖 create() 的返回。
#
# 【判读】
#   H1 出字节                    => 走路线乙（改动最小：抓拍删掉转换，直接编码）
#   H1 失败 + H2 出字节          => 走路线甲（抓拍改 CHN1）
#   H1、H2 都失败                => 走路线丙（弃用 PipeLine，裸 Sensor 三通道，
#                                   官方出处 det_video.py:144-149 / 19.snapshot.py:90-91）
#   H3 的 shape 不是 (3,224,224) => 先别改抓拍，AI 路也要一起重做（贴回来再定）

import sys, time, gc, os

# --- 让 k230_classify 能被 import（板子从 /sdcard 跑；IDE 里 __file__ 可能没有）---
for _p in ("/sdcard", "/sdcard/probes", ".", ".."):
    if _p not in sys.path:
        sys.path.append(_p)

import image
from media.sensor import *
from media.media import *
from media.display import *
from libs.PipeLine import PipeLine
import k230_classify as K        # 复用存量：K._say / K._mem_free / K._encode_jpeg / K.IMG_SIZE

TAG = "P12"

# ========================= 开关 =========================
DUMP_LIB = True                  # H0：把 /sdcard/libs/PipeLine.py 打出来（事实来源）
DUMP_MAX_LINES = 400             # 太长就截断（自己数行数，会打印总行数）
DUMP_ONLY = False                # True = 只 dump 源码、不碰相机（万一相机路出问题）

CH1_FRAME_SIZE = [640, 480]      # 试给 pl.create() 的 ch1 尺寸；设 None 就只试无参 create()

SHOT_W, SHOT_H = 640, 480        # 抓拍目标尺寸（Q2 定为 640x480，P10 实测 13KB）
SHOT_QUALITY = 60
QUAL_LADDER = (60, 40, 25)       # 每档**重新取帧**（to_jpeg/compress 是原地操作，见 v10 的坑）

RUN_H3 = True                    # pl.get_frame() 回归
RUN_H1 = True                    # CHN0（YUV420SP）直接编码
RUN_H2 = True                    # CHN1 直接编码

DO_DESTROY = True                # 收尾 teardown；卡住的话日志会停在这句前面


# ------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------
def _fmt_of(img):
    for name in ("format",):
        fn = getattr(img, name, None)
        if fn is None:
            continue
        try:
            return fn()
        except Exception as e:
            return "<%s() 失败: %s>" % (name, e)
    return "<无 format()>"


def _dim_of(img):
    w = h = -1
    try:
        w = img.width()
    except Exception:
        pass
    try:
        h = img.height()
    except Exception:
        pass
    return w, h


def show_src(text, fname):
    """把源码里 __init__ 与 create 两个块打出来（别的太长就不刷屏）。"""
    lines = text.split("\n")
    K._say("[%s]     %s 共 %d 行" % (TAG, fname, len(lines)))
    targets = ("def __init__", "def create", "def __del__", "def destroy")
    for t in targets:
        start = None
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith(t):
                start = i
                break
        if start is None:
            K._say("[%s]     ---- %s : 未找到 ----" % (TAG, t))
            continue
        indent = len(lines[start]) - len(lines[start].lstrip())
        K._say("[%s]     ---- %s (第 %d 行) ----" % (TAG, t, start + 1))
        for j in range(start, min(len(lines), start + 120)):
            if j > start:
                s = lines[j]
                if s.strip() and (len(s) - len(s.lstrip())) <= indent \
                        and (s.lstrip().startswith("def ") or s.lstrip().startswith("class ")):
                    break
            K._say("[%s]     | %s" % (TAG, lines[j]))


def h0_dump_lib():
    K._say("[%s] ======== H0：PipeLine 源码取证 ========" % TAG)
    try:
        K._say("[%s]   /sdcard/libs 目录：%s" % (TAG, os.listdir("/sdcard/libs")))
    except Exception as e:
        K._say("[%s]   listdir('/sdcard/libs') 失败: %s: %s" % (TAG, type(e).__name__, e))

    try:
        K._say("[%s]   dir(PipeLine) = %s" % (TAG, dir(PipeLine)))
    except Exception as e:
        K._say("[%s]   dir(PipeLine) 失败: %s: %s" % (TAG, type(e).__name__, e))

    for name in ("__init__", "create", "destroy", "__del__", "get_frame"):
        fn = getattr(PipeLine, name, None)
        if fn is None:
            K._say("[%s]   PipeLine.%s : 不存在" % (TAG, name))
            continue
        try:
            K._say("[%s]   PipeLine.%s.__doc__ = %r" % (TAG, name, getattr(fn, "__doc__", None)))
        except Exception as e:
            K._say("[%s]   PipeLine.%s.__doc__ 取不到: %s" % (TAG, name, e))

    for path in ("/sdcard/libs/PipeLine.py", "/sdcard/libs/PipeLine.mpy"):
        try:
            with open(path) as f:
                txt = f.read()
            K._say("[%s]   ✔ 读到 %s（%d 字符）" % (TAG, path, len(txt)))
            show_src(txt, path)
            lines = txt.split("\n")
            if len(lines) <= DUMP_MAX_LINES:
                K._say("[%s]   ---- 全文（%d 行）----" % (TAG, len(lines)))
                for i, ln in enumerate(lines):
                    K._say("[%s]   %3d| %s" % (TAG, i + 1, ln))
            else:
                K._say("[%s]   ---- 全文 %d 行 > 上限 %d，已跳过；只打上面几个块 ----"
                       % (TAG, len(lines), DUMP_MAX_LINES))
            return
        except Exception as e:
            K._say("[%s]   读 %s 失败: %s: %s" % (TAG, path, type(e).__name__, e))
    K._say("[%s]   ✘ 两份都没读到 —— 请在 IDE 里手动打开 /sdcard/libs/PipeLine.py 贴给我"
           % TAG)


# ------------------------------------------------------------------
# 抓拍 + 编码（同一通道反复取帧，因为 to_jpeg/compress 是**原地**改格式）
# ------------------------------------------------------------------
def shot_and_encode(pl, chn, label, ladder):
    """返回 (ok, first_bytes)。ok=True 表示至少 q 最高那档出了合法 JPEG。"""
    K._say("[%s] %s: snapshot(chn=%s) 中…（mem_free=%s）" % (TAG, label, chn, K._mem_free()))
    t0 = time.ticks_ms()
    try:
        img = pl.sensor.snapshot(chn=chn)
    except Exception as e:
        K._say("[%s] %s ✘ snapshot 失败: %s: %s" % (TAG, label, type(e).__name__, e))
        return False, None
    dt = time.ticks_diff(time.ticks_ms(), t0) / 1000.0
    w, h = _dim_of(img)
    K._say("[%s] %s ✔ 帧 %dx%d  format=%s（%.2fs，mem_free=%s）"
           % (TAG, label, w, h, _fmt_of(img), dt, K._mem_free()))
    try:
        K._say("[%s] %s   img 能力: %s"
               % (TAG, label,
                  [n for n in ("to_rgb888", "to_rgb565", "to_jpeg", "compress",
                               "save", "copy", "to_numpy_ref", "format")
                   if hasattr(img, n)]))
    except Exception:
        pass
    del img
    gc.collect()

    first = None
    for q in ladder:
        try:
            img = pl.sensor.snapshot(chn=chn)
        except Exception as e:
            K._say("[%s] %s ✘ q%d 重取帧失败: %s: %s" % (TAG, label, q, type(e).__name__, e))
            return False, first
        t0 = time.ticks_ms()
        raw = K._encode_jpeg(img, q)
        dt = time.ticks_diff(time.ticks_ms(), t0) / 1000.0
        if raw:
            K._say("[%s] %s ✔ q%d → %d 字节（%.2fs）%s"
                   % (TAG, label, q, len(raw), dt,
                      "  ← 远小于上限 45000，不需要缩放" if len(raw) <= 45000 else "  ← 超上限！"))
            if first is None:
                first = raw
        else:
            K._say("[%s] %s ✘ q%d → 编码失败（%.2fs）：to_jpeg/compress/save 三种都不可用"
                   % (TAG, label, q, dt))
        del img
        try:
            del raw
        except Exception:
            pass
        gc.collect()
    return first is not None, first


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
print("=" * 60)
print(" 探针 P12：抓拍通道选型（只用相机 + 编码，不联网/不推理/不写库）")
print(" CH1_FRAME_SIZE=%s  SHOT=%dx%d  QUAL=%s  DUMP_ONLY=%s"
      % (CH1_FRAME_SIZE, SHOT_W, SHOT_H, QUAL_LADDER, DUMP_ONLY))
print(" ⚠️ 全程不调用 to_rgb888（对 CHN0 活帧调它必掉电）")
print("=" * 60)
K._say("[%s] mem_free(启动) = %s" % (TAG, K._mem_free()))

pl = None
try:
    if DUMP_LIB:
        h0_dump_lib()

    if DUMP_ONLY:
        K._say("[%s] DUMP_ONLY=True，跳过相机部分" % TAG)
    else:
        # ---------------- 建 PipeLine（顺带试 ch1_frame_size）----------------
        K._say("[%s] ======== 建 PipeLine ========" % TAG)
        pl = PipeLine(rgb888p_size=[K.IMG_SIZE, K.IMG_SIZE],
                      display_size=[SHOT_W, SHOT_H], display_mode=K.DISPLAY_MODE)
        has_ch1 = False
        if CH1_FRAME_SIZE:
            try:
                pl.create(ch1_frame_size=CH1_FRAME_SIZE)
                has_ch1 = True
                K._say("[%s] pl.create(ch1_frame_size=%s) ✔ —— 本固件**支持** CHN1 配置"
                       % (TAG, CH1_FRAME_SIZE))
            except TypeError as e:
                # TypeError 是在**进函数体之前**抛的（参数绑定阶段），所以这里 create 还没跑过
                K._say("[%s] pl.create(ch1_frame_size=...) ✘ TypeError: %s" % (TAG, e))
            except Exception as e:
                K._say("[%s] pl.create(ch1_frame_size=...) ✘ %s: %s"
                       % (TAG, type(e).__name__, e))
        if not has_ch1:
            try:
                pl.create()
                K._say("[%s] 退回 pl.create()（无参）✔ —— 抓拍改 CHN1 这条要另想办法"
                       % TAG)
            except Exception as e:
                K._say("[%s] ⚠️ 退回无参 create() 也失败（可能已被部分创建）: %s: %s"
                       % (TAG, type(e).__name__, e))
        try:
            K._say("[%s] PipeLine 就绪：display_size=%s rgb888p_size=%s display_mode=%s"
                   % (TAG, getattr(pl, "display_size", "?"),
                      getattr(pl, "rgb888p_size", "?"), getattr(pl, "display_mode", "?")))
        except Exception:
            pass
        K._say("[%s]   mem_free=%s" % (TAG, K._mem_free()))

        # ---------------- H3：AI 路回归（CHN2）----------------
        if RUN_H3:
            K._say("[%s] ======== H3：pl.get_frame() 回归（AI 的输入口）========" % TAG)
            try:
                frame = pl.get_frame()
                shp = getattr(frame, "shape", "?")
                dtp = "?"
                try:
                    dtp = frame.dtype
                except Exception:
                    dtp = "<无 dtype>"
                K._say("[%s] H3 ✔ shape=%s dtype=%s" % (TAG, shp, dtp))
                if str(shp) in ("(3, 224, 224)", "[3, 224, 224]", "(1, 3, 224, 224)"):
                    K._say("[%s] H3 ✔ 与正式脚本 preprocess() 的预期一致，AI 路不用动" % TAG)
                else:
                    K._say("[%s] ⚠️ H3 shape 不是 (3,224,224)/(1,3,224,224) —— 贴回来再定" % TAG)
                del frame
            except Exception as e:
                K._say("[%s] H3 ✘ %s: %s" % (TAG, type(e).__name__, e))
            gc.collect()

        # ---------------- H1：CHN0（YUV420SP）直接编码 ----------------
        if RUN_H1:
            K._say("[%s] ======== H1：CHN0（YUV420SP，零转换）直接编码 ========" % TAG)
            ok1, _ = shot_and_encode(pl, CAM_CHN_ID_0, "H1 CHN0", (SHOT_QUALITY,) + QUAL_LADDER[1:])
            if ok1:
                K._say("[%s] H1 结论：✔ 成立 ⇒ 路线乙（抓拍删掉 _to_rgb_scaled，直接编码）" % TAG)
            else:
                K._say("[%s] H1 结论：✘ 不成立 ⇒ CHN0 的 YUV420SP 连直接编码都不行" % TAG)

        # ---------------- H2：CHN1 直接编码 ----------------
        if RUN_H2:
            K._say("[%s] ======== H2：CHN1（PipeLine 空闲通道）直接编码 ========" % TAG)
            ok2, _ = shot_and_encode(pl, CAM_CHN_ID_1, "H2 CHN1", (SHOT_QUALITY,) + QUAL_LADDER[1:])
            if ok2:
                K._say("[%s] H2 结论：✔ 成立 ⇒ 路线甲（抓拍改 CHN1，AI 路一行不动）" % TAG)
            else:
                K._say("[%s] H2 结论：✘ 不成立 ⇒ 只剩路线丙（裸 Sensor 三通道）" % TAG)

    # ---------------- 收尾 ----------------
    if DO_DESTROY and pl is not None:
        K._say("[%s] 收尾：pl.destroy() 中…（若日志停在这句，说明 destroy 卡住了，按 reset）" % TAG)
        try:
            pl.destroy()
            K._say("[%s] PipeLine 已销毁" % TAG)
        except Exception as e:
            K._say("[%s] PipeLine 销毁失败（照旧按 reset）: %s: %s" % (TAG, type(e).__name__, e))
    try:
        Display.deinit()
        K._say("[%s] Display 已反初始化" % TAG)
    except Exception as e:
        K._say("[%s] Display.deinit 失败: %s: %s" % (TAG, type(e).__name__, e))
    try:
        MediaManager.deinit()
        K._say("[%s] MediaManager 已反初始化" % TAG)
    except Exception as e:
        K._say("[%s] MediaManager.deinit 失败: %s: %s" % (TAG, type(e).__name__, e))

except Exception as e:
    K._say("[%s] 异常（Python 层，不是掉电）: %s: %s" % (TAG, type(e).__name__, e))
    try:
        sys.print_exception(e)
    except Exception:
        pass

print("-" * 60)
print("[%s] 判读：" % TAG)
print("   H0  create() 真实签名 + 各通道 pixformat  -> 贴回来，停止猜接口")
print("   H3  shape=(3,224,224)                      -> AI 路没被牵连，不用动")
print("   H1  CHN0 直接编码出字节                    -> 路线乙（最省，删一行转换）")
print("   H2  CHN1 直接编码出字节                    -> 路线甲（抓拍换通道）")
print("   H1、H2 都失败                              -> 路线丙（裸 Sensor 三通道）")
print("[%s] 把上面全部日志贴回来；跑完请断电重上电" % TAG)
