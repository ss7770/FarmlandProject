"""AiPi-CAM-D200 网络摄像头客户端（适配出厂固件 MJPEG 流）

为什么是"单连接常驻"而不是每次抓一张：
    D200 出厂固件的 HTTP 服务并发能力≈1，且已建立的连接长期不释放。
    反复新建连接会被拒绝（表现为 RST / 超时），浏览器多开一个标签也会把服务位挤掉
    导致画面卡死。所以这里只维持 **一条** /stream 连接，把最新一帧放在内存里，
    Flask 的各个接口（预览、抓拍、自动巡检）都从内存取帧，不再碰摄像头。

    ⚠️ 因此不要在别处再 new 一个 D200Cam，统一用 get_cam() 拿单例。

用法（Flask 后端）：
    from utils.d200_cam import get_cam
    cam = get_cam()          # 单例，配置来自 config.D200_*
    cam.start()              # 后台线程开始拉流（幂等）
    jpg = cam.latest()       # 最新一帧 JPEG 字节，直接喂 TFLite
    cam.status()             # 在线状态 / 帧序号 / 最后错误
    cam.stop()

独立自测：python utils/d200_cam.py   （连抓 3 帧存盘）
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402


class D200Cam:
    def __init__(self, ip, port=80, path='/stream', reconnect_delay=3.0):
        self.ip = ip
        self.port = port
        self.path = path
        self.reconnect_delay = reconnect_delay

        self._cond = threading.Condition()
        self._frame = b''
        self._frame_seq = 0
        self._last_frame_ts = 0.0
        self._connected = False
        self._last_error = ''
        self._last_connect_ts = 0.0

        self._stop_flag = threading.Event()
        self._thread = None
        self._sock = None

    # ---------- 生命周期 ----------
    def start(self):
        """幂等启动后台拉流线程"""
        if self._thread and self._thread.is_alive():
            return False
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._worker, name='d200-stream', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_flag.set()
        s = self._sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=6)
        with self._cond:
            self._connected = False
            self._cond.notify_all()

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    # ---------- 取帧 ----------
    def latest(self, timeout=5.0, max_age=None):
        """拿最新一帧 JPEG 字节。

        max_age=None（默认）：缓存里有帧就立即返回，不看新旧（保持旧行为）。
        max_age=N 秒：只接受"年龄不超过 N 秒"的帧；超龄则最多等 timeout 秒
                      看有没有新帧进来，仍然没有就返回 b''。

        为什么需要 max_age：
            D200 断线或被别的客户端挤掉时 self._frame 会一直保留最后一帧。
            不判断年龄的话，上层会把这张"化石帧"当实时画面反复使用——
            表现为看板画面永久冻结，且自动巡检会对同一张旧图重复识别、重复入库。
        """
        deadline = time.time() + timeout
        with self._cond:
            while True:
                if self._frame:
                    if max_age is None:
                        return self._frame
                    if (time.time() - self._last_frame_ts) <= max_age:
                        return self._frame
                remaining = deadline - time.time()
                if remaining <= 0:
                    return b''
                self._cond.wait(remaining)    # 有新帧时被 notify_all 唤醒

    def wait_frame(self, after_seq, timeout=5.0):
        """等到"比 after_seq 更新"的帧，返回 (seq, jpeg)。MJPEG 推流用，避免重复推同一帧。"""
        deadline = time.time() + timeout
        with self._cond:
            while self._frame_seq <= after_seq:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._frame_seq, self._frame

    def status(self):
        with self._cond:
            age = (time.time() - self._last_frame_ts) if self._last_frame_ts else None
            return {
                'enabled': bool(getattr(config, 'D200_ENABLED', True)),
                'host': self.ip,
                'port': self.port,
                'path': self.path,
                'worker_running': self.is_running(),
                'connected': self._connected,
                'online': bool(self._frame) and age is not None and age < 30,
                'frame_seq': self._frame_seq,
                'frame_bytes': len(self._frame),
                'last_frame_age_sec': None if age is None else round(age, 1),
                'last_error': self._last_error,
            }

    # ---------- 内部 ----------
    def _publish(self, frame):
        with self._cond:
            self._frame = frame
            self._frame_seq += 1
            self._last_frame_ts = time.time()
            self._connected = True
            self._last_error = ''
            self._cond.notify_all()

    def _mark_down(self, err):
        with self._cond:
            self._connected = False
            self._last_error = err
            self._cond.notify_all()

    def _worker(self):
        while not self._stop_flag.is_set():
            try:
                sock = socket.create_connection((self.ip, self.port), timeout=6)
                self._sock = sock
                self._last_connect_ts = time.time()
                sock.settimeout(5)
                req = ('GET %s HTTP/1.1\r\nHost: %s\r\n'
                       'User-Agent: farmland-backend\r\n'
                       'Connection: keep-alive\r\n\r\n' % (self.path, self.ip)).encode()
                sock.sendall(req)
                buf = b''
                while not self._stop_flag.is_set():
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        # 5 秒没有新数据：连接可能已被固件静默掐断，重连一次
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        st = buf.find(b'\xff\xd8')        # JPEG SOI
                        if st < 0:
                            buf = buf[-1:]               # 保留 1 字节防止 SOI 跨包
                            break
                        en = buf.find(b'\xff\xd9', st)   # JPEG EOI
                        if en < 0:
                            if st > 0:
                                buf = buf[st:]
                            break
                        frame = buf[st:en + 2]
                        buf = buf[en + 2:]
                        self._publish(frame)
                try:
                    sock.close()
                except Exception:
                    pass
                self._sock = None
            except Exception as e:
                self._sock = None
                self._mark_down('%s: %s' % (type(e).__name__, e))

            if not self._stop_flag.is_set():
                time.sleep(self.reconnect_delay)

    def snapshot(self, timeout=8.0):
        """一次性抓一帧（自建连接）。

        只在后台线程未运行时使用；否则会与常驻连接抢 D200 那唯一一个服务位。
        """
        if self.is_running():
            return self.latest(timeout)
        s = socket.create_connection((self.ip, self.port), timeout=timeout)
        s.settimeout(timeout)
        try:
            req = ('GET %s HTTP/1.1\r\nHost: %s\r\n'
                   'User-Agent: farmland-backend\r\n'
                   'Connection: keep-alive\r\n\r\n' % (self.path, self.ip)).encode()
            s.sendall(req)
            buf = b''
            while len(buf) < 8_000_000:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                st = buf.find(b'\xff\xd8')
                if st >= 0:
                    en = buf.find(b'\xff\xd9', st)
                    if en >= 0:
                        return buf[st:en + 2]
            return b''
        finally:
            s.close()


_cam = None
_cam_lock = threading.Lock()


def get_cam():
    """单例：全后端只允许存在一条 D200 连接"""
    global _cam
    with _cam_lock:
        if _cam is None:
            _cam = D200Cam(config.D200_HOST,
                           getattr(config, 'D200_PORT', 80),
                           getattr(config, 'D200_STREAM_PATH', '/stream'),
                           getattr(config, 'D200_RECONNECT_DELAY', 3.0))
        return _cam


if __name__ == '__main__':
    cam = get_cam()
    print('连接 %s:%s%s，连抓 3 帧...' % (cam.ip, cam.port, cam.path))
    out_dir = os.path.dirname(os.path.abspath(__file__))
    for i in range(3):
        jpg = cam.snapshot(timeout=8)
        if jpg:
            fn = os.path.join(out_dir, 'd200_frame_%d.jpg' % i)
            with open(fn, 'wb') as f:
                f.write(jpg)
            print('第 %d 帧: %d 字节 -> %s' % (i, len(jpg), fn))
        else:
            print('第 %d 帧: 抓取失败' % i)
        time.sleep(0.5)
    print('测试完成')
