# -*- coding: utf-8 -*-
# 探针 P5：完整组合推理 1 轮后，按 0.5s / 2s / 10s / 30s / 60s 递增等待再探网络
# 问题：网络是"推理后立刻死、一直死"，还是"死一阵子自己缓过来"？
#       能缓过来 ⇒ 指向资源回收/驱动去初始化类问题（有救：等一等再发）；
#       一直死 ⇒ 更硬的固件级问题。
# 判读：后面几档（30s/60s）开始 ✔ ⇒ 有救，正式脚本可加"推理后延迟上报"；
#       全 ✘ ⇒ 无自愈，P5 排除"时间"这个变量。

import time, network, gc, socket
from libs.PipeLine import PipeLine
import nncase_runtime as nn

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
WAITS = (0.5, 2, 10, 30, 60)             # 秒，累计约 103 秒
TAG = "P5"

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


print("=" * 52)
print(" 探针 P5：推理 1 轮 → 等待 0.5/2/10/30/60s 各探一次网络")
print("=" * 52)
wlan = wifi_connect()

pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
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
print("[%s] 推理 ✔ %.2fs mem_free=%d" % (TAG, elapsed(t0), gc.mem_free()))
t_infer = tms()

for w in WAITS:
    # 等到"推理后第 w 秒"这个时间点再探（等待是累计对齐的）
    while elapsed(t_infer) < w:
        time.sleep_ms(100)
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    print("[%s] 推理后 %5.1fs 探测 %s %.2fs  %s"
          % (TAG, elapsed(t_infer), "✔" if ok else "✘", dt, head))

# 收尾：销毁采集链路 —— 否则下一个采集类探针会撞 "OSError: sensor(2) is already inited"
try:
    pl.destroy()
    print("[%s] PipeLine 已销毁（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    print("[%s] PipeLine 销毁失败（照旧按 reset 再跑下一个）: %s" % (TAG, e))

print("-" * 52)
print("[%s] 结论 = 看上面五档：出现 ✔ 的最早一档就是『网络恢复时间』；"
      "全 ✘ = 不自愈，『时间』变量排除" % TAG)
