from flask import Flask, render_template,blueprints,jsonify,request

from blueprints.history import history_bp
from blueprints.disease import disease_bp
from blueprints.capture import capture_bp
from blueprints.sensor_api import sensor_bp
from blueprints.ai import ai_bp
from blueprints.camera import camera_bp

app = Flask(__name__)

app.register_blueprint(history_bp)
app.register_blueprint(disease_bp)
app.register_blueprint(capture_bp)
app.register_blueprint(sensor_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(camera_bp)

class Config:
    DEBUG = True
    SECRET_KEY = 'my-secret-key'

app.config.from_object(Config)

_camera_booted = False

@app.before_request
def _boot_camera_once():
    """第一次有请求进来时启动 D200 拉流 + 自动巡检线程。

    放在这里而不是 import 期：Flask debug 模式的重载器会让模块在父子两个进程各导入一次，
    在 import 期起线程会导致两个进程同时连 D200（固件并发≈1，会互相挤掉）。
    请求只会由真正对外服务的那个进程处理，因此这里天然只启动一次。
    """
    global _camera_booted
    if not _camera_booted:
        _camera_booted = True
        try:
            from blueprints.camera import init_camera
            init_camera()
        except Exception as e:
            print('[D200] 摄像头初始化失败（不影响其他接口）：%s: %s' % (type(e).__name__, e))

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

if __name__ == '__main__':
    app.run(host='0.0.0.0')
