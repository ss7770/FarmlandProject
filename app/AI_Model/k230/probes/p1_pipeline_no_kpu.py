# -*- coding: utf-8 -*-
# 探针 P1：PipeLine 起来、只取帧、**不推理**，网络还活不活？
# 问题：PipeLine（media 资源 / 显示层绑定 / CHN0+CHN2 双通道）本身会不会伤网络？
#       P0 过 + 这里挂 => 病灶在 PipeLine/media 层，跟 KPU 无关。
# 判读：取帧 30 秒全正常、网络 10 轮全 ✔ => PipeLine 本身无罪，继续跑 P2。

import time, network, gc, socket
from libs.PipeLine import PipeLine

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
SPIN_SEC, ROUNDS, GAP_MS = 30, 10, 2000
TAG = "P1"

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
    """我们的写法：先睡 0.15s + setblocking(False) 轮询读头"""
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


print("=" * 52)
print(" 探针 P1：PipeLine 只取帧（不推理）+ 网络")
print("=" * 52)
wlan = wifi_connect()

print("[%s] PipeLine 初始化(display_mode=virt) ..." % TAG)
pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
print("[%s] PipeLine 就绪，开始空转取帧 %d 秒（不推理）" % (TAG, SPIN_SEC))

frames = 0
t_end = tms() + SPIN_SEC * 1000
while time.ticks_diff(tms(), t_end) < 0:
    pl.get_frame()
    frames += 1
    time.sleep_ms(100)
print("[%s] 取帧结束，共 %d 帧 ✔（PipeLine 保持活着）" % (TAG, frames))

stat = [0, 0]
t_infer = None                       # 没有推理，从取帧结束起算
t0_all = tms()
for i in range(1, ROUNDS + 1):
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    stat[0] += 1
    stat[1] += 1 if ok else 0
    print("[%s] 第 %02d/%d 轮 %s %.2fs（距取帧结束 %.1fs）  %s"
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
print("[%s] 网络通过 = %d/%d" % (TAG, stat[1], stat[0]))
print("[%s] 结论 = %s" % (
    TAG,
    "PipeLine 本身无罪（只取帧不推理，网络照常），继续跑 P2"
    if stat[1] == stat[0]
    else "PipeLine 起来后网络就坏了 —— 病灶在 media/显示层，把日志发给 AI"))
