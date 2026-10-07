# -*- coding: utf-8 -*-
# 探针 P6：完整组合推理 1 轮后，**主动松手**——del KPU 对象 + gc.collect()，再探网络
# 问题：13 轮排查里"甲方案：推理后松手 + gc"这个空缺从未试过。KPU 对象/推理
#       中间结果被持有时网络会死，松手后就好了？
# 判读：松手后网络 ✔ ⇒ 有救！正式脚本改成"每轮推理完 del + gc"即可保住 WiFi 直连；
#       松手后仍 ✘ ⇒ 最后一个软性方案排除，根因坐实为固件级。

import time, network, gc, socket
from libs.PipeLine import PipeLine
import nncase_runtime as nn

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
ROUNDS, GAP_MS = 10, 2000
TAG = "P6"

REQ = ("GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"
       % (PATH, HOST)).encode()


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("[%s] wifi 连接 %s ..." % (TAG, SSID))
        wlan.connect(SSID, PASS)
        t0 = tms()
        while not wlan.isconnected():
            if time.ticks_diff(tms(), t0) > 15000:
                raise RuntimeError("wifi 15 秒没连上")
            time.sleep_ms(200)
    print("[%s] wifi OK IP=%s" % (TAG, wlan.ifconfig()[0]))
    return wlan


def _pull(s):
    try:
        return s.recv(256)
    except OSError:
        return b""


def net_round():
    t0 = tms()
    s = socket.socket()
    try:
        s.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
        s.send(REQ)
        time.sleep_ms(150)
        s.setblocking(False)
        buf = b""
        t1 = tms()
        while b"\r\n\r\n" not in buf and time.ticks_diff(tms(), t1) < 4000:
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
    return x.reshape((1, h, w, c)) / 255.0


def top1(row):
    best = 0
    for i in range(1, len(row)):
        if row[i] > row[best]:
            best = i
    return best


print("=" * 52)
print(" 探针 P6：推理 → del kpu + gc.collect() → 探网络")
print("=" * 52)
wlan = wifi_connect()

pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
print("[%s] 加载模型（先测『不松手』的基线）..." % TAG)
kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())

frame = pl.get_frame()
t0 = tms()
kpu.set_input_tensor(0, nn.from_numpy(preprocess(frame)))
kpu.run()
row = kpu.get_output_tensor(0).to_numpy()[0]
print("[%s] 推理 ✔ %.2fs top1=类%d mem_free=%d"
      % (TAG, elapsed(t0), top1(row), gc.mem_free()))

# ---- 先探一次：不松手（对照）----
try:
    ok, dt, head = net_round()
except Exception as e:
    ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
print("[%s] 【不松手】探测 %s %.2fs  %s" % (TAG, "✔" if ok else "✘", dt, head))

# ---- 松手：del + gc ----
del row, frame, kpu
gc.collect()
print("[%s] 已 del kpu + gc.collect()，mem_free=%d" % (TAG, gc.mem_free()))

stat = [0, 0]
t0_all = tms()
for i in range(1, ROUNDS + 1):
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    stat[0] += 1
    stat[1] += 1 if ok else 0
    print("[%s] 松手后 %02d/%d 轮 %s %.2fs（%.1fs）  %s"
          % (TAG, i, ROUNDS, "✔" if ok else "✘", dt, elapsed(t0_all), head))
    if i < ROUNDS:
        time.sleep_ms(GAP_MS)

# 收尾：销毁采集链路 —— 否则下一个采集类探针会撞 "OSError: sensor(2) is already inited"
try:
    pl.destroy()
    print("[%s] PipeLine 已销毁（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    print("[%s] PipeLine 销毁失败（照旧按 reset 再跑下一个）: %s" % (TAG, e))

print("-" * 52)
print("[%s] 松手后网络通过 = %d/%d" % (TAG, stat[1], stat[0]))
print("[%s] 结论 = %s" % (
    TAG,
    "松手有效！正式脚本可改『每轮推理完 del+gc』保住 WiFi 直连"
    if stat[1] == stat[0]
    else "松手也救不回 —— 最后一个软性方案排除，根因坐实为固件级，走串口是对的"))
