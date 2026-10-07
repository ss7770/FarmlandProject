# -*- coding: utf-8 -*-
# 探针 P0：纯网络基线（无相机 / 无 KPU / 无 PipeLine）
# 问题：① 什么都不加载时，网络本身通不通？
#       ② 官方 settimeout(0) 写法（3.网络应用/2.Socket通信/socket.py）
#          和我们的 setblocking(False) 轮询写法，哪个能活？（重钉第 8 轮结论）
# 判读：20 轮全 ✔ = 基线健康，继续跑 P1；
#       这里就挂 = 问题根本不在推理，在 WiFi/路由器/固件，后面都不用跑了。

import time, network, gc, socket

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
ROUNDS, GAP_MS = 20, 2000
TAG = "P0"

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
        return b""          # 非阻塞下没数据 / EAGAIN


def net_round(mode):
    """一次完整 连接 -> 发送 -> 读响应头。
    mode: "ours" = 先睡 0.15s + setblocking(False) 轮询（我们 v9.2 的写法）
          "official" = 官方 socket.py 的 settimeout(0) 循环写法"""
    t0 = tms()
    s = socket.socket()
    try:
        s.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
        s.send(REQ)
        buf = b""
        if mode == "official":
            s.settimeout(0)
            t1 = tms()
            while b"\r\n\r\n" not in buf and time.ticks_diff(tms(), t1) < 4000:
                buf += _pull(s)
                time.sleep_ms(2)
        else:
            time.sleep_ms(150)
            s.setblocking(False)
            t1 = tms()
            while b"\r\n\r\n" not in buf and time.ticks_diff(tms(), t1) < 4000:
                buf += _pull(s)
                time.sleep_ms(2)
        head = buf.split(b"\r\n", 1)[0][:36]
        ok = b" 200 " in head
        return ok, elapsed(t0), head
    finally:
        try:
            s.close()
        except Exception:
            pass


print("=" * 52)
print(" 探针 P0：纯网络基线（无相机 / 无 KPU）")
print(" %d 轮，每轮隔 %.0fs；偶数轮=我们的写法，奇数轮=官方写法" % (ROUNDS, GAP_MS / 1000))
print("=" * 52)
wlan = wifi_connect()

stat = {"ours": [0, 0], "official": [0, 0]}      # [成功, 总数]
t_start = tms()
for i in range(1, ROUNDS + 1):
    mode = "ours" if i % 2 == 0 else "official"
    try:
        ok, dt, head = net_round(mode)
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    stat[mode][1] += 1
    stat[mode][0] += 1 if ok else 0
    print("[%s] 第 %02d 轮(%-8s) %s %.2fs  %s"
          % (TAG, i, mode, "✔" if ok else "✘", dt, head))
    if i < ROUNDS:
        time.sleep_ms(GAP_MS)

print("-" * 52)
print("[%s] ours     = %d/%d" % (TAG, stat["ours"][0], stat["ours"][1]))
print("[%s] official = %d/%d" % (TAG, stat["official"][0], stat["official"][1]))
print("[%s] 结论 = %s" % (
    TAG,
    "基线健康（两种写法都通），继续跑 P1"
    if stat["ours"][0] == stat["ours"][1] and stat["official"][0] == stat["official"][1]
    else "基线就有问题！问题不在推理 —— 看 WiFi/路由器/固件，把日志发给 AI"))
