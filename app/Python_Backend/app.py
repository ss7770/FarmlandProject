from flask import Flask, render_template,blueprints,jsonify,request
from werkzeug.serving import WSGIRequestHandler

from blueprints.history import history_bp
from blueprints.disease import disease_bp
from blueprints.capture import capture_bp
from blueprints.sensor_api import sensor_bp
from blueprints.ai import ai_bp
from blueprints.edge import edge_bp          # K230 边缘推理结论接收（方案 B，2026-10-01）
from blueprints.asr import asr_bp            # 离线语音识别（Vosk，2026-10-07）
import config

app = Flask(__name__)

app.register_blueprint(history_bp)
app.register_blueprint(disease_bp)
app.register_blueprint(capture_bp)
app.register_blueprint(sensor_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(edge_bp)
app.register_blueprint(asr_bp)

# D200 摄像头接口：设备已淘汰（config.D200_ENABLED=False），但路由**保留注册**——
# 前端/APP 的老调用不会 404，只拿到"未启用"响应；将来接回任何 HTTP 摄像头也不用改前端。
from blueprints.camera import camera_bp
app.register_blueprint(camera_bp)

class Config:
    DEBUG = True
    SECRET_KEY = 'my-secret-key'

app.config.from_object(Config)

_camera_booted = False
_purge_booted = False
_raw_booted = False


def _start_raw_image_server():
    """后台线程：监听原始 TCP 端口，收 K230 推上来的现场图（协议见 utils/edge_raw.py）。

    为什么要有这条通道（v10，2026-10-01）：
        板端「发得出去、读不回来」——一旦跑过 KPU 推理，读响应就会挂死。
        图片这条路要多等一次响应，所以最早牺牲。改成单向推送后，
        板端一个 recv 都不做，挂死点从结构上消失。
    这里用后台线程而不是 Flask 路由：WSGI 层拿不到裸 TCP 连接，
    而我们要的正是"不按 HTTP 来"。
    """
    from utils.edge_raw import start_raw_image_server
    start_raw_image_server()


def _start_purge_thread():
    """后台清理线程：未确诊的识别记录过期自动删除（连图一起）。

    为什么要它：记录策略改成"拍到就入库"之后，K230 每轮巡检都会留一条，
    未确诊的那些很快会堆成几千条、把 uploads 也撑大。它们的价值只是"短期可追溯"，
    过期就该回收。判定口径见 utils/db.purge_unconfirmed。
    """
    import threading
    import time as _time
    from utils.db import purge_unconfirmed

    def loop():
        while True:
            try:
                ttl = int(getattr(config, 'UNCONFIRMED_TTL_MIN', 30))
                n, files = purge_unconfirmed(ttl)
                if n:
                    print('[purge] 清理未确诊记录 %d 条 / 图片 %d 张（保留 %d 分钟）'
                          % (n, len(files), ttl))
            except Exception as e:
                print('[purge] 清理失败（不影响其他功能）：%s: %s' % (type(e).__name__, e))
            _time.sleep(max(30, int(getattr(config, 'PURGE_INTERVAL_SEC', 300))))

    threading.Thread(target=loop, name='unconfirmed-purge', daemon=True).start()
    print('[purge] 未确诊记录清理线程已启动（TTL=%s 分钟，每 %s 秒扫描）'
          % (getattr(config, 'UNCONFIRMED_TTL_MIN', 30),
             getattr(config, 'PURGE_INTERVAL_SEC', 300)))


@app.before_request
def _boot_once():
    """第一次有请求进来时启动后台线程（摄像头拉流 / 未确诊清理）。

    放在这里而不是 import 期：Flask debug 模式的重载器会让模块在父子两个进程各导入一次，
    在 import 期起线程会导致两个进程同时跑同一套后台任务。

    D200 淘汰后摄像头分支实际是空转（init_camera 内部直接返回 False），
    保留是为了将来接回 HTTP 摄像头时无需改这里。
    """
    global _camera_booted, _purge_booted, _raw_booted
    if not _purge_booted:
        _purge_booted = True
        try:
            _start_purge_thread()
        except Exception as e:
            print('[purge] 线程启动失败：%s: %s' % (type(e).__name__, e))
    if not _raw_booted:
        # 原始 TCP 图片通道：必须在**第一个请求**时起（reloader 的父进程不处理请求，
        # 在 import 期起会父子各绑一次端口 -> 第二个报 "address already in use"）
        _raw_booted = True
        try:
            _start_raw_image_server()
        except Exception as e:
            print('[raw] 通道启动失败（图片会退回 HTTP 通道）：%s: %s'
                  % (type(e).__name__, e))
    if _camera_booted or not getattr(config, 'D200_ENABLED', False):
        return
    _camera_booted = True
    try:
        from blueprints.camera import init_camera
        init_camera()
    except Exception as e:
        print('[camera] 摄像头初始化失败（不影响其他接口）：%s: %s' % (type(e).__name__, e))

@app.route('/history')
def history():
    limit = request.args.get('limit', default=6, type=int)
    field_name = request.args.get('field', default='temp')
    from utils import fetch_history_data
    data = fetch_history_data(limit, field_name)
    return jsonify(data)

@app.errorhandler(404)
def error(e):
    return render_template('error.html'), 404

@app.template_filter('weather_icon')
def weather_icon(value):
    map = {
        '晴': '☀️',
        '多云': '⛅',
        '阴': '☁️',
        '小雨': '🌦️',
        '中雨': '🌧️',
        '大雨': '🌧️',
        '雷阵雨': '⛈️'
    }
    return map.get(value, '🌤️')

@app.route('/')
def weather():
    from utils import fetch_weather
    data = fetch_weather()
    return render_template('dashboard.html', weather=data)


@app.route('/disease')
def disease_board():
    """病害识别独立看板（2026-10-01）。

    从 dashboard 里抽出来做成独立页：识别结果/K230 巡检/手动上传本就同属一件事，
    混在"更多"看板里既挤又难找入口。数据由 /api/disease/overview 一次给全。
    """
    return render_template('disease_board.html')


class _LeanWSGIRequestHandler(WSGIRequestHandler):
    """不发送 `Server` / `Date` 响应头 —— 只为给 K230 省字节。

    为什么（2026-10-01 第五次上板卡死排查）：
        板端一次能收多少字节是有上限的（实测：253 字节的响应**每次都能通**，
        933 字节的响应**每次都卡死**，改读块大小也没用）。
        `Server: Werkzeug/3.1.8 Python/3.11.9`（38B）+ `Date: ...`（37B）
        纯属服务端自报家门，对这个局域网开发服务毫无用处，
        砍掉就是给板端省 75 字节 —— 配合 `X-Edge-Brief` 把响应压到 ~190 字节。
    只改响应头，功能不变；看板/APP 都拿这几个头当空气。
    """

    def send_response(self, code, message=None):
        self.log_request(code)
        self.send_response_only(code, message)      # 不 send_header(Server/Date)


if __name__ == '__main__':
    app.run(host='0.0.0.0', request_handler=_LeanWSGIRequestHandler)
