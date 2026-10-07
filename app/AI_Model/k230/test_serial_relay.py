# -*- coding: utf-8 -*-
"""serial_relay.py 的离线测试（PC 跑，不需要板子、不需要 Flask）。

    cd app/AI_Model/k230 && D:\\software\\Python\\Python311\\python.exe test_serial_relay.py

覆盖：
    [1] 帧解析：@@R / @@B / @@D / @@E 正常还原，非 @@ 行一律忽略
    [2] label 放最后一位 ⇒ 标签含空格也不散架
    [3] 坏帧必须被拒：缺块 / 校验和不对 / 长度不符 / 不是 JPEG / uid 不匹配
    [4] 帧序异常：@@D 先于 @@B、上一张没收完又来 @@B
    [5] 置信度越界（0~1 那种"忘了乘 100"的经典 bug）要被拦下
    [6] ★ 真打一次：本地起一个 HTTP 服务 + 一个 TCP 端口，验证
        POST /api/edge/disease 的 body、以及 EIMG1 头 + 原始 JPEG 逐字节正确
        —— 服务端代码一行没改，所以这里能通过就说明串口网关真的能顶替板子。
"""
import base64
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_relay as R                       # noqa: E402

PASS = [0]
FAIL = [0]


def check(ok, label, extra=''):
    if ok:
        PASS[0] += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL[0] += 1
        print("  [FAIL] %s %s" % (label, extra))


def board_lines(uid, label, conf=88.5, manual=1, jpeg=None, chunk=512,
                sum_override=None, nchunks_override=None, nbytes_override=None,
                drop_chunk=None, bad_b64=False):
    """按板端 k230_classify.py::serial_send_image 的格式造一段串口输出。"""
    out = ["@@R %s %.1f %d %s" % (uid, conf, manual, label)]
    if jpeg is None:
        return out
    b64 = base64.b64encode(jpeg).decode()
    chunks = [b64[i:i + chunk] for i in range(0, len(b64), chunk)]
    # ⚠️ 先算块数（用完整块数声明），再按需要抽掉一块 —— 这样"缺块"才是真缺块
    nch = len(chunks) if nchunks_override is None else nchunks_override
    if drop_chunk is not None:
        chunks = chunks[:drop_chunk] + chunks[drop_chunk + 1:]
    nbytes = len(jpeg) if nbytes_override is None else nbytes_override
    out.append("@@B %s %d %d" % (uid, nbytes, nch))
    for i, c in enumerate(chunks):
        if bad_b64 and i == 0:
            c = c[:-1] + ('*' if c[-1] != '*' else '!')
        out.append("@@D %d %s" % (i, c))
    s = sum(jpeg) & 0xFF if sum_override is None else sum_override
    out.append("@@E %s %d" % (uid, s))
    return out


# 一张"真"JPEG 头 + 伪数据，长度够大以产生多块
JPEG = b'\xff\xd8' + bytes((i * 7 + 13) & 0xFF for i in range(3000)) + b'\xff\xd9'

print("== [1] 正常帧：结论 + 图片逐字节还原 ==")
asm = R.FrameAssembler()
events = []
for ln in ["[labels] 加载 38 类", "Network (rt-smart) is always active.",
           "[infer] Corn___healthy 61.3%", "[shot] ① snapshot 中…"] + \
          board_lines("k230-canmv-1-1", "Corn___healthy", conf=61.3, manual=1,
                      jpeg=JPEG):
    events += asm.feed(ln)
kinds = [e[0] for e in events]
check(kinds == ["result", "image"], "只产出 result + image 两个事件（其余日志被忽略）", str(kinds))
_res = [e[1] for e in events if e[0] == "result"][0]
_img = [e[1] for e in events if e[0] == "image"][0]
check(_res["label"] == "Corn___healthy" and abs(_res["conf"] - 61.3) < 0.01 and _res["manual"],
      "结论字段解析正确", str(_res))
check(_img["jpeg"] == JPEG, "图片**逐字节**还原", "%d 字节" % len(_img["jpeg"]))
check(_img["uid"] == _res["uid"] == "k230-canmv-1-1", "结论与图片 uid 一致")
check(asm.stats == {"result": 1, "image": 1, "drop": 0}, "统计正确", str(asm.stats))

print("\n== [2] label 里带空格也不散架 ==")
asm = R.FrameAssembler()
ev = asm.feed("@@R k230-canmv-1-2 55.0 0 Potato Late blight (leaf)")
check(ev and ev[0][0] == "result" and ev[0][1]["label"] == "Potato Late blight (leaf)",
      "split(' ', 4) 把 label 完整保留", repr(ev[0][1]["label"]) if ev else "")

print("\n== [3] 坏帧必须被拒 ==")
cases = [
    ("缺了中间一块", dict(drop_chunk=1)),
    ("校验和不对", dict(sum_override=999)),
    ("声明的 nbytes 与实际不符", dict(nbytes_override=123)),
    ("声明的块数比实际多", dict(nchunks_override=999)),
    ("base64 里混了非法字符", dict(bad_b64=True)),
]
for name, kw in cases:
    asm = R.FrameAssembler()
    ev = []
    for ln in board_lines("k230-canmv-1-3", "X", jpeg=JPEG, **kw):
        ev += asm.feed(ln)
    kinds = [e[0] for e in ev]
    check("image" not in kinds and any(e[0] == "warn" for e in ev),
          "%s -> 丢弃并告警" % name, str(kinds))
    check(asm.stats["image"] == 0 and asm.stats["drop"] == 1,
          "  ↳ 计数器：图 0 / 丢 1", str(asm.stats))

# 不是 JPEG
asm = R.FrameAssembler()
ev = []
for ln in board_lines("k230-canmv-1-4", "X", jpeg=b'\x00\x01' * 600):
    ev += asm.feed(ln)
check("image" not in [e[0] for e in ev], "内容不是 JPEG -> 丢弃")

# uid 结尾对不上
asm = R.FrameAssembler()
ev = []
for ln in board_lines("k230-canmv-1-5", "X", jpeg=JPEG):
    if ln.startswith("@@E "):
        ln = "@@E k230-canmv-OTHER 0"
    ev += asm.feed(ln)
check("image" not in [e[0] for e in ev], "@@E 的 uid 与 @@B 不一致 -> 丢弃")

print("\n== [4] 帧序异常 ==")
asm = R.FrameAssembler()
ev = asm.feed("@@D 0 ZGF0YQ==")
check(any(e[0] == "warn" for e in ev), "@@D 出现在 @@B 之前 -> 告警且不崩")
asm = R.FrameAssembler()
for ln in board_lines("k230-canmv-1-6", "X", jpeg=JPEG):
    if not ln.startswith("@@E"):
        asm.feed(ln)
asm.feed("@@B k230-canmv-1-7 100 1")          # 上一张没收完又来一张
check(asm.stats["drop"] == 1 and asm.img["uid"] == "k230-canmv-1-7",
      "上一张没收完又来 @@B -> 丢旧收新（板子复位后不会卡住）", str(asm.stats))

print("\n== [5] 置信度越界（经典的『忘了 x100』）==")
asm = R.FrameAssembler()
ev = asm.feed("@@R k230-canmv-1-8 0.873 0 Blueberry___healthy")
check(any(e[0] == "warn" for e in ev),
      "0.873 直接告警（服务端明确拒收 <=1.0）")
check(any(e[0] == "result" for e in ev),
      "  ↳ 但**照样转发**：权威判定在服务端，relay 不擅自丢数据")
asm = R.FrameAssembler()
ev = asm.feed("@@R k230-canmv-1-9 88.5 0 OK")
check(not any(e[0] == "warn" for e in ev), "正常值 88.5 不告警")

print("\n== [6] 真打一次：本地 HTTP + 本地 TCP，验证发出去的东西 ==")


class _Handler(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n)
        _Handler.got.append((self.path, dict(self.headers), body))
        payload = json.dumps({'status': 'ok', 'accepted': True,
                              'verdict': 'confirmed', 'record_id': 4242}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


httpd = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
http_port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

raw_srv = socket.socket()
raw_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
raw_srv.bind(('127.0.0.1', 0))
raw_srv.listen(1)
raw_port = raw_srv.getsockname()[1]
raw_got = []


def _raw_worker():
    c, _ = raw_srv.accept()
    try:
        buf = b''
        while b'\n' not in buf:
            d = c.recv(256)
            if not d:
                break
            buf += d
        head, _, rest = buf.partition(b'\n')
        parts = head.decode().split()
        n = int(parts[2])
        body = bytearray(rest)
        while len(body) < n:
            d = c.recv(8192)
            if not d:
                break
            body += d
        raw_got.append((parts, bytes(body)))
    finally:
        c.close()


threading.Thread(target=_raw_worker, daemon=True).start()

sender = R.Sender(server="http://127.0.0.1:%d" % http_port,
                  raw_host="127.0.0.1", raw_port=raw_port, timeout=5.0)
asm = R.FrameAssembler()
UID = "k230-canmv-2-1"
for ln in board_lines(UID, "Potato___Late_blight", conf=91.2, manual=0, jpeg=JPEG):
    for kind, payload in asm.feed(ln):
        if kind == "result":
            sender.post_result(payload)
        elif kind == "image":
            sender.push_image(payload)

check(len(_Handler.got) == 1, "服务端收到 1 个结论请求", str(len(_Handler.got)))
if _Handler.got:
    path, hdrs, body = _Handler.got[0]
    j = json.loads(body.decode())
    check(path == "/api/edge/disease", "打到 /api/edge/disease", path)
    check(j.get("disease") == "Potato___Late_blight", "disease 正确", j.get("disease"))
    check(abs(float(j.get("confidence")) - 91.2) < 0.01, "confidence 是 0~100", j.get("confidence"))
    check(j.get("source") == "k230-canmv" and j.get("client_uid") == UID,
          "source / client_uid 正确", str(j.get("client_uid")))
    check(j.get("manual") is False, "manual=false（自动巡检轮次，服务端照常节流去重）")
    check(hdrs.get("Content-Type", "").startswith("application/json"), "Content-Type 正确")

import time as _t                                    # noqa: E402
for _ in range(50):
    if raw_got:
        break
    _t.sleep(0.05)
check(len(raw_got) == 1, "图片端口收到 1 条连接", str(len(raw_got)))
if raw_got:
    parts, body = raw_got[0]
    check(parts[0] == "EIMG1" and parts[1] == UID
          and int(parts[2]) == len(JPEG) and parts[3] == "k230-canmv",
          "头行 EIMG1 <uid> <nbytes> <source> 正确", " ".join(parts))
    check(body == JPEG, "原始 JPEG 逐字节一致", "%d 字节" % len(body))
check(sender.n_ok == 1 and sender.n_img == 1 and sender.n_fail == 0,
      "计数：结论 1 / 图 1 / 失败 0",
      "ok=%d img=%d fail=%d" % (sender.n_ok, sender.n_img, sender.n_fail))

print("\n== [7] dry-run 不碰网络 ==")
s2 = R.Sender(server="http://127.0.0.1:1", raw_host="127.0.0.1", raw_port=1,
              dry_run=True)
asm2 = R.FrameAssembler()
for ln in board_lines("k230-canmv-3-1", "X", jpeg=JPEG):
    for kind, payload in asm2.feed(ln):
        if kind == "result":
            s2.post_result(payload)
        elif kind == "image":
            s2.push_image(payload)
check(s2.n_fail == 0, "dry-run 下不会因为连不上而报失败")

httpd.shutdown()
raw_srv.close()

print("\n" + "=" * 50)
print("PASS %d / FAIL %d" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
