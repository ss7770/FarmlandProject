# -*- coding: utf-8 -*-
# 探针 P9：**完整 send_round 复刻（含图片）** —— 直接调用正式脚本的真函数，不复制业务代码
#
# 为什么需要 P9：
#   P0~P4 + P7 已经把「推理 / 相机 / KPU / 轮前四步」全部洗清（全 ✔，100+ 次网络调用 0 失败）。
#   P7 唯一**没有复刻**的，就是 send_round() 里的图片那两步：
#       ⑤ grab_shot_jpeg()  —— snapshot(CHN0) + 缩放 + 编码阶梯
#       ⑥ send_image_raw()  —— 连 5001 推 EIMG1 + 原始 JPEG
#   而正式脚本的日志最后一行却停在 report 的 connect 上。这个矛盾只有两种解释：
#       (a) 死点其实在 report **之后**（抓拍 / 推图），硬复位只是丢掉了输出缓冲区的尾巴；
#       (b) 死点确实在 report 的 connect，那差异只剩「启动期多做的那几件事」。
#   P9 就是为了把这两者分开。
#
# 本探针**故意不重写业务代码**：
#   · `import k230_classify as K`，直接调用 K.report / K.grab_shot_jpeg / K.send_image_raw / K.send_round
#     （只在外面套一层计时打点：前后各一行 + mem_free）。
#     这样测的就是**真代码**，不会再有"探针和正式脚本不一样"的扯皮。
#   · 全部输出走 say()（每行停 30ms）—— 板子复位会丢掉 USB 缓冲区里没刷出去的尾巴，
#     加了这一下之后「最后一行 = 真正死点」才可信。
#
# 用法：默认三个开关全开跑一次。
#   · 死了   => 逐个把 DO_REPORT / DO_SHOT / DO_RAW_TCP 改成 False，一次锁死是哪一段；
#   · 没死   => 图片链路无罪，回正式脚本查「启动期」：load_labels / flush_pending / 启动时那次 selftest。
#
# ⚠️ DO_REPORT=True 会**真的写入库记录**：disease="P9probe"，未确诊的 30 分钟后服务端自动清。
# ⚠️ 跑之前确认 Flask 在跑（/api/edge/status 里 raw_image=True），否则推图必失败（且是"应该失败"）。
# ⚠️ 跑这个之前先断电重上电一次，别带上一轮 probe 的烂状态。

import time, network, gc, socket, sys

# ---------------- 开关（排查用，一次只动一个）----------------
DO_REPORT = True          # a) 结论 POST（只发不读）—— P7 已证清白，这里当「同轮前置」
DO_SHOT = True            # b) 抓拍 grab_shot_jpeg()（snapshot CHN0 + 缩放 + 编码阶梯）
DO_RAW_TCP = True         # c) 推图 send_image_raw()（连 5001 推 EIMG1 + 原始 JPEG）
ROUNDS = 2                # 复刻几轮（正式脚本死在第 1 轮；改 3 可看累积效应）
WATCH_SEC = 30            # 全部轮次跑完后，再盯网络多久
SHOT_LADDER = "script"    # "script" = 正式脚本原梯度 (0.5,0.25,1.0)x(q60,40,25)
                          # "small"  = 从最小档起步 (0.25,0.5,1.0)x(q40,25) —— 堆只有 3.8MB 时更稳

# ---------------- 环境 ----------------
HOST, PORT = "192.168.57.97", 5000
PATH_PING = "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
SOURCE_ID = "k230-canmv"
TAG = "P9"


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def say(msg):
    """打印 + 停 30ms —— 让 USB-CDC 缓冲区有机会刷出去。
    板子复位会丢掉没刷出去的尾巴，加了这一下之后『最后一行 = 真正死点』才可信。"""
    print(msg)
    time.sleep_ms(30)


def mem():
    try:
        return gc.mem_free()
    except Exception:
        return -1


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        say("[%s] wifi 连接 %s ..." % (TAG, SSID))
        wlan.connect(SSID, PASS)
        t0 = tms()
        while not wlan.isconnected():
            if time.ticks_diff(tms(), t0) > 15000:
                raise RuntimeError("wifi 15 秒没连上")
            time.sleep_ms(200)
    say("[%s] wifi OK IP=%s" % (TAG, wlan.ifconfig()[0]))
    return wlan


def _pull(s):
    try:
        return s.recv(256)
    except OSError:
        return b""


def _req(method, path, body=b""):
    h = ("%s %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n"
         % (method, path, HOST))
    if body:
        h += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(body)
    return (h + "\r\n").encode() + body


def net_get(path, budget_ms=4000, settle_ms=150):
    """轮后探针：connect → send → 先睡 → setblocking(False) 轮询读头（打 /ping，不产生记录）"""
    t0 = tms()
    s = socket.socket()
    try:
        s.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
        s.send(_req("GET", path))
        time.sleep_ms(settle_ms)
        s.setblocking(False)
        buf = b""
        t1 = tms()
        while b"\r\n\r\n" not in buf and time.ticks_diff(tms(), t1) < budget_ms:
            buf += _pull(s)
            time.sleep_ms(2)
        head = buf.split(b"\r\n", 1)[0][:36]
        return (b" 200 " in head), elapsed(t0), head
    finally:
        try:
            s.close()
        except Exception:
            pass


def preprocess(frame):
    sh = frame.shape
    if len(sh) == 4:
        c, h, w = sh[1], sh[2], sh[3]
    else:
        c, h, w = sh[0], sh[1], sh[2]
    x = frame.reshape((c, h * w)).transpose().copy().reshape((h, w, c))
    # ⚠️ 2026-10-02：kmodel 已改成 uint8 口径（/255 烤进模型），这里**不能**再除 255
    return x.reshape((1, h, w, c))


def top1(row):
    best = 0
    for i in range(1, len(row)):
        if row[i] > row[best]:
            best = i
    return best


say("=" * 52)
say(" 探针 P9：完整 send_round 复刻（开关 ①结论=%s ②抓拍=%s ③推图=%s，梯度=%s）"
    % (DO_REPORT, DO_SHOT, DO_RAW_TCP, SHOT_LADDER))
say(" ★ 本探针直接调用 k230_classify 的真函数（不复制代码）—— 测的就是正式脚本那条路")
say("=" * 52)

# ---------- 导入正式脚本（必须和本文件在同一目录，一般是 /sdcard）----------
# 探针常放在 /sdcard/probes/ 而 k230_classify.py 在 /sdcard/，所以把这几处都塞进 sys.path，
# 免得因为"找不到模块"白跑一趟。
for _p in ("/sdcard", "/sdcard/probes", ".", ".."):
    try:
        if _p not in sys.path:
            sys.path.append(_p)
    except Exception:
        pass
try:
    import k230_classify as K
except ImportError as e:
    say("[%s] ✘ 导入 k230_classify 失败：%s" % (TAG, e))
    say("[%s]    把 k230_classify.py 和本探针放同一个目录（/sdcard）再跑" % TAG)
    raise

# 钉死成 v10「HTTP 老路」：P9 要测的就是这条路
K.TRANSPORT = "http"
K.SELFTEST_SOCK = False        # P9 自己不做 selftest（P7 已验过）
K.SEND_IMAGE = True
K.IMAGE_TRANSPORT = "raw"      # 走原始 TCP，不走 base64 退路
if SHOT_LADDER == "small":
    # ⚠️ v12：缩放阶梯已整体删除（K.IMAGE_SCALE_LADDER 不存在了，设了会 AttributeError）。
    #    抓拍不再缩放，只能靠质量降级。
    K.IMAGE_QUALITY_LADDER = (40, 25)
say("[%s] 已钉：TRANSPORT=http / IMAGE_TRANSPORT=raw / 质量梯度=%s"
    % (TAG, K.IMAGE_QUALITY_LADDER))

wlan = wifi_connect()

# ---------- 采集 + 推理（和正式脚本一致）----------
say("[%s] 初始化采集+推理（和正式脚本一致）..." % TAG)
from libs.PipeLine import PipeLine
import nncase_runtime as nn

pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
K._pl = pl                     # ★ 正式脚本的 _grab_shot_raw() 靠这个全局拿 sensor
kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())
say("[%s] 就绪（mem_free=%d）" % (TAG, mem()))

# ---------- 给正式脚本的三个函数套上"计时打点" ----------
_orig_report = K.report
_orig_grab = K.grab_shot_jpeg
_orig_raw = K.send_image_raw
_orig_send_round = K.send_round


def _report_logged(disease, conf100, manual=False, uid=None):
    if not DO_REPORT:
        say("[%s] a) 开关关着：跳过结论上报（只造一个假的返回值）" % TAG)
        return {'accepted': True, 'verdict': 'skipped', 'record_id': None,
                'client_uid': uid}
    say("[%s] a) 结论 POST 前 mem_free=%d" % (TAG, mem()))
    t0 = tms()
    r = _orig_report(disease, conf100, manual=manual, uid=uid)
    say("[%s] a) 结论返回 %s（%.2fs，mem_free=%d）"
        % (TAG, "dict=已发出" if r is not None else "None=没发出去", elapsed(t0), mem()))
    return r


def _grab_logged():
    if not DO_SHOT:
        say("[%s] b) 开关关着：跳过抓拍（返回 None）" % TAG)
        return None
    say("[%s] b) 抓拍开始  mem_free=%d（★ 抓拍峰值：FHD 的 RGB888 要 6.2MB）" % (TAG, mem()))
    t0 = tms()
    j = _orig_grab()
    say("[%s] b) 抓拍结束 %s  用了 %.2fs  mem_free=%d"
        % (TAG, ("%d 字节 JPEG" % len(j)) if j else "没拿到图", elapsed(t0), mem()))
    return j


def _raw_logged(uid, jpeg):
    if not DO_RAW_TCP:
        say("[%s] c) 开关关着：跳过推图（记录仍在，只是没图）" % TAG)
        return False
    say("[%s] c) 推图开始 -> %s:%d  JPEG=%s  mem_free=%d"
        % (TAG, K.RAW_HOST, K.RAW_PORT, len(jpeg) if jpeg else None, mem()))
    t0 = tms()
    r = _orig_raw(uid, jpeg)
    say("[%s] c) 推图结束 %s  用了 %.2fs  mem_free=%d"
        % (TAG, "✔ 已推送" if r else "✘ 失败", elapsed(t0), mem()))
    return r


K.report = _report_logged
K.grab_shot_jpeg = _grab_logged
K.send_image_raw = _raw_logged

# ---------- 主流程 ----------
for r in range(1, ROUNDS + 1):
    say("\n[%s] ===== 复刻第 %d/%d 轮 =====" % (TAG, r, ROUNDS))

    # ① 轮前取指令（正式脚本每 2 秒一次；这里每轮一次，只为"同轮前置"）
    # ⚠️ v15（2026-10-02）：「立即抓拍」整条链已删除 ⇒ 这个地址现在**必然 404**。
    #    这一行保留只为复刻当年的时序；看到 ✘ 是预期结果，**不是故障**。
    say("[%s]   ① GET /api/edge/command（v15 起已删除，期望 404）" % TAG)
    try:
        ok, dt, head = net_get("/api/edge/command", budget_ms=2000, settle_ms=150)
        say("[%s]   ① %s %.2fs  %s" % (TAG, "✔" if ok else "✘（v15 后的期望值）", dt, head))
    except Exception as e:
        say("[%s]   ① 异常 %s: %s" % (TAG, type(e).__name__, e))

    # 推理（和正式脚本逐行一致）
    frame = pl.get_frame()
    t0 = tms()
    kpu.set_input_tensor(0, nn.from_numpy(preprocess(frame)))
    kpu.run()
    row = kpu.get_output_tensor(0).to_numpy()[0]
    b = top1(row)
    conf = float(row[b]) * 100.0
    say("[%s]   推理 ✔ %.2fs 类%d (%.1f%%) mem_free=%d" % (TAG, elapsed(t0), b, conf, mem()))

    # ② ★ 正式脚本的 send_round() —— 结论 → 抓拍 → 推图，全在真函数里跑
    say("[%s]   ② 调 K.send_round(\"P9probe\", %.1f, manual=True)  ← 真函数，下面全是它的原生输出"
        % (TAG, conf))
    t0 = tms()
    try:
        ret = _orig_send_round("P9probe", conf, manual=True)
        say("[%s]   ② send_round 返回 %s，共 %.2fs" % (TAG, ret, elapsed(t0)))
    except Exception as e:
        say("[%s]   ② send_round 抛异常 %s: %s" % (TAG, type(e).__name__, e))

    # 轮后立刻探 3 次：这一轮到底有没有把网络弄死，立刻见分晓
    for k in range(3):
        try:
            ok, dt, head = net_get(PATH_PING)
        except Exception as e:
            ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
        say("[%s]   轮后探测 %d/3 %s %.2fs  %s"
            % (TAG, k + 1, "✔" if ok else "✘", dt, head))
        time.sleep_ms(1000)

# 最后长盯一段
say("\n[%s] 全部轮次结束，继续盯网络 %d 秒" % (TAG, WATCH_SEC))
t_end = tms() + WATCH_SEC * 1000
n = 0
while time.ticks_diff(tms(), t_end) < 0:
    n += 1
    try:
        ok, dt, head = net_get(PATH_PING)
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    say("[%s]   长盯 %02d %s %.2fs  %s" % (TAG, n, "✔" if ok else "✘", dt, head))
    time.sleep_ms(2000)

try:
    pl.destroy()
    say("[%s] PipeLine 已销毁（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    say("[%s] PipeLine 销毁失败（照旧按 reset）: %s" % (TAG, e))

say("-" * 52)
say("[%s] 判读：" % TAG)
say("[%s]   · 崩在 b) 抓拍   => 病根是**抓拍/内存**（手上只有 ~3.8MB，FHD RGB888 要 6.2MB），"
    "跟网络无关；把 SHOT_LADDER 改 \"small\" 再跑" % TAG)
say("[%s]   · 崩在 c) 推图   => 是**大包上行**的问题；先把图压小（160x120 q50）再试" % TAG)
say("[%s]   · 全 ✔           => 图片链路无罪，回正式脚本查启动期：load_labels / flush_pending / 启动时 selftest"
    % TAG)
