from flask import Flask, render_template,blueprints,jsonify,request

from blueprints.history import history_bp
from blueprints.disease import disease_bp
from blueprints.capture import capture_bp
from blueprints.sensor_api import sensor_bp
from blueprints.ai import ai_bp

app = Flask(__name__)

app.register_blueprint(history_bp)
app.register_blueprint(disease_bp)
app.register_blueprint(capture_bp)
app.register_blueprint(sensor_bp)
app.register_blueprint(ai_bp)

class Config:
    DEBUG = True
    SECRET_KEY = 'my-secret-key'

app.config.from_object(Config)

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
