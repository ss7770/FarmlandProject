# -*- coding: utf-8 -*-
"""板端 k230_classify.py 的**离线逻辑**测试（在 PC 上跑，不需要板子）。

    cd app/AI_Model/k230 && D:\\software\\Python\\Python311\\python.exe test_logic_offline.py

为什么要有这个：板子上出问题要来回拷文件、看截图，一轮很慢；而大部分改动
（压缩阶梯、上报语义、补传队列、HTTP 通路）其实是**纯逻辑**，先在 PC 上验证
能省掉大量往返。
MicroPython 的硬件模块（network / nncase_runtime / ulab / media.sensor / libs.PipeLine）
全部用桩替代，**socket 用假 socket 按脚本喂响应**，不联网、不碰硬件。

覆盖：
    [1]  模块可导入 & IMAGE_URL 推导 + ★ v15：CMD_* 必须已删除
    [2]  抓拍体积阶梯：超限必须返回 None（宁可没图，也不能让大包把记录带崩）
    [2b] ★ v12：抓拍**零转换**——不得调 to_rgb888/to_rgb565（对 CHN0 活帧调它必掉电），
         且 _to_rgb_scaled / IMAGE_SCALE_LADDER 必须已删除
    [3]  report() 语义：网络异常->None(进补传)，成功->dict，且**不带 image**
    [4]  图片补传单独走 /api/edge/image，且 record_id 为空时跳过
    [5]  补传队列只存结论（2 元组）——防 20×几十KB 常驻堆 OOM
    [6]  队列上限：超出丢最旧
    [7]  ★ v15：「立即抓拍」指令通道必须已删除（反向断言，防加回来）
    [8]  send_round()：结论先行 + 手动标记 manual=True + 随后单独补图
    [9]  被节流(429)：**手动**重试一次、**自动巡检直接跳过**（少一次卡死机会）
    [10] ★ HTTP 通路（v6 核心）：HTTP/1.0 + Content-Length + Connection: close
         + **读完响应头就停，绝不读 body**（这是两次上板卡死的病根）
    [11] 读不到完整响应头时返回 (0,{})，**不抛异常、不装作成功**
    [12] 异常隔离：整轮包 try + 抓拍链路自带兜底
    [13] ★★ v9.1 命门：**不 settimeout** + **每次 recv 前 setblocking(False)** + 时间预算
         （FakeSock 一被调 settimeout 就抛断言；而"没紧邻地重设非阻塞就 recv"
           会被记成违规 —— 因为假 socket 把"sleep 会冲掉 setblocking"这条假设
           也建模进去了，正是 v8.1 卡死的那个坑）
    [13b] ★★ 读不到响应头也要把图补上：结论与图片共用 client_uid
         （第七/八次上板：记录一直在涨、APP 一张图都没有，就是这里被跳过）
    [14] record_id 兜底：走 GET /api/edge/last-record
    [15] 读不到 POST 响应头 ≠ 失败：不重发，改用 GET 兜底拿 id
    [16] 源码约束：不得有 settimeout / uselect；必须有 _set_nonblocking 与三档预算

注意：这里**验证不了**硬件相关的东西（通道、编码 API、真实网络体积、板端 socket 实现），
  那些必须上板跑 k230_probe.py（PROBE="net" 默认档不会碰相机）。
  ⚠️ 假 socket 的 recv 语义是**我们自己写的**，只能证明"逻辑自洽"、
     证明不了真固件会那样工作 —— 第四/五/六/七/九轮都在这上面栽过，别再过度自信。
     但下面两条**不是猜的**，是九轮上板实测的：
       ① 不许 settimeout —— 三个失败版本都调了它，唯一成功的对照实验没调；
       ② 不许纯阻塞 recv —— 第九次上板第 2 次 POST 就是这么永久卡死的。
     v9.1 唯一还带着"假设"成分的是 **"每次 recv 前重设非阻塞就不会卡"**：
     这条由上板跑 `k230_probe.py` 的 **C8** 段来一锤定音（见探针文件头）。
"""
import base64
import json as _J
import os
import sys
import time
import types

# ---------- 造 MicroPython 桩模块 ----------
# 按脚本自身位置定位 k230_classify.py，换电脑/换目录都不用改
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _mk(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class FakeImage:
    """模拟 image.Image（CHN0 抓到的 YUV420SP 活帧）。

    ⚠️ 刻意还原真机语义，**防止把死路改回去**（2026-10-02 P9/P10/P12 实测）：

      1. `to_rgb888()` / `to_rgb565()` **直接抛异常** —— 真机上它们（带参、无参都一样）
         会让板子**直接掉电**（无 Python 回溯、USB 掉线）。所以抓拍链路一旦调用它们，
         这里立刻炸，测试红给你看。
         注：官方源码里 to_rgb888() 只用于**读文件**得到的 image.Image，活帧从不用它；
         P12 H1 证明 CHN0 的活帧**不做任何转换**就能直接 JPEG 编码。
      2. `copy(x_scale=..)` 同样不可用（真机上不报错但出"平铺 2×2 四份 + 花屏"）。
      3. `to_jpeg(quality=)` 返回**合法 JPEG 字节**（带 \xff\xd8 魔数），
         体积随 quality 单调递增，供"体积阶梯"的断言使用。
    """
    def __init__(self, w, h, is_rgb=False):
        self._w, self._h = w, h
        self._rgb = is_rgb

    def width(self):
        return self._w

    def height(self):
        return self._h

    def format(self):
        # 真机 CHN0 的 YUV420SP format 值（P12 H1 实测打出来是 470417443）
        return 470417443

    def to_rgb888(self, *a, **kw):
        raise RuntimeError("to_rgb888() on a live YUV420SP frame powers the board OFF "
                           "(P9/P10 measured) — capture must be zero-conversion")

    to_rgb565 = to_rgb888

    def copy(self, x_scale=1.0, y_scale=1.0, **kw):
        raise TypeError("copy() on YUV420SP is unsupported (simulated)")

    def to_jpeg(self, quality=80):
        # 粗略模拟：像素数 × 质量系数，保证"越小越少"，且是真 JPEG 头
        n = int(self._w * self._h * quality / 100.0 / 16)
        return b'\xff\xd8' + bytes(n) + b'\xff\xd9'

    compress = to_jpeg

    def save(self, path, quality=80):
        with open(path, 'wb') as f:
            f.write(self.to_jpeg(quality))


# --- 基础模块 ---
_mk('network', LAN=lambda *a: None, STA_IF=0, WLAN=lambda *a: None)
_mk('ujson', dumps=_J.dumps, loads=_J.loads)
_mk('ulab')
_mk('ulab.numpy', ndarray=object)
_mk('nncase_runtime', kpu=lambda *a: None, from_numpy=lambda x: x)
_mk('libs')
_mk('libs.PipeLine', PipeLine=lambda **kw: None)

# v14：k230_classify 顶层直接 import media 三件套（弃用 PipeLine 后自己配裸 Sensor）
class _FakeSensor:
    YUV420SP = 1
    RGB565 = 2
    RGBP888 = 3

def _fake_display(*a, **kw):
    pass

_FakeDisplay = type('Display', (), {
    'VIRT': 'VIRT', 'LCD': 'LCD', 'LT9611': 'LT9611',
    'LAYER_VIDEO1': 1, 'LAYER_OSD3': 3,
    'init': staticmethod(_fake_display),
    'bind_layer': staticmethod(_fake_display),
    'deinit': staticmethod(_fake_display),
})
_FakeMediaMgr = type('MediaManager', (), {
    'init': staticmethod(lambda: None),
    'deinit': staticmethod(lambda: None),
})
_mk('media.display', Display=_FakeDisplay)
_mk('media.media', MediaManager=_FakeMediaMgr)

# --- 假 socket（v9.1：**不 settimeout** + 先睡 + setblocking(False) 轮询）---
# 板端 _http() 只做：
#   socket() → connect() → sendall() → sleep(settle) → [setblocking(False) → recv()] × N
# 所以这里只要喂一段"响应头 + 可选 body"的字节流，就能完整驱动它。
SESSION = {'socks': [], 'default_reply': None, 'replies': []}


def _mk_reply(code=200, headers=None, body=b''):
    """拼一段真实形状的 HTTP 响应（头 + body），交给假 socket 逐个字节喂回去。"""
    reason = {200: 'OK', 400: 'Bad Request', 404: 'Not Found',
              429: 'Too Many Requests', 500: 'Internal Server Error'}.get(code, 'OK')
    lines = ['HTTP/1.1 %d %s' % (code, reason),
             'Content-Length: %d' % len(body),
             'Connection: close']
    for k, v in (headers or {}).items():
        lines.append('%s: %s' % (k, v))
    return ('\r\n'.join(lines) + '\r\n\r\n').encode() + body


class FakeSock:
    """假 socket —— 同时扮演"真板子的 socket"和"源码守卫"两个角色。

    ★★ v9.1 的两条硬约束（都来自上板实测，不是推的）：
       ① **绝不能调 `settimeout()`**：一调，这块固件的 recv 就走 `uselect.poll`
          —— 六~八轮全部卡死 / 读回 0 字节的共同点。所以它一被调用就抛断言。
       ② **每次 recv 之前必须 `setblocking(False)`**：
              v8   = `setblocking(False)` → **立刻** recv          → 不卡，但读回 0 字节
              v8.1 = `setblocking(False)` → `sleep(0.25)` → recv   → **卡死**
          两者只差中间那个 sleep ⇒ 高度怀疑 `setblocking` 会被 `time.sleep()` 冲掉。
          这里把这条假设**直接建模进假 socket**：`nb_armed` 只有"紧邻 recv 之前
          调过 setblocking(False)"才为真，而任何一次 `time.sleep()` 都会把它清掉。
          ⇒ 谁把代码改回"只设一次非阻塞"，[13] 段的违规计数立刻非 0。
       ③ 非阻塞下"此刻没数据"→ 返回**空字节**（K230 实测行为，不是 EAGAIN）：
          它和"对端关闭"同形，所以板端**不能**把空当 EOF —— 这条由 [13b] 钉住。
    """
    def __init__(self, reply):
        self.reply = reply
        self.rpos = 0
        self.sent = b''
        self.closed = False
        self.nrecv = 0          # ★ 累计读到的字节数：断言"只读了头、没读 body"
        self.recv_calls = 0     # ★ recv 调用次数
        self.settimeout_calls = 0
        self.setblocking_calls = 0
        self.blocking = True    # 真 socket 默认阻塞
        self.nb_armed = False   # ★ 非阻塞是否"上膛"（sleep 会清）
        self.violations = 0     # ★ "recv 之前没紧邻 setblocking(False)" 的次数
        self.eagain = 0         # 非阻塞下"暂时没数据"的次数（= 板端日志里的"空转"）
        # 模拟 TCP 分段：一次最多给 chunk_cap 字节（默认一次全给）
        self.chunk_cap = int(SESSION.get('chunk_cap') or 0) or 10 ** 9
        # ★ 前 N 次 recv 故意"没数据"（模拟响应还没到板端）
        self.empty_lead = int(SESSION.get('empty_lead', 0))

    def settimeout(self, t):
        self.settimeout_calls += 1
        raise AssertionError(
            "板端调了 sock.settimeout() —— 这正是六~八轮全部卡死/读回 0 字节的共同点")

    def setblocking(self, flag):
        # ⚠️ v9.1 **允许且要求**用这个方法（和 settimeout 完全不同）：
        #    它是唯一能让 recv"没数据就立刻返回"的手段，而这块板子的 recv 一旦阻塞
        #    就会永久卡死（第九次上板：第 2 次 POST 卡死在 recv 上，必须断电）。
        self.setblocking_calls += 1
        self.blocking = bool(flag)
        self.nb_armed = not bool(flag)      # 只有"切到非阻塞"才算上膛

    def connect(self, addr):
        self.addr = addr

    def sendall(self, data):
        self.sent += data

    def send(self, data):
        self.sent += data
        return len(data)

    def recv(self, n):
        self.recv_calls += 1
        # ★★ 守卫：没有"紧邻的 setblocking(False)"就直接 recv = 板端那个"卡死"动作。
        #    这里**不抛异常**（抛了会被 _read_head 的 except 吞掉、反而测不出来），
        #    而是记一次违规 + 返回空（模拟"永远读不到"），由 [13] 段末尾断言违规数为 0。
        if self.blocking or not self.nb_armed:
            self.violations += 1
            return b''
        if self.empty_lead > 0:          # 模拟"响应还在路上"
            self.empty_lead -= 1
            self.eagain += 1
            return b''                   # ← K230 的非阻塞语义：没数据返回空，不抛 EAGAIN
        if self.rpos >= len(self.reply):
            self.eagain += 1
            return b''                   # 同上：读完了也是空
        cap = min(n, self.chunk_cap)
        chunk = self.reply[self.rpos:self.rpos + cap]
        self.rpos += len(chunk)
        self.nrecv += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def _fake_socket():
    if SESSION.get('connect_fail_next'):
        SESSION['connect_fail_next'] = False
        raise OSError('simulated network failure')
    pending = SESSION['replies']
    reply = pending.pop(0) if pending else (
        SESSION['default_reply'] if SESSION['default_reply'] is not None else _mk_reply(200, body=b'{}'))
    cls = SESSION.get('sock_cls') or FakeSock      # 允许测试指定假 socket 的子类（断流场景）
    s = cls(reply)
    SESSION['socks'].append(s)
    return s


_mk('usocket', socket=_fake_socket,
    getaddrinfo=lambda host, port: [(None, None, None, None, ('127.0.0.1', port))])


# --- 假 select（v9 起板端**不再使用**）---
# 留着只是为了"万一有人重新 import uselect"时报错更好看；
# 真正的约束在 [16]：源码里不许出现 uselect（poll 在这块固件上会静默失效）。
class _FakePoller:
    def __init__(self):
        self._reg = []

    def register(self, sock, mask=1):
        self._reg.append(sock)

    def unregister(self, sock):
        if sock in self._reg:
            self._reg.remove(sock)

    def poll(self, timeout_ms=0):
        SESSION['poll_calls'] = SESSION.get('poll_calls', 0) + 1
        ready = [(s, 1) for s in self._reg if s.rpos < len(s.reply)]
        if not ready:
            SESSION['poll_timeouts'] = SESSION.get('poll_timeouts', 0) + 1
        return ready


_mk('uselect', poll=lambda: _FakePoller(), POLLIN=1)

# 注意：不桩 ubinascii —— k230_classify.py 自己有 `except ImportError: import binascii`，
# PC 上正好走那条回退分支，顺带把回退路径也验证了。
_mk('media')
_mk('media.sensor', CAM_CHN_ID_0=0, CAM_CHN_ID_1=1, CAM_CHN_ID_2=2,
    Sensor=_FakeSensor)

# ---------- 导入被测模块 ----------
import k230_classify as K                     # noqa: E402

# ⚠️ v11 起 k230_classify.py 默认 TRANSPORT="serial"（方案丙：板子不开 socket）。
#    下面 [1]~[16] 全部是针对 **v10 HTTP 通道**的历史断言，所以这里先钉回 "http"，
#    保证它们测的还是那段代码；方案丙由末尾的 [17] 单独覆盖。
#    （serial 模式下这些用例根本不走 socket，硬跑会把 FakeSock 的计数断言全带偏。）
K.TRANSPORT = "http"

# ★ 2026-10-02：抓拍编码改成 **save() 优先**（`to_jpeg()` 对 CHN0 的 YUV420SP 会
#   **静默**产出 RGB565 花屏，见 k230_classify._encode_jpeg 的说明）。
#   板上 SHOT_PATH 是 `/sdcard/_edge_shot.jpg`，PC 上那个路径不存在 —— `save()`
#   会直接抛异常、回落到 to_jpeg，等于**这条新分支根本没被测到**。
#   所以这里把它指到系统临时目录，让 save 分支真的跑一遍。
import tempfile as _tempfile                    # noqa: E402
K.SHOT_PATH = os.path.join(_tempfile.gettempdir(), '_edge_shot_offline.jpg')


# ---------- 时间代理：把"sleep 会冲掉 setblocking"这条假设建模进测试 ----------
# 证据链（都是上板实测）：
#     v8   = `setblocking(False)` → **立刻** recv           → 不卡，但每次读回 0 字节
#     v8.1 = `setblocking(False)` → `sleep(0.25)` → recv     → **又卡死**
#   两者只差中间那个 sleep ⇒ 高度怀疑 `setblocking` 会被 `time.sleep()` 冲掉。
#   所以这里让**每一次 time.sleep() 都清掉所有假 socket 的"非阻塞上膛"标志**：
#   代码若在 sleep 之后没重新 setblocking(False) 就 recv，[13] 段的违规计数立刻非 0。
# 顺带让 sleep **虚拟推进时间**（不真睡）：
#   ① [9] 段要等 5.5 秒，真睡会让自测很慢；
#   ② `_read_head` 的空转预算靠**单调时钟**到点退出（PC 端没有 ticks_ms，回退到
#      `time.time()`），时间不推进就会死循环 —— _TimeProxy 虚拟推进的正是这个。
class _TimeProxy:
    def __init__(self, real):
        self._real = real
        self._offset = 0.0

    def time(self):
        return self._real.time() + self._offset

    def sleep(self, t):
        for sk in SESSION['socks']:
            sk.nb_armed = False          # ★ sleep 冲掉非阻塞（v8.1 踩过的坑）
        self._offset += float(t or 0)    # 虚拟推进，测试才跑得动

    def __getattr__(self, name):
        return getattr(self._real, name)


_ORIG_TIME = K.time                  # 留着当"真实时钟"
K.time = _TimeProxy(_ORIG_TIME)
_REAL_TIME = _ORIG_TIME              # ★ 测量"到底卡不卡"必须用真实时钟（K.time 是虚拟的）

# 自测里把"发完先睡一会儿"关掉：那 0.35/0.8s 是给真板子的，
# 在 PC 上只会让整轮跑得慢；它们本身由 [16] 的源码约束去钉。
_DEFAULT_SETTLE = (K.HTTP_SETTLE, K.IMG_SETTLE)   # 先留一份原始值
K.HTTP_SETTLE = 0
K.IMG_SETTLE = 0

PASS = [0]
FAIL = [0]


def code_only(src):
    """剥掉注释和字符串字面量，只留可执行代码 —— 给 [16] 的源码约束用。

    为什么需要：v9 的源码里到处写着"不 settimeout"这类说明文字（注释、docstring、
    连 boot 横幅的 print 里都有），直接 `'.settimeout(' in src` 会把它们也匹配上，
    反而把新版判成旧版。
    """
    out = []
    in_str = None
    i = 0
    n = len(src)
    while i < n:
        if in_str:                       # 三引号块（docstring）内部：整块跳过
            if src.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            i += 1
            continue
        if src.startswith('"""', i) or src.startswith("'''", i):
            in_str = src[i:i + 3]
            i += 3
            continue
        if src[i] == '#':                # 行注释
            while i < n and src[i] != '\n':
                i += 1
            continue
        if src[i] in ('"', "'"):         # 单/双引号字符串字面量（boot 横幅那种 print 文案）
            q = src[i]
            i += 1
            while i < n:
                if src[i] == '\\':
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


def check(ok, label, extra=''):
    if ok:
        PASS[0] += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL[0] += 1
        print("  [FAIL] %s %s" % (label, extra))


def src_const(src, name):
    """读源码里 `name = <字面量>` 的**真实赋值**（AST 解析，只认代码）。

    ⚠️ 为什么不用 `'X = "y"' in src`（2026-10-02 踩到的假通过）：
        TRANSPORT 默认值已经从 "serial" 改成 "http"，可上面注释块里还留着
        `#   TRANSPORT = "serial" —— 默认。` —— 纯字符串匹配照样 PASS，
        等于这条断言根本没在盯真正的常量。AST 只解析代码，天然忽略注释。
        也顺带解决"以后在 serial / http 之间来回切换默认值就要改测试"的问题。
    """
    import ast as _ast
    try:
        for node in _ast.parse(src).body:
            if isinstance(node, _ast.Assign):
                for t in node.targets:
                    if isinstance(t, _ast.Name) and t.id == name:
                        if isinstance(node.value, _ast.Constant):
                            return node.value.value
    except SyntaxError:
        pass
    return None


def last_req():
    """最近一次请求 -> (请求行, 头部小写字典, body bytes)"""
    raw = SESSION['socks'][-1].sent
    head, _, body = raw.partition(b'\r\n\r\n')
    lines = head.decode().split('\r\n')
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(':')
        if k:
            hdrs[k.strip().lower()] = v.strip()
    return lines[0], hdrs, body


def ok_reply(record_id='999', verdict='confirmed'):
    """一个"正常入库"的响应：结果全在头里，body 故意留空 —— 板端本来就不该读 body。"""
    SESSION['default_reply'] = _mk_reply(200, {
        'X-Edge-Status': 'ok',
        'X-Edge-Verdict': verdict,
        'X-Edge-Accepted': '1',
        'X-Edge-Record-Id': record_id,
    })


print("== [1] 模块导入 & 配置 ==")
check(K.IMAGE_URL.endswith('/api/edge/image'), "IMAGE_URL 由 SERVER_URL 推导", K.IMAGE_URL)
# ★ v15（2026-10-02）：「立即抓拍」整条链已删除。谁把它加回来，这里立刻红。
check(not hasattr(K, 'CMD_URL'), "★ v15：CMD_URL 已删除（指令通道下线）")
check(not hasattr(K, 'poll_command'), "★ v15：poll_command() 已删除")
check(not (hasattr(K, 'CMD_SETTLE') or hasattr(K, 'CMD_BUDGET')
           or hasattr(K, 'CMD_POLL_MS') or hasattr(K, 'CMD_POLL_INTERVAL')),
      "★ v15：CMD_* 四个常量都已删除")
check(K.IMAGE_MAX_B64 == 60000, "IMAGE_MAX_B64", K.IMAGE_MAX_B64)
check(len(K.IMAGE_QUALITY_LADDER), "质量阶梯已配置", str(K.IMAGE_QUALITY_LADDER))
check(not hasattr(K, 'IMAGE_SCALE_LADDER'),
      "★ v12：IMAGE_SCALE_LADDER 已删除（抓拍不再缩放）")
check(not hasattr(K, '_to_rgb_scaled'),
      "★ v12：_to_rgb_scaled() 已删除（抓拍零转换）")
check(not hasattr(K, 'requests'), "已不再依赖 urequests（改用自实现 _http）")

print("\n== [2] 抓拍体积阶梯（超限必须返回 None，不允许带崩记录）==")
K._sensor = types.SimpleNamespace(
    snapshot=lambda chn=None: FakeImage(640, 480))   # v14：抓拍走 CHN1（RGB565）
K.IMAGE_QUALITY_LADDER = (60, 40, 25)

# a) 上限正常 -> 第一档就该出图（640x480 q60 模拟 11524 字节，远小于 45000）
K.IMAGE_MAX_RAW = 45000
r = K.grab_shot_base64()
check(r is not None and len(r) <= K.IMAGE_MAX_B64,
      "640x480 q60 一档即出图", "%d base64" % (len(r) if r else -1))
check(base64.b64decode(r)[:2] == b'\xff\xd8', "内容是合法 JPEG（可被服务端校验）")

# b) 上限被压到任何一档都超 -> 走完质量阶梯必须返回 None（宁可没图，不能带崩记录）
K.IMAGE_MAX_RAW = 100
check(K.grab_shot_base64() is None, "所有质量档都超限时返回 None（不带图，只报结论）")

# c) 质量阶梯只剩一档且必超限 -> 仍返回 None，且**不抛异常**（异常隔离）
K.IMAGE_QUALITY_LADDER = (80,)
check(K.grab_shot_base64() is None, "质量阶梯全失败 -> None（不抛异常）")

# 复位
K.IMAGE_QUALITY_LADDER = (60, 40, 25)
K.IMAGE_MAX_RAW = K.IMAGE_MAX_B64 * 3 // 4

print("\n== [2b] ★ v12：抓拍必须零转换（不得调 to_rgb888/to_rgb565）==")
# FakeImage 现在**按真机语义**让 to_rgb888/to_rgb565 直接抛异常（真机上它们让板子掉电）。
# 所以上面 [2] 能跑通，就已经证明抓拍链路没碰这两个方法；这里再显式钉死语义。
_raised = False
try:
    FakeImage(640, 480).to_rgb888()
except Exception:
    _raised = True
check(_raised, "FakeImage.to_rgb888() 已按真机语义（活帧上会掉电）")

_src_shot = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'k230_classify.py'), encoding='utf-8').read()
_i = _src_shot.find('def _grab_shot_raw(')
_j = _src_shot.find('def grab_shot_jpeg(', _i)
_body = _src_shot[_i:_j]
# docstring 里**故意**写了 to_rgb888 —— 那是为了讲清"为什么把它删掉"。
# 所以只扫 docstring 之后的**可执行代码**。
_e = _body.find('"""', _body.find('"""') + 3)
_code = _body[_e:] if _e > 0 else _body
check('to_rgb888' in _body and 'to_rgb888' not in _code,
      "docstring 讲清了为什么删 to_rgb888，但**代码里一次都不调**",
      "正文 %d 字符" % len(_code))
check(bool(_code) and 'to_rgb565' not in _code, "★★★ 抓拍代码里不出现 to_rgb565")
check('_encode_jpeg(' in _code, "★ _grab_shot_raw() 直接对帧调 _encode_jpeg")
_enc_src = _src_shot[_src_shot.find('def _encode_jpeg('):]
_enc_src = _enc_src[:_enc_src.find('\ndef ')] if _enc_src.find('\ndef ') > 0 else _enc_src
check('for how in ("save", "to_jpeg", "compress")' in _enc_src,
      "★★★ 编码候选顺序 save 优先（to_jpeg 对 YUV420SP 会**静默**出 RGB565 花屏）")
check('编码 ✔ %s' in _enc_src,
      "★ 编码成功后打印「是谁编的 + 多少字节」（上次只看字节数，才没发现花屏）")
check('_to_rgb_scaled' not in _code, "★★ _grab_shot_raw() 不再调用 _to_rgb_scaled")
check('IMAGE_QUALITY_LADDER' in _code and 'IMAGE_SCALE_LADDER' not in _code,
      "★ 只走质量阶梯，不走缩放阶梯")

print("\n== [3] report() 语义（v10：**只发不读**）==")
ok_reply()
SESSION['connect_fail_next'] = True
check(K.report('X', 90.0) is None, "网络异常 -> None（进补传队列）")
SESSION['socks'] = []
d = K.report('X', 90.0)
check(isinstance(d, dict) and d.get('accepted') is True,
      "发出去了 -> dict（只代表「已发出」，不代表服务端判成什么）", str(d))
check(d.get('verdict') == 'unknown',
      "verdict 恒为 'unknown'（板端不读响应，无从知道服务端结论）")
_s3 = SESSION['socks'][-1]
check(_s3.recv_calls == 0 and _s3.nrecv == 0,
      "★★★ v10：结论链路**一个 recv 都没做**（这就是 v10 的全部意义）",
      "recv %d 次 / %d 字节" % (_s3.recv_calls, _s3.nrecv))
check(_s3.closed, "发完就关连接（不等对端、不等响应）")
check('image' not in _J.loads(last_req()[2].decode()),
      "结论请求体里**不含** image 字段")

print("\n== [4] 图片补传单独走 ==")
SESSION['default_reply'] = _mk_reply(200, {'X-Edge-Status': 'ok'})
K.report_image(999, 'ZmFrZQ==')
line, _, body = last_req()
check(line.startswith('POST /api/edge/image '), "打到补传接口", line)
check(_J.loads(body.decode()).get('record_id') == 999, "带上 record_id")
n = len(SESSION['socks'])
SESSION['default_reply'] = _mk_reply(200, {})       # 兜底查询也拿不到 record_id
check(K.report_image(None, 'x') is False, "record_id 为空且兜底也拿不到时，放弃这张图")
check(len(SESSION['socks']) == n + 1,
      "v7：会先试**一次** GET 兜底（不再直接放弃）",
      "新建了 %d 个连接" % (len(SESSION['socks']) - n))

print("\n== [5] 补传队列只存结论（防 OOM）==")
K._pending.clear()
K.push_pending('A', 80.0)
K.push_pending('B', 81.0)
check(all(len(t) == 2 for t in K._pending), "队列元素是 2 元组（无图片）", str(K._pending))
SESSION['socks'] = []                       # 只看本段发出的请求
ok_reply()
K.flush_pending()
check(len(K._pending) == 0, "补传成功后队列清空")
check(all(len(_J.loads(s.sent.split(b'\r\n\r\n')[1].decode())) == 4
          for s in SESSION['socks']),
      "补传请求体只有 4 个字段（无图）")

print("\n== [6] 队列上限 ==")
K._pending.clear()
for i in range(K.MAX_PENDING + 5):
    K.push_pending('D%d' % i, 80.0)
check(len(K._pending) == K.MAX_PENDING, "超出时丢弃最旧的，长度不超上限",
      "%d/%d" % (len(K._pending), K.MAX_PENDING))

print("\n== [7] ★ v15：「立即抓拍」指令通道已删除（反向断言）==")
# 原 [7] 测的是 poll_command() 从响应头 X-Edge-Cmd 取指令。
# 用户 2026-10-02 拍板整条链杀死（板端 poll_command / 服务端 /api/edge/command / 前端按钮），
# 这里改成"必须不存在"，谁加回来立刻红。
_src7 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'k230_classify.py'), encoding='utf-8').read()
import ast as _ast7
_names7 = set()
for _n7 in _ast7.walk(_ast7.parse(_src7)):
    if isinstance(_n7, _ast7.Name):
        _names7.add(_n7.id)
    elif isinstance(_n7, _ast7.Attribute):
        _names7.add(_n7.attr)
for _gone7 in ('poll_command', 'CMD_URL', 'CMD_POLL_MS', 'CMD_SETTLE', 'CMD_BUDGET'):
    check(_gone7 not in _names7, "★ v15：%s 已彻底删除（无悬空引用）" % _gone7)
check('get("x-edge-cmd")' not in _src7, "★ v15：不再读 X-Edge-Cmd 响应头")

print("\n== [8] send_round()：结论先行（只发不读）+ 图片原始 TCP 单推 ==")
SESSION['socks'] = []
ok_reply()
K.SEND_IMAGE = True
K.IMAGE_TRANSPORT = "raw"
ok = K.send_round('Potato___Late_blight', 88.5, manual=True)
check(ok is True, "手动轮次上报成功")
first = SESSION['socks'][0].sent.decode('latin1')
check(first.startswith('POST /api/edge/disease '), "第一步打结论接口")
check('"manual": true' in first or '"manual":true' in first,
      "带 manual=True（服务端据此跳过去重）")
check('"image"' not in first.split('\r\n\r\n')[1], "结论请求体**不含**图（小包必达）")

_raw8 = SESSION['socks'][-1]
check(_raw8.sent.decode('latin1').startswith('EIMG1 '),
      "第二步走**原始 TCP**（头行 EIMG1 + 原始 JPEG）", _raw8.sent[:24])
check(_raw8.recv_calls == 0,
      "★★★ 图片链路也**零 recv**（挂死点从结构上没有了）")
_hdr8, _, _jpg8 = _raw8.sent.partition(b'\n')
check(_jpg8.startswith(b'\xff\xd8'),
      "推的是**原始 JPEG 字节**（不是 base64）", "%d 字节" % len(_jpg8))
check(str(len(_jpg8)).encode() in _hdr8.split(),
      "头行声明的 nbytes 与实际字节数一致", _hdr8.decode().strip())

print("\n== [9] ★ v10：结论链路**没有**「429 重试」了 ==")
# 为什么删掉重试（v10）：板端不读响应 ⇒ 根本不知道被没被节流。
# 配套的服务端改动是「manual=True 免节流」，人按按钮不会因为 5 秒间隔被静默丢掉；
# 自动巡检 10 秒一轮，也远大于服务端 5 秒的最小间隔。
# 少一次请求 = 少一次撞上抖动/挂死的机会（第九次上板正是卡在"被 429 后的那次重发"上）。
K.SEND_IMAGE = True
K.IMAGE_TRANSPORT = "raw"
SESSION['socks'] = []
SESSION['replies'] = [_mk_reply(429, {'X-Edge-Verdict': 'throttled'})]
_ok9 = K.send_round('X', 80.0, manual=True)
check(_ok9 is True, "服务端就算回 429 也照样返回 True（板端不读，只知道自己发出了）")
check(sum(1 for s in SESSION['socks']
          if s.sent.decode('latin1').startswith('POST /api/edge/disease')) == 1,
      "★★ 无论手动还是自动，结论接口都**只请求 1 次**（不再重发）")
check(all(s.recv_calls == 0 for s in SESSION['socks']),
      "★★★ 整轮**零 recv**（含图片那一步）",
      "%d 条连接" % len(SESSION['socks']))

print("\n== [10] ★ HTTP 通路（v6 核心）：不读 body、显式关连接、显式超时 ==")
# 为什么这几条必须断言（两次上板卡死换来的）：
#   ① urequests 的 timeout 在 CanMV 上不生效；② 它读 body 用 sock.read()（无参 = 读到 EOF），
#   服务端半关闭时永久阻塞 —— 表现为"库里记录已经写进去了，板子却卡死"。
#   所以 v6 自己实现：读完响应头（\r\n\r\n 有终止符）就停，绝不碰 body。
big_body = b'Z' * 5000                      # 故意给一个 5KB 的 body
SESSION['default_reply'] = _mk_reply(
    200, {'X-Edge-Status': 'ok', 'X-Edge-Record-Id': '7'}, body=big_body)
code, hdrs = K._http('GET', 'http://192.168.57.97:5000/api/edge/last-record')
sock = SESSION['socks'][-1]
line, reqh, _ = last_req()
check(code == 200, "能解析出状态码")
check(hdrs.get('x-edge-record-id') == '7', "响应头解析正常（大小写不敏感）")
check(line.endswith('HTTP/1.0'), "用 HTTP/1.0 请求（服务端不会回 chunked）", line)
check(reqh.get('connection') == 'close', "请求带 Connection: close", str(reqh.get('connection')))
check(sock.settimeout_calls == 0,
      "★★ 全程**没有**调 sock.settimeout()（v9 铁律：一设超时，recv 就走坏的 poll 路径）",
      "%d 次" % sock.settimeout_calls)
check(sock.nrecv < len(big_body),
      "★ 只读了响应头，5KB 的 body **一个字节都没读**",
      "读了 %d 字节 / body %d 字节" % (sock.nrecv, len(big_body)))
check(sock.closed, "读完就关连接（不等对端 EOF）")

# 带 body 的 POST 必须声明 Content-Length，否则服务端会一直等剩下的数据
SESSION['default_reply'] = _mk_reply(200, {'X-Edge-Status': 'ok'})
payload = _J.dumps({'disease': 'X', 'confidence': 88.5}).encode()
K._http('POST', 'http://192.168.57.97:5000/api/edge/disease', payload)
_, reqh, body = last_req()
check(reqh.get('content-length') == str(len(payload)), "POST 声明了 Content-Length",
      "%s / 实际 %d" % (reqh.get('content-length'), len(payload)))
check(reqh.get('content-type', '').startswith('application/json'), "Content-Type 正确")
check(body == payload, "body 原样发出")

check(all(v > 0 for v in _DEFAULT_SETTLE),
      "两档「发完先睡」的**默认值**都是正数（睡够再读，v9 唯一允许的等待方式）",
      "HTTP=%.2fs IMG=%.2fs（自测里临时置 0 只是为了跑得快）" % _DEFAULT_SETTLE)
check(not hasattr(K, 'HTTP_TIMEOUT') and not hasattr(K, 'CMD_TIMEOUT'),
      "旧的 *_TIMEOUT 常量已删除（它们代表的是「设超时」那套做法）")

print("\n== [11] 响应头不完整 → 按“已送达”处理，**不抛异常、不装作成功**（v7 语义）==")
# 为什么 v7 把这里从"抛异常"改掉：请求已经发出去了，服务端多半也已经入库。
# 抛异常会让上层当成"失败"去重发 —— 而手动抓拍会绕过去重，重发就是一条重复记录。
# 所以用 (0, {}) 表示"已发出但没读到响应头"，由调用方走 GET 兜底去拿 record_id。
SESSION['default_reply'] = b'HTTP/1.1 200 OK\r\nX-Edge-Status: ok\r\n'   # 没有空行结尾
SESSION['socks'] = []
_code11, _hdrs11 = K._http('GET', 'http://h/x')
check(_code11 == 0, "缺 \\r\\n\\r\\n 时返回 code=0（不抛异常）", "code=%r" % (_code11,))
check(_hdrs11 == {}, "也没有解析出任何响应头")
check(SESSION['socks'][-1].closed, "照样把连接关掉")

print("\n== [12] 一轮异常不得中断循环（异常只丢这一轮）==")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'k230_classify.py'), encoding='utf-8').read()
check('本轮异常（已跳过，循环继续）' in _src,
      "main() 里整轮包了 try/except（不再一路冒到 pl.destroy()）")
check('抓拍链路异常（本轮不带图）' in _src,
      "抓拍链路自身也有兜底 except")
check('DISPLAY_MODE = "virt"' in _src,
      "DISPLAY_MODE 是 'virt'（没接屏时填 'lcd' 会让 get_frame() 无限卡死）")

print("\n== [13] ★★ v9.1 命门：**不 settimeout** + 每次 recv 前 setblocking(False) ==")
# 九轮上板，失败版本与它们的共同点（完整对照见 README §7.4）：
#       v6 / v7   settimeout(6)                              → 永久卡死
#       v8        settimeout(6) + setblocking(False) 轮询      → 不卡，但每次读回 0 字节
#       v8.1      同上 + 发完先睡 0.25s                        → 又卡死
#       v9.0      **不 settimeout** + 阻塞 recv               → 读通了！但响应不来时永久卡死
#   ⇒ 两个结论都成立、而且互相独立：
#       ① `settimeout` **一次都不能调**（前三版全调了，全废）；
#       ② 纯阻塞 recv 也不行（第九轮：第 2 次 POST 卡死在 recv 上，必须断电）。
#   ⇒ v9.1 = **非阻塞轮询** + **每次 recv 前重设 `setblocking(False)`** + 时间预算。
#     其中"每次重设"针对的就是 v8.1 那种"setblocking → sleep → recv"卡死：
#     假 socket 已把"sleep 会冲掉非阻塞"建模进去（见 _TimeProxy），
#     所以只要代码退化成"只设一次非阻塞"，下面 violations 立刻非 0。
_okrep = _mk_reply(200, {
    'Content-Type': 'application/json',
    'Connection': 'close',
    'X-Edge-Status': 'ok',
    'X-Edge-Verdict': 'rejected',
    'X-Edge-Accepted': '1',
    'X-Edge-Record-Id': '80',
}, body=b'Z' * 5000)                    # 故意给大 body：证明"只读头、不读 body"
_okrep_head = _okrep.index(b'\r\n\r\n') + 4

SESSION['default_reply'] = _okrep
SESSION['socks'] = []
_code13, _hdrs13 = K._http('GET', 'http://192.168.57.97:5000/api/edge/disease')
_s13 = SESSION['socks'][-1]
_, _reqh13, _ = last_req()
check(_reqh13.get('x-edge-brief') == '1',
      "请求仍带 X-Edge-Brief（省字节，无副作用）")
check(_hdrs13.get('x-edge-record-id') == '80', "能解析出响应头 X-Edge-Record-Id")
check(_code13 == 200, "状态码正常")
check(_s13.settimeout_calls == 0,
      "★★ 一次都没调 settimeout（六~八轮全部卡死的根因）",
      "settimeout 调用 %d 次" % _s13.settimeout_calls)
check(_s13.setblocking_calls >= 1,
      "★ 用了 setblocking(False)（v9.1：唯一能避免'永久卡死'的手段）",
      "setblocking 调用 %d 次" % _s13.setblocking_calls)
check(_s13.violations == 0,
      "★★ 每一次 recv 之前都**紧邻地**重设了非阻塞（sleep 冲不掉它）",
      "违规 %d 次" % _s13.violations)
check(_s13.nrecv <= 1024 and _s13.nrecv < len(_okrep),
      "★ 最多读了一个 recv 的量（1024B），**没有**一直读到 EOF 把 5KB body 吞掉",
      "读了 %d / 总 %d" % (_s13.nrecv, len(_okrep)))

# ★ TCP 分段：响应头被拆成多块到达时，必须继续读到空行为止（不是"读一次就完事"）。
#   注意这里的分段是**假 socket 模拟的**，真板子上分不分段取决于网络；
#   要论证的是"多读几次也不会把连接读坏"。
SESSION['chunk_cap'] = 100              # 每次最多给 100 字节 → 强迫多次 recv
_big_head = _mk_reply(200, {
    'X-Edge-Status': 'ok',
    'X-Edge-Verdict': 'rejected',
    'X-Edge-Accepted': '1',
    'X-Edge-Record-Id': '81',
    'X-Edge-Pad': 'x' * 500,            # 撑到几百字节，确保要读好几次
}, body=b'{}')
_head_bytes = _big_head.index(b'\r\n\r\n') + 4
SESSION['default_reply'] = _big_head
SESSION['socks'] = []
_c, _h = K._http('GET', 'http://h/big')
_sb = SESSION['socks'][-1]
SESSION['chunk_cap'] = 0
check(_head_bytes > 100, "构造了一个必须分多次 recv 的响应头", "%d 字节" % _head_bytes)
check(_h.get('x-edge-record-id') == '81',
      "★ 分块到达也照样读全（读到 \\r\\n\\r\\n 就停，不靠运气、不靠 chunk 大小）")
check(_sb.recv_calls >= 2, "确实读了不止一次", "recv %d 次" % _sb.recv_calls)
check(_sb.violations == 0, "分块场景下每次 recv 前也都重设了非阻塞")

# ★★ 第九次上板的正题：**响应压根不来**时，必须"到点放弃"而不是永久卡死。
#   假 socket 在"没数据"时返回空（K230 实测语义），这里让它**一直**没数据。
#   ⚠️ 用真实时钟量耗时：K.time 已经被 _TimeProxy 换掉（虚拟推进），量不出真实卡不卡。
SESSION['empty_lead'] = 10 ** 9          # 永远"没数据"
SESSION['default_reply'] = _mk_reply(200)
SESSION['socks'] = []
_t13c = _REAL_TIME.time()
_c13c, _h13c = K._http('GET', 'http://h/never', budget=4.0)
_d13c = _REAL_TIME.time() - _t13c
_s13c = SESSION['socks'][-1]
SESSION['empty_lead'] = 0
check(_c13c == 0, "响应不来时返回 (0, {})，**不抛异常**（按「已送达」处理）")
check(_s13c.violations == 0, "空转期间每次 recv 前也都重设了非阻塞")
check(_d13c < 3.0,
      "★★★ 在预算内退出，**没有永久卡死**（第九次上板就是卡死在这里，必须断电）",
      "实际耗时 %.2fs" % _d13c)

print("\n== [13b] ★★★ v10 主线：整轮零 recv —— 图片不再依赖任何响应 ==")
# 第七/八次上板的现象：**记录一直在涨，APP 上一张图都没有**。
#   原因链：图片要靠"结论响应头里的 record_id" → 响应读不到 → 抓拍整块被跳过
#           （指纹就是日志里**一条 [shot] 都没有**）。
# v9 用 client_uid 兜底（仍然要读响应，只是读不到也不怕）；
# v10 更彻底：**连响应都不读了** —— 结论只发不读，图片改原始 TCP 单向推。
SESSION['socks'] = []
SESSION['replies'] = []          # 就算服务端一个响应都不回，也不该影响这一轮
K.SEND_IMAGE = True
K.IMAGE_TRANSPORT = "raw"
K.HTTP_SETTLE = 0
K.IMG_SETTLE = 0
K._pending.clear()                  # [6] 段塞了 20 条进去，先清空再看这一轮有没有误加
_ok13b = K.send_round('Corn___healthy', 61.3)
_lines13b = [s.sent.decode('latin1') for s in SESSION['socks']]
check(_ok13b is True, "整轮完成并返回 True")
check(len(_lines13b) == 2, "共 2 个动作：结论 HTTP POST + 图片原始 TCP",
      "%d 个" % len(_lines13b))
check(_lines13b[0].startswith('POST /api/edge/disease '), "① 结论先行")
check(_lines13b[1].startswith('EIMG1 '), "② 图片走原始 TCP（不再 POST /api/edge/image）")
check(all(s.recv_calls == 0 for s in SESSION['socks']),
      "★★★ 全链路**零 recv** —— 这正是 v10 要的效果（挂死点从结构上消失）")
_body13b_concl = _J.loads(_lines13b[0].split('\r\n\r\n', 1)[1])
_uid13b = _body13b_concl.get('client_uid')
check(_uid13b, "★ 结论请求带 client_uid", str(_uid13b))
check(_uid13b.encode() in SESSION['socks'][1].sent.split(b'\n', 1)[0],
      "★★ 图片头行里的 uid 与结论的 client_uid 一致（服务端据此配对，不依赖响应）")
check(K._pending == [], "整轮没读任何响应也没有把结论塞进补传队列")
print("\n== [14] record_id 兜底（走 GET 通道）—— v10 只给 HTTP 退路用 ==")
# ⚠️ v10 起主链路**不再需要** record_id（结论只发不读，图片靠 uid 配对），
#    所以这个函数只在 `IMAGE_TRANSPORT="http"` 的退路里才会被调用。
#    保留 + 继续测：退路也得是可用的，不能是"写了一半的备胎"。
SESSION['socks'] = []
SESSION['default_reply'] = _mk_reply(200, {'X-Edge-Record-Id': '555'})
K._LAST_RID = 0
_rid = K._fetch_last_record_id()
_line14, _reqh14, _ = last_req()
check(_rid == '555', "GET /api/edge/last-record 能拿到 record_id", str(_rid))
check(_line14.startswith('GET ') and '/api/edge/last-record' in _line14,
      "走的是 GET（这条通道实测每次都通）", _line14)
check('after=' in _line14 and 'source=' in _line14,
      "带 after= 和 source=（只认比自己已知更新的记录）", _line14)
check(K._LAST_RID == 555, "记住最大 id，避免把新图贴到旧记录上", str(K._LAST_RID))

print("\n== [15] ★ v10：退路 HTTP 图片通道（IMAGE_TRANSPORT='http'）仍可用 ==")
# 原始 TCP 端口万一被网络环境（防火墙/端口策略）挡住，改一个常量就能退回老的
# base64 通道。它经受过实机验证（历史上真出过图），所以不能让它烂在仓库里。
SESSION['socks'] = []
SESSION['default_reply'] = _mk_reply(200, {'X-Edge-Record-Id': '556'})
K.IMAGE_TRANSPORT = "http"
K.SEND_IMAGE = True
K.send_round('X', 80.0)
_lines15 = [s.sent.decode('latin1') for s in SESSION['socks']]
check(_lines15[0].startswith('POST /api/edge/disease '), "结论依然先行")
check(SESSION['socks'][0].recv_calls == 0,
      "★ 结论那一步**仍是零 recv**（v10 行为不因图片通道而变）")
check(any(l.startswith('POST /api/edge/image ') for l in _lines15),
      "切到 http 时图片走 POST /api/edge/image（base64）",
      str([l.split(' ')[1] for l in _lines15]))
K.IMAGE_TRANSPORT = "raw"           # 复位成默认值，不影响后面的段落

print("\n== [16] ★★ v9.1 源码约束：读响应的写法只能有一种 ==")
# 九轮反复回退，所以把"看着能行、实测会挂"的写法**钉死在源码层面**。
# 判据一律用 code_only()（剥掉注释和字符串）—— 源码文档里到处写着
# "不 settimeout"这类说明，不剥掉的话文档会被当成代码匹配。
_src16 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'k230_classify.py'), encoding='utf-8').read()
_code16 = code_only(_src16)
check('.settimeout(' not in _code16,
      "★★ 代码里**没有**任何 settimeout 调用（一设超时，recv 就走坏的 poll）")
check('uselect' not in _code16 and 'select' not in _code16,
      "代码里**没有** uselect/select（poll 在这块固件上会静默失效）")
check('_read_head' in _src16, "读响应头统一走 _read_head")
check('_recv_head' not in _code16 and '_wait_readable' not in _code16,
      "已删除 _recv_head / _wait_readable（它们的「等」在板上是坏的）")
check('time.sleep(settle)' in _src16,
      "★ 发完先睡 settle 再读（让响应躺进接收缓冲区）")
check('sock.recv(limit)' in _src16,
      "读响应用的是普通 sock.recv(limit)，不是 sock.read()（无参读到 EOF）")
check('import urequests' not in _code16, "没有 import urequests（v6 起自实现 HTTP）")
check('X-Edge-Brief' in _src16, "仍带 brief 头（省字节，留着无副作用）")
# ---- v9.1 的硬约束（第九次上板后新增）----
check('setblocking(False)' in _code16 or 'setblocking(0)' in _code16,
      "★★ v9.1：用了 setblocking(False)（唯一能避免'响应不来就永久卡死'的手段）")
check('_set_nonblocking' in _code16,
      "★★ v9.1：非阻塞走 _set_nonblocking 统一入口")
check('_set_nonblocking(sock)' in _code16,
      "★★ v9.1：**每次 recv 之前**都重设非阻塞（sleep 会把它冲掉，见 _TimeProxy）")
check('HEAD_BUDGET' in _code16 and 'IMG_BUDGET' in _code16,
      "★★ v9.1：两档时间预算各自独立（结论 4s / 图片 8s）")
# ---- 2026-10-01 第十次上板：预算计时改成单调时钟（原来那条断言把 bug 钉住了）----
check('time.time() >= deadline' not in _code16,
      "★★ 预算**不再**拿 time.time() 和 deadline 比（RTC epoch + 浮点会把它抹平 ⇒ 一次 recv 都没做就'用尽'）")
check('_ticks_ms' in _code16 and '_ticks_diff' in _code16,
      "★★ 预算 / 排程统一走单调时钟 _ticks_ms + _ticks_diff")
check('_ticks_diff(_ticks_ms(), t0) >= budget_ms' in _code16,
      "★ 预算 = 单调时钟差值 ≥ 毫秒预算（budget_ms），与 socket 超时无关")
# ---- 沿用 v9 的两条 ----
check('HTTP_SETTLE' in _src16 and 'IMG_SETTLE' in _src16,
      "两档 settle 各自独立（结论 0.35s / 图片 0.80s）")
check('client_uid' in _src16,
      "★★ 结论与图片共用 client_uid（服务端据此配对，不依赖任何响应）")

# ---- ★★★ v10 的硬约束（第十二次上板后新增）----
# 把"以后又忍不住去读响应"这件事钉死在源码层面。
# 背景：probe 干净环境下读响应 5/5 全通，主脚本跑过一次 KPU 推理后就挂死；
#       结论/图片都改成单向之后，挂死点从结构上消失。所以"结论链路里不许读"是硬约束。
check('_http_send_only' in _code16,
      "★★★ v10：结论走 _http_send_only（connect→send→close，只发不读）")
check('send_image_raw' in _code16,
      "★★★ v10：图片走 send_image_raw（原始 TCP 单向推送）")
check('EIMG1' in _src16,
      "★★ 协议头 EIMG1（与 Python_Backend/utils/edge_raw.py 对齐）")
check(src_const(_src16, 'IMAGE_TRANSPORT') in ('raw', 'http'),
      "★ IMAGE_TRANSPORT 取值受支持（raw=原始 TCP；http=base64 补传）",
      src_const(_src16, 'IMAGE_TRANSPORT'))
_i16 = _code16.find('def report(')
_j16 = _code16.find('def report_image(')
_body16 = _code16[_i16:_j16] if (_i16 >= 0 and _j16 > _i16) else ''
check(bool(_body16) and '_read_head' not in _body16,
      "★★★ report() 函数体里**没有** _read_head（结论链路一个字都不读）",
      "%d 字符" % len(_body16))

# ==================================================================
# [17] ★★★ 方案丙（v11）：TRANSPORT="serial" —— 板子**不开 socket**，全走串口
# ==================================================================
# 背景（第十三次上板）：推理后的 [selftest] **全过**（connect 0.03s、recv 立刻返回
# b''），可紧接着 report() 的 connect 就让板子复位、USB 掉线 —— 连固件的
# `connect success` 都没来得及打印。而 0.12 秒前那个**一模一样的 connect** 刚成功过。
# ⇒ 病根不是某一行，是「推理之后不能用网」。所以方案丙一个 socket 都不开：
#   结论/图片全部 print 到 USB 串口，由 PC 侧 serial_relay.py 代发。
#   下面这几条断言就是用来钉死"板端整轮零 socket"的。
print("\n== [17] ★★★ 方案丙：serial 模式（零 socket，全部 @@ 打印）==")
import io as _io                                   # noqa: E402
import contextlib as _ctx                         # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_src17 = open(os.path.join(_HERE, 'k230_classify.py'), encoding='utf-8').read()
check(src_const(_src17, 'TRANSPORT') in ("serial", "http"),
      "★ TRANSPORT 取值受支持（serial=方案丙串口 / http=板端直连）",
      src_const(_src17, 'TRANSPORT'))
check(K.TRANSPORT in ("serial", "http"), "TRANSPORT 取值受支持", K.TRANSPORT)

# ---- 17.1 结论：一行 @@R，且整轮不建任何 socket ----
K.TRANSPORT = "serial"
K.SEND_IMAGE = False
_n_before = len(SESSION['socks'])
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    _ok17 = K.send_round('Potato___Late_blight', 88.5, manual=True)
_lines17 = [l for l in _buf.getvalue().splitlines() if l.startswith('@@')]
check(_ok17 is True, "serial 轮次返回 True")
check(len(SESSION['socks']) == _n_before,
      "★★★ 整轮**没有新建任何 socket**（方案丙的全部意义）",
      "新增 %d 个" % (len(SESSION['socks']) - _n_before))
check(len(_lines17) == 1 and _lines17[0].startswith('@@R '),
      "结论打成一行的 @@R", _lines17[0][:64] if _lines17 else "(无)")
_p17 = _lines17[0].split(' ', 4) if _lines17 else ['', '', '', '', '']
check(len(_p17) == 5 and _p17[4] == 'Potato___Late_blight',
      "★ label 放最后一位（标签里万一有空格也不会错位）",
      _p17[4] if len(_p17) > 4 else '')
check(abs(float(_p17[2]) - 88.5) < 0.05, "置信度是 0~100 百分数（不是 0~1）", _p17[2])
check(_p17[3] == '1', "manual 位为 1（手动轮次标记）", _p17[3])

# ---- 17.2 图片：@@B/@@D/@@E 分块，PC 能逐字节还原 ----
K.SEND_IMAGE = True
K.IMAGE_QUALITY_LADDER = (60, 40, 25)
_n_before = len(SESSION['socks'])
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    _ok17b = K.send_round('Corn___healthy', 61.3)
_lines17b = [l for l in _buf.getvalue().splitlines() if l.startswith('@@')]
_kinds17 = [l.split(' ', 1)[0] for l in _lines17b]
check(len(SESSION['socks']) == _n_before, "带图那一轮也**没有新建 socket**")
check(_kinds17[:1] == ['@@R'], "① 结论先行（顺序不能调）", _kinds17[:1])
check('@@B' in _kinds17 and '@@E' in _kinds17, "② 图片有开始/结束帧")
_uid17 = [l for l in _lines17b if l.startswith('@@R ')][0].split(' ', 4)[1]
_b17 = [l for l in _lines17b if l.startswith('@@B ')][0].split()
_e17 = [l for l in _lines17b if l.startswith('@@E ')][0].split()
check(_b17[1] == _e17[1] == _uid17,
      "结论与图片共用 uid（服务端据此配对，不需要 record_id）", _uid17)
_d17 = [l for l in _lines17b if l.startswith('@@D ')]
check(int(_b17[3]) == len(_d17),
      "声明的块数 = 实际 @@D 行数", "%s / %d" % (_b17[3], len(_d17)))
_b64_17 = ''.join(l.split(' ', 2)[2]
                  for l in sorted(_d17, key=lambda s: int(s.split(' ', 2)[1])))
_jpg17 = base64.b64decode(_b64_17)
check(len(_jpg17) == int(_b17[2]), "还原字节数 = 声明 nbytes", "%d" % len(_jpg17))
check(_jpg17.startswith(b'\xff\xd8'), "还原出来是合法 JPEG")
check((sum(_jpg17) & 0xFF) == int(_e17[2]),
      "校验和一致（PC 侧用同一个算法：sum(jpeg) & 0xFF）", _e17[2])
check(K._pending == [], "serial 模式**不进补传队列**（串口无从判断'发送失败'）")

# ---- 17.3 源码约束：串口链路里不许出现任何 socket/HTTP ----
_iS = _src17.find('def _send_round_serial(')
_jS = _src17.find('def report(', _iS)
_bodyS = _src17[_iS:_jS] if (_iS >= 0 and _jS > _iS) else ''
check(bool(_bodyS) and 'serial_report' in _bodyS and 'serial_send_image' in _bodyS,
      "_send_round_serial() 走 serial_report / serial_send_image", "%d 字符" % len(_bodyS))
check(bool(_bodyS) and '_socket' not in _bodyS and '_http' not in _bodyS
      and '_read_head' not in _bodyS,
      "★★★ _send_round_serial() 里**不出现任何 socket / HTTP 调用**")
for _tag, _what in (('@@R', '结论'), ('@@B', '图片开始'), ('@@D', '图片分块'),
                    ('@@E', '图片结束')):
    check(_tag in _src17, "帧标记 %s（%s）已定义" % (_tag, _what))
_iW = _src17.find('    wlan = None')
check(_iW >= 0 and 'if TRANSPORT == "serial"' in _src17,
      "★★ main() 里 serial 模式**根本不连 WiFi**（不调 wifi_connect）")
check(_src17.count('TRANSPORT == "http" and') >= 2,
      "★ 联网检查 / socket 自检 都已按 TRANSPORT 开关（serial 下不跑）",
      "%d 处" % _src17.count('TRANSPORT == "http" and'))

K.TRANSPORT = "http"          # 复位成默认，避免影响以后追加的段

print("\n" + "=" * 50)
print("PASS %d / FAIL %d" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
