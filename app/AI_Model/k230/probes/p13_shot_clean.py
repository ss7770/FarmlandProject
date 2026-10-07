# -*- coding: utf-8 -*-
# 探针 P13：抓拍【拍清楚】路线 B / A 实测 —— 图片直接 POST 回 Flask，PC 上解码肉眼判读
#
# ============ 只用相机 + 编码 + 传图：不推理 / 不写巡检记录 ============
#
# 【为什么要跑】
#   2026-10-02 花屏定案：`to_jpeg()` 拿 CHN0 的 YUV420SP **不报错**，却按 RGB565 打包。
#     · 判据：日志里**一条 `编码 xxx 不可用` 都没有**（那几行是裸 print，不受 _say 节流）
#             ⇒ 排在第一个的 `to_jpeg` 就"成功"了，compress / save 压根没被试过。
#     · 算术：640×480 按 RGB565 装 = 614400 字节容量 > NV12 buffer 460800
#             ⇒ 前 50% = Y 平面 / 接着 25% = UV 交错 / 最后 25% = 读越界，
#               与花屏图上的三条带完全吻合。
#   v13 把编码顺序改成 save 优先也没救回来：板上明说
#     `编码 save 不可用: OSError: current format not support save function!` → 回落 to_jpeg。
#
#   ⇒ 结论：**CHN0 的 YUV420SP 在这一版 image 模块里根本没有可用的 JPEG 出口。**
#     想拍清楚，必须换一个"image 模块认识的格式"的通道。本探针就是去找那个通道。
#
# 【官方出处】（两套官方资料全扫过）
#   ① `02.Basic/19.snapshot.py:90-91,106,130` —— 官方**唯一**的 snapshot 存图范式：
#        sensor.set_framesize(width=640, height=480, chn=CAM_CHN_ID_1)
#        sensor.set_pixformat(Sensor.RGB565,      chn=CAM_CHN_ID_1)
#        img = sensor.snapshot(chn=CAM_CHN_ID_1)
#        img.save(path)                      # 直接出 .jpg
#   ② `13.training/det_video.py:138-160` —— 裸 Sensor 多通道并存范式：
#        CHN0 = YUV420SP 绑显示 / CHN2 = RGBP888 给 AI / Display.bind_layer(..., LAYER_VIDEO1)
#   ③ `04.Detecting/01.find_lines.py:100-110` —— `pl.create(ch1_frame_size=[W,H])`
#        ⚠️ 那是**另一套 SDK** 的 PipeLine；我们板上那份没这个参数（P12 实测 TypeError）。
#
# 【本探针要回答的 2 个问题】（顺序：先 B 后 A）
#   B  裸 Sensor 三通道：CHN0 YUV420SP 显示 / **CHN1 RGB565 拍照** / CHN2 RGBP888 给 AI
#      → snapshot(chn=1) 出来是什么格式？能不能出**内容正确**的 JPEG？
#      → 顺便回归 CHN2 的 to_numpy_ref()（形状必须 (3,224,224)，AI 路不能被弄坏）
#   A  保留 PipeLine，在 `create()` 之后**自己补配 CHN1**（19.snapshot.py 的姿势）
#      → 唯一的未知：create() 内部已经 MediaManager.init()+sensor.run()，
#        **run 之后再改通道配置能不能被接受**。
#        ⚠️ P12 只试过"把 ch1_frame_size 传给 create()"（板上 TypeError），
#           **从没试过自己调 set_framesize/set_pixformat** ⇒ 路线 A 没死，只是没试对姿势。
#
#   ⚠️ 为什么 B 在前：A 的"run 后改配置"是**非标准操作**，万一它把相机流水线搞坏，
#      同一次开机里后跑的 B 就会被冤枉。B 全是官方标准姿势，先跑最干净。
#      若日志里 B 的 init 看起来像"第二次初始化失败"（sensor already inited / 卡 A4），
#      把 ONLY 改成 "B"，**断电重上电**后单独再跑一次。
#
# ⚠️⚠️ 判读标准（P12 的老毛病，别再犯）：
#      "出字节 / 魔数对 / 字节数大" **证明不了内容正确** —— P12 就是只看字节数，
#      把一张花图判成了 ✅。所以本探针把每张候选图 **POST 回 Flask**，
#      落到 `Python_Backend/uploads/edge_p13-*.jpg`，**PC 上解码肉眼看才算数**。
#
# ⚠️ 全程**不调用 to_rgb888 / to_rgb565**（对 CHN0 活帧调它必掉电，P9/P10 两次实测）。
#
# 【怎么跑】
#   1. PC 上 Flask 要在跑，且 PC 与板子同网段（地址见 k230_classify.py 的 SERVER_URL）。
#      PC 自检：`curl http://192.168.57.97:5000/api/edge/ping` → 200
#   2. CanMV IDE 里直接跑本文件（探针不必拷进 /sdcard）。跑完把**全部日志**贴回来。
#   3. 顺便记一下 IDE 预览窗口是不是正常彩色 —— 那是"相机数据本身是好的"的旁证。
#   4. 跑完**断电重上电**一次再跑别的（相机流水线不释放会让下个脚本假卡在 A4）。
#
# 【判读】
#   B 出图且肉眼正确     => 走路线 B 改正式脚本（裸 Sensor 三通道）
#   B 花/失败，A 正确    => 走路线 A（PipeLine + 自配 CHN1），改动最小
#   B、A 都花/失败       => 回 PC 反解兜底（utils/edge_shot_decode.py，Y 平面已验证能还原）
#   任一变体里 CHN2 形状不是 (3,224,224) => 先别动正式脚本，AI 路被牵连了

import sys
import os
import gc
import time

# --- 让 k230_classify 能被 import（板子从 /sdcard 跑；IDE 里 __file__ 可能没有）---
for _p in ("/sdcard", "/sdcard/probes", ".", ".."):
    if _p not in sys.path:
        sys.path.append(_p)

import image
from media.sensor import *
from media.media import *
from media.display import *
from libs.PipeLine import PipeLine

import k230_classify as K        # 复用存量：_say / _mem_free / _encode_jpeg / _http / 质量阶梯 …

TAG = "P13"

# ========================= 开关（一般不用改）=========================
ONLY = "AB"            # "B" | "A" | "AB"（默认先 B 后 A）
POST_IMAGES = True     # 把候选图 POST 回 Flask —— 判读全靠它，别关
SAVE_LOCAL = True      # 同时在 /sdcard 留备份（POST 失败时的唯一退路）
RUN_AI_CHECK = True    # 回归 CHN2 的 to_numpy_ref()（AI 输入的形状）
USE_CHN0 = True        # 变体 B 是否配 CHN0 + 绑显示（保住 IDE 预览；失败会自动跳过）

SHOT_W, SHOT_H = 640, 480
IMG_SIZE = K.IMG_SIZE                    # 224
QUALITY_LADDER = K.IMAGE_QUALITY_LADDER  # (60, 40, 25)
SAVE_DIR = "/sdcard"

# 容器 uid：结论记录 + 所有候选图共用它（服务端据此把图贴到同一条记录上）。
# 用开机时刻做后缀，跨次开机不会撞；跑完按它删记录。
try:
    UID = "p13probe-%d" % int(time.time())
except Exception:
    UID = "p13probe-0"


# ------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------
def _fmt_of(img):
    fn = getattr(img, "format", None)
    if fn is None:
        return "<无 format()>"
    try:
        return fn()
    except Exception as e:
        return "<format() 失败: %s>" % e


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


def wifi_up():
    try:
        wlan = K.wifi_connect()
        if K.wifi_ready(wlan):
            K._say("[%s] wifi ✔ 可以传图" % TAG)
            return True
    except Exception as e:
        K._say("[%s] wifi 连接异常: %s: %s" % (TAG, type(e).__name__, e))
    K._say("[%s] wifi ✘ 没连上" % TAG)
    return False


def make_record():
    """建一条记录当"容器" —— 服务端 /image 要求 record_id 或能定位到记录的 client_uid。

    直接用 manual=True 绕开节流与去重，保证这条一定建出来。
    """
    if not POST_IMAGES:
        return False
    payload = {
        "disease": "p13probe",
        "confidence": 12.3,          # ⚠️ 服务端 _sane_confidence 把 <=1.0 判成非法，别写 1.0
        "source": "p13probe",
        "manual": True,
        "client_uid": UID,
    }
    try:
        body = K.ujson.dumps(payload).encode()
        K._http_send_only("POST", K.SERVER_URL, body, verbose=True)
        K._say("[%s] 容器记录已发出（uid=%s，跑完按它删）" % (TAG, UID))
        return True
    except Exception as e:
        K._say("[%s] ✘ 建容器记录失败: %s: %s" % (TAG, type(e).__name__, e))
        return False


def save_local(tag, raw):
    if not SAVE_LOCAL or not raw:
        return ''
    path = "%s/%s.jpg" % (SAVE_DIR, tag)
    try:
        with open(path, "wb") as f:
            f.write(raw)
        K._say("[%s] 本地备份 %s（%d 字节）" % (TAG, path, len(raw)))
        return path
    except Exception as e:
        K._say("[%s] ⚠️ 本地备份失败 %s: %s: %s" % (TAG, path, type(e).__name__, e))
        return ''


def post_image(tag, raw):
    """把一张候选 JPEG POST 回 Flask。

    tag 决定服务端落盘的文件名：`uploads/edge_<tag>_<微秒>.jpg`
    —— 所以每个候选图**必须给不同的 tag**，PC 上才不会认错哪张是哪条路线出来的。
    （每次 POST 都新建文件，不覆盖旧的；见 utils/edge_raw.save_jpeg_bytes）
    """
    if not raw:
        return False
    if not POST_IMAGES:
        K._say("[%s] POST_IMAGES=False，跳过 %s 的传输" % (TAG, tag))
        return False
    try:
        b64 = K.ubinascii.b2a_base64(raw).decode().strip()
    except Exception as e:
        K._say("[%s] ✘ %s base64 编码失败: %s: %s" % (TAG, tag, type(e).__name__, e))
        return False
    if len(b64) > K.IMAGE_MAX_B64:
        K._say("[%s] ⚠️ %s 的 base64 有 %d 字节 > 上限 %d —— 服务端可能不收"
               % (TAG, tag, len(b64), K.IMAGE_MAX_B64))
    payload = {"image": b64, "source": tag, "client_uid": UID}
    K._say("[%s] → 传 %s（%d 字节 JPEG / %d 字节 base64）"
           % (TAG, tag, len(raw), len(b64)))
    try:
        code, hdrs = K._http("POST", K.IMAGE_URL, K.ujson.dumps(payload).encode(),
                             settle=K.IMG_SETTLE, verbose=True, budget=K.IMG_BUDGET)
    except Exception as e:
        K._say("[%s] ✘ %s 传输异常: %s: %s" % (TAG, tag, type(e).__name__, e))
        return False
    if code == 200:
        K._say("[%s] ✔ %s 已落盘（record_id=%s）；PC 上找 uploads/edge_%s_*.jpg"
               % (TAG, tag, hdrs.get("x-edge-record-id") or "-", tag))
        return True
    if code == 0:
        K._say("[%s] ⚠️ %s 已发出但没读到响应头 —— 服务端**可能已经落盘**，"
               "先去 PC 上找文件再下结论" % (TAG, tag))
        return True
    K._say("[%s] ✘ %s 被拒 %d（status=%s）"
           % (TAG, tag, code, hdrs.get("x-edge-status") or "-"))
    return False


def deliver(tag, raw):
    """存本地 + 传给 Flask。返回 True 表示"图已经离开板子"。"""
    if not raw:
        K._say("[%s] %s 没出图，跳过传输" % (TAG, tag))
        return False
    save_local(tag, raw)
    return post_image(tag, raw)


def shot_encode(snap, label):
    """取一帧并编码，返回 (quality, raw)；全失败返回 (None, None)。

    ⚠️ **每一档质量都重新取帧** —— `to_jpeg` / `compress` 是**原地**把图变成 JPEG 的，
    同一个对象再编一次只会拿回上次结果（v10 踩过的坑）。
    """
    for q in QUALITY_LADDER:
        try:
            img = snap()
        except Exception as e:
            K._say("[%s] %s ✘ 取帧失败（q%d）: %s: %s" % (TAG, label, q, type(e).__name__, e))
            return None, None
        w, h = _dim_of(img)
        K._say("[%s] %s 取帧 %dx%d format=%s（mem_free=%s）"
               % (TAG, label, w, h, _fmt_of(img), K._mem_free()))
        t0 = K._ticks_ms()
        raw = None
        try:
            raw = K._encode_jpeg(img, q)     # 内部按 save → to_jpeg → compress 试，并打印"是谁编的"
        except Exception as e:
            K._say("[%s] %s ✘ 编码 q%d 抛异常: %s: %s" % (TAG, label, q, type(e).__name__, e))
        dt = K._ticks_diff(K._ticks_ms(), t0) / 1000.0
        try:
            del img
        except Exception:
            pass
        gc.collect()
        if raw:
            K._say("[%s] %s ✔ 编码 q%d -> %d 字节（%.2fs）" % (TAG, label, q, len(raw), dt))
            return q, raw
        K._say("[%s] %s ✘ 编码 q%d 一个字节都没出（%.2fs）" % (TAG, label, q, dt))
    return None, None


def ai_check(snap):
    """回归：AI 的输入通道还是不是 (3,224,224)。别把好的弄坏。"""
    K._say("[%s] ---- AI 通道回归：snapshot(chn=CHN2).to_numpy_ref() ----" % TAG)
    im = arr = None
    try:
        im = snap()
        arr = im.to_numpy_ref()
        shp = getattr(arr, "shape", None)
        K._say("[%s] AI ✔ shape=%s dtype=%s" % (TAG, shp, getattr(arr, "dtype", "?")))
        if shp == (3, IMG_SIZE, IMG_SIZE) or shp == (1, 3, IMG_SIZE, IMG_SIZE):
            K._say("[%s] AI ✔ 与 k230_classify.preprocess() 的预期一致，AI 路没被牵连" % TAG)
        else:
            K._say("[%s] AI ⚠️ shape 不是 (3,%d,%d) —— 贴回来再定，先别改正式脚本"
                   % (TAG, IMG_SIZE, IMG_SIZE))
    except Exception as e:
        K._say("[%s] AI ✘ %s: %s" % (TAG, type(e).__name__, e))
    try:
        del im
    except Exception:
        pass
    try:
        del arr
    except Exception:
        pass
    gc.collect()


def teardown_raw(sensor):
    """官方收尾顺序（19.snapshot.py:144-157）：sensor.stop → Display.deinit → MediaManager.deinit"""
    K._say("[%s] ---- 拆裸 Sensor ----" % TAG)
    if sensor is not None:
        try:
            sensor.stop()
            K._say("[%s] sensor.stop() ✔" % TAG)
        except Exception as e:
            K._say("[%s] ⚠️ sensor.stop() 失败: %s: %s" % (TAG, type(e).__name__, e))
    try:
        Display.deinit()
        K._say("[%s] Display.deinit() ✔" % TAG)
    except Exception as e:
        K._say("[%s] ⚠️ Display.deinit() 失败: %s: %s" % (TAG, type(e).__name__, e))
    try:
        os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
    except Exception:
        pass
    time.sleep_ms(100)
    try:
        MediaManager.deinit()
        K._say("[%s] MediaManager.deinit() ✔" % TAG)
    except Exception as e:
        K._say("[%s] ⚠️ MediaManager.deinit() 失败: %s: %s" % (TAG, type(e).__name__, e))
    gc.collect()
    time.sleep_ms(300)


def teardown_pipeline(pl):
    """拆 PipeLine。

    ⚠️ 板上那份 `PipeLine.destroy()` **不含 `MediaManager.deinit()`**（P12 H0 读过 157 行全文），
    官方 19.snapshot.py 的收尾是有它的 —— 这里**补上**，否则下一个变体建不起来。
    """
    K._say("[%s] ---- 拆 PipeLine ----" % TAG)
    if pl is not None:
        try:
            pl.destroy()
            K._say("[%s] pl.destroy() ✔" % TAG)
        except Exception as e:
            K._say("[%s] ⚠️ pl.destroy() 抛异常（照旧往下走）: %s: %s"
                   % (TAG, type(e).__name__, e))
    try:
        MediaManager.deinit()
        K._say("[%s] MediaManager.deinit() 补上 ✔" % TAG)
    except Exception as e:
        K._say("[%s] ⚠️ MediaManager.deinit() 失败: %s: %s" % (TAG, type(e).__name__, e))
    gc.collect()
    time.sleep_ms(300)


# ------------------------------------------------------------------
# 变体 B：裸 Sensor 三通道（官方 det_video.py + 19.snapshot.py 的组合）
# ------------------------------------------------------------------
def variant_b():
    K._say("")
    K._say("[%s] ======== 变体 B：裸 Sensor 三通道 ========" % TAG)
    sensor = None
    try:
        sensor = Sensor()
        sensor.reset()

        if USE_CHN0:
            try:
                sensor.set_framesize(width=SHOT_W, height=SHOT_H, chn=CAM_CHN_ID_0)
                sensor.set_pixformat(Sensor.YUV420SP, chn=CAM_CHN_ID_0)
                K._say("[%s] B: CHN0 = %dx%d YUV420SP（给显示）✔" % (TAG, SHOT_W, SHOT_H))
            except Exception as e:
                K._say("[%s] B: ⚠️ CHN0 配置失败（不影响拍照）: %s: %s"
                       % (TAG, type(e).__name__, e))

        # ↓↓↓ 本变体的重点：CHN1 = RGB565（官方 19.snapshot.py 的存图范式）
        sensor.set_framesize(width=SHOT_W, height=SHOT_H, chn=CAM_CHN_ID_1)
        sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)
        K._say("[%s] B: CHN1 = %dx%d RGB565（拍照）✔" % (TAG, SHOT_W, SHOT_H))

        sensor.set_framesize(width=IMG_SIZE, height=IMG_SIZE, chn=CAM_CHN_ID_2)
        sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_2)
        K._say("[%s] B: CHN2 = %dx%d RGBP888（给 AI）✔" % (TAG, IMG_SIZE, IMG_SIZE))

        try:
            Display.init(Display.VIRT, SHOT_W, SHOT_H, to_ide=True)
            K._say("[%s] B: Display.init(VIRT %dx%d, to_ide=True) ✔" % (TAG, SHOT_W, SHOT_H))
        except Exception as e:
            K._say("[%s] B: ⚠️ Display.init 失败（不影响拍照）: %s: %s"
                   % (TAG, type(e).__name__, e))

        if USE_CHN0:
            try:
                info = sensor.bind_info(x=0, y=0, chn=CAM_CHN_ID_0)
                Display.bind_layer(**info, layer=Display.LAYER_VIDEO1)
                K._say("[%s] B: CHN0 已绑 LAYER_VIDEO1（IDE 预览）✔" % TAG)
            except Exception as e:
                K._say("[%s] B: ⚠️ bind_layer 失败（不影响拍照）: %s: %s"
                       % (TAG, type(e).__name__, e))

        MediaManager.init()
        sensor.run()
        K._say("[%s] B: sensor.run() ✔（mem_free=%s）" % (TAG, K._mem_free()))

        q, raw = shot_encode(lambda: sensor.snapshot(chn=CAM_CHN_ID_1), "B-CHN1")
        # tag 直接当服务端的 `source` 用 → 落盘名 uploads/edge_p13-B-q60_<微秒>.jpg
        deliver("p13-B-q%d" % (q or 0), raw)

        if RUN_AI_CHECK:
            ai_check(lambda: sensor.snapshot(chn=CAM_CHN_ID_2))
    except Exception as e:
        K._say("[%s] ✘ 变体 B 整体失败: %s: %s" % (TAG, type(e).__name__, e))
    finally:
        teardown_raw(sensor)


# ------------------------------------------------------------------
# 变体 A：保留 PipeLine，在 create() 之后自己补配 CHN1
# ------------------------------------------------------------------
def variant_a():
    K._say("")
    K._say("[%s] ======== 变体 A：PipeLine + 自己补配 CHN1 ========" % TAG)
    pl = None
    try:
        pl = PipeLine(rgb888p_size=[IMG_SIZE, IMG_SIZE], display_mode=K.DISPLAY_MODE)
        pl.create()
        K._say("[%s] A: pl.create() ✔（mem_free=%s）" % (TAG, K._mem_free()))

        # ↓↓↓ 本变体的核心：create() 之后**自己**配 CHN1
        #     （官方 19.snapshot.py 就是这么配的；P12 只试过"传参给 create()"，那条 TypeError）
        cfg_ok = True
        try:
            pl.sensor.set_framesize(width=SHOT_W, height=SHOT_H, chn=CAM_CHN_ID_1)
            K._say("[%s] A: set_framesize(chn=1, %dx%d) ✔" % (TAG, SHOT_W, SHOT_H))
        except Exception as e:
            cfg_ok = False
            K._say("[%s] A: ✘ run 之后 set_framesize(chn=1) 失败: %s: %s"
                   % (TAG, type(e).__name__, e))
        try:
            pl.sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)
            K._say("[%s] A: set_pixformat(RGB565, chn=1) ✔" % TAG)
        except Exception as e:
            cfg_ok = False
            K._say("[%s] A: ✘ run 之后 set_pixformat(chn=1) 失败: %s: %s"
                   % (TAG, type(e).__name__, e))

        if cfg_ok:
            K._say("[%s] A: 两个配置调用都**没报错** —— 但「没报错」不等于生效，"
                   "看下面取帧出来的 format" % TAG)
            q, raw = shot_encode(lambda: pl.sensor.snapshot(chn=CAM_CHN_ID_1), "A-CHN1")
            deliver("p13-A-q%d" % (q or 0), raw)
        else:
            K._say("[%s] A: 配置本身失败 ⇒ 路线 A 判死（改动需换成方案 B）" % TAG)

        if RUN_AI_CHECK:
            ai_check(lambda: pl.sensor.snapshot(chn=CAM_CHN_ID_2))
    except Exception as e:
        K._say("[%s] ✘ 变体 A 整体失败: %s: %s" % (TAG, type(e).__name__, e))
    finally:
        teardown_pipeline(pl)


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
print("=" * 60)
print("[%s] 抓拍【拍清楚】路线实测   ONLY=%s" % (TAG, ONLY))
print("   送图目标  : %s" % K.IMAGE_URL)
print("   容器 uid  : %s   （跑完服务端按它精确删记录）" % UID)
print("   分辨率    : CHN 拍照 %dx%d / AI %dx%d" % (SHOT_W, SHOT_H, IMG_SIZE, IMG_SIZE))
print("   质量阶梯  : %s   上限 raw=%d / b64=%d"
      % (QUALITY_LADDER, K.IMAGE_MAX_RAW, K.IMAGE_MAX_B64))
print("   ⚠️ 全程不调用 to_rgb888 / to_rgb565（对 CHN0 活帧调它必掉电）")
print("   ⚠️ 验收标准是「PC 上解码肉眼看」，**不是**「出没出字节、多少字节」")
print("=" * 60)

WIFI_OK = wifi_up()
if WIFI_OK and POST_IMAGES:
    make_record()
elif POST_IMAGES:
    K._say("[%s] ⚠️ WiFi 没连上 —— 图传不回来，只能靠 /sdcard/p13-*.jpg 备份" % TAG)

if "B" in ONLY:
    variant_b()
if "A" in ONLY:
    variant_a()

print("")
print("=" * 60)
print("[%s] 跑完。下一步**在 PC 上做**（不是板上）：" % TAG)
print("   1) 看 Python_Backend/uploads/edge_p13-B-*.jpg 与 edge_p13-A-*.jpg")
print("      （文件名里的 B/A 就是变体，q60/q40 是质量档 —— 一一对应，不会认错）")
print("   2) **解码肉眼看**：能看出正常颜色和场景 = 这条路线成立；绿/品红 = 还是老毛病")
print("   3) 板子**断电重上电**一次，再跑别的脚本")
print("   4) 清理：按 uid=%s 删掉那条容器记录" % UID)
print("=" * 60)
