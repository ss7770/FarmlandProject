# -*- coding: utf-8 -*-
# 探针 P7：**真实轮次复刻** —— 把正式脚本比 P3 多出来的那 4 步原样串一遍
#
# 背景（P0~P4 已经把变量排除干净了）：
#   P0 纯网络 ✔ / P1 PipeLine 取帧 ✔ / P2 只跑 KPU ✔ / P3 组合推理后盯网 27/27 ✔
#   / P4 官方采集路径 ✔  =>  「推理后网络必死」作废，相机·media·KPU 都不伤网络。
#   那正式脚本死在哪？就在它比探针**多做的那 4 步**里：
#     ① poll_command()  每 2 秒 GET /api/edge/command（读响应头）
#     ② selftest_socket() 推理后又一次 connect + recv
#     ③ wifi_ready()    推理后调 wlan.isconnected()
#     ④ report()        POST 真实接口 /api/edge/disease（带 JSON body）
#
# 用法：先按默认（4 个开关全 True）跑一次。
#   · 死了  => 逐个把开关改成 False 再跑，一次就能钉出是哪一步；
#   · 没死  => 说明还有别的东西（下一批往"第二轮累积 / load_labels / flush_pending"上找）。
#
# ⚠️ STEP_POST_REAL=True 会**真的写入库记录**（disease/confidence 用的是本次推理结果，
#    client_uid 前缀 k230-canmv-p7-，方便事后按 uid 清理；未确诊的 30 分钟后自动清）。

import time, network, gc, socket, ujson
from libs.PipeLine import PipeLine
import nncase_runtime as nn

# ---------------- 开关（排查用，一次只动一个）----------------
# ⚠️ v15（2026-10-02）：「立即抓拍」整条链已删除 ⇒ 该地址现在必然 404。
#    p7 是当年的定因探针（历史产物），保留原样只作考古；要重跑请把 STEP_POLL 设成 False。
STEP_POLL = True          # ① 轮前 GET /api/edge/command（读响应头；v15 起必然 404）
STEP_SELFTEST = True      # ② 推理后 connect + 非阻塞 recv 一次
STEP_WIFI_READY = True    # ③ 推理后调 wlan.isconnected()
STEP_POST_REAL = True     # ④ POST 真实接口（会入库！）；False = 改打 /ping 作对照
ROUNDS = 2                # 复刻几轮（正式脚本死在第 1 轮；改 3 可看累积效应）
WATCH_SEC = 30            # 所有轮次跑完后，再盯网络多久

# ---------------- 环境 ----------------
HOST, PORT = "192.168.57.97", 5000
PATH_PING, PATH_CMD, PATH_DISEASE = "/api/edge/ping", "/api/edge/command", "/api/edge/disease"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
SOURCE_ID = "k230-canmv"
TAG = "P7"


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def say(msg):
    """打印 + 停 30ms —— 让 USB-CDC 缓冲区有机会刷出去。
    板子复位会丢掉没刷出去的尾巴，加了这一下之后『最后一行 = 真正死点』才可信。"""
    print(msg)
    time.sleep_ms(30)


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
    """我们的写法：connect → send → 先睡 → setblocking(False) 轮询读头"""
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


def post_send_only(path, body):
    """**只发不读** + 每步打点（正式 report() 走的是同一条路）。
    返回 True=发完即关，False=没发出去。"""
    req = _req("POST", path, body)
    say("[%s]   ④-a 请求已拼好 %dB，准备 socket()" % (TAG, len(req)))
    sock = socket.socket()
    say("[%s]   ④-b socket() 建好" % TAG)
    try:
        sock.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
        say("[%s]   ④-c **已连接**" % TAG)
        try:
            sock.sendall(req)
        except AttributeError:
            pos = 0
            while pos < len(req):
                pos += sock.send(req[pos:])
        say("[%s]   ④-d **已发出** %dB" % (TAG, len(req)))
        return True
    finally:
        try:
            sock.close()
            say("[%s]   ④-e 已关闭" % TAG)
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
say(" 探针 P7：真实轮次复刻（开关 ①poll=%s ②selftest=%s ③wifi_ready=%s ④真实POST=%s）"
    % (STEP_POLL, STEP_SELFTEST, STEP_WIFI_READY, STEP_POST_REAL))
say("=" * 52)
wlan = wifi_connect()

say("[%s] 初始化采集+推理（和正式脚本一致）..." % TAG)
pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())
say("[%s] 就绪" % TAG)

for r in range(1, ROUNDS + 1):
    say("\n[%s] ===== 复刻第 %d/%d 轮 =====" % (TAG, r, ROUNDS))

    # ① 轮前取指令（正式脚本每 2 秒一次，这里每轮一次）
    if STEP_POLL:
        say("[%s]   ① GET %s（读响应头）" % (TAG, PATH_CMD))
        try:
            ok, dt, head = net_get(PATH_CMD, budget_ms=2000, settle_ms=150)
            say("[%s]   ① %s %.2fs  %s" % (TAG, "✔" if ok else "✘", dt, head))
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
    say("[%s]   推理 ✔ %.2fs 类%d (%.1f%%) mem_free=%d"
        % (TAG, elapsed(t0), b, conf, gc.mem_free()))

    # ② 推理后再自检一次 socket
    if STEP_SELFTEST:
        say("[%s]   ② 推理后 selftest：connect → 睡 0.35s → setblocking(False) → recv(16)"
            % TAG)
        try:
            s = socket.socket()
            s.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
            say("[%s]   ② 已连接" % TAG)
            time.sleep_ms(350)
            s.setblocking(False)
            say("[%s]   ② 准备 recv…" % TAG)
            got = s.recv(16)
            say("[%s]   ② recv 返回 %r ✔" % (TAG, got))
            s.close()
        except Exception as e:
            say("[%s]   ② 异常 %s: %s" % (TAG, type(e).__name__, e))

    # ③ 推理后问一次 wlan 状态（正式脚本的 wifi_ready）
    if STEP_WIFI_READY:
        try:
            say("[%s]   ③ wlan.isconnected() = %s" % (TAG, wlan.isconnected()))
        except Exception as e:
            say("[%s]   ③ 异常 %s: %s" % (TAG, type(e).__name__, e))

    # ④ 上报
    uid = "%s-p7-%d-%d" % (SOURCE_ID, r, time.ticks_ms())
    if STEP_POST_REAL:
        payload = {"disease": "P7probe", "confidence": round(conf, 1),
                   "source": SOURCE_ID, "timestamp": "p7-probe",
                   "client_uid": uid}
        body = ujson.dumps(payload).encode()
        say("[%s]   ④ POST %s（只发不读，body=%dB）" % (TAG, PATH_DISEASE, len(body)))
        try:
            post_send_only(PATH_DISEASE, body)
        except Exception as e:
            say("[%s]   ④ 异常 %s: %s" % (TAG, type(e).__name__, e))
    else:
        say("[%s]   ④ 开关关着：改打 %s 作对照" % (TAG, PATH_PING))
        try:
            ok, dt, head = net_get(PATH_PING)
            say("[%s]   ④ %s %.2fs  %s" % (TAG, "✔" if ok else "✘", dt, head))
        except Exception as e:
            say("[%s]   ④ 异常 %s: %s" % (TAG, type(e).__name__, e))

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
say("[%s] 结论 = 整轮复刻后网络仍全 ✔ ⇒ 死因不在这 4 步（下一批查第二轮累积 / "
    "load_labels / flush_pending）；若中途就断了，把最后一行发我"
    "（say() 打了 30ms 间隔，最后一行可信）" % TAG)
