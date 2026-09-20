import requests
import config
import functools
import time

# ---- 成功/失败契约（重要）----
#   成功 → Java /api/weather 的原始 payload，并补 'ok': True
#   失败 → {'ok': False, 'error': '原因'}
# 不要用 'reason' 当失败标记：Java 端 /api/weather 的成功响应里本来就带 'reason'
# （灌溉建议文案，dashboard.html 用它渲染"灌溉建议：..."）。历史 bug 正是用
# 'reason' in result 判失败，导致天气服务正常时也被误判成"不可用"。
_SUCCESS_KEYS = ('weather', 'temperature', 'humidity', 'soilHumidity', 'rain24h')


def is_available(data):
    """fetch_weather() 的返回是否可用（ai.py / 模板 / 前端统一按此判口径）"""
    if not isinstance(data, dict):
        return False
    if data.get('ok') is False or 'error' in data:
        return False
    if data.get('ok') is True:
        return True
    return any(k in data for k in _SUCCESS_KEYS)


def retry(max_retry=2, delay=0.5):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = None
            for i in range(1, max_retry + 1):
                result = func(*args, **kwargs)
                if is_available(result):
                    return result
                if i == max_retry:
                    return result
                time.sleep(delay)
            return result
        return wrapper
    return decorator


@retry(max_retry=2, delay=0.5)
def _fetch_remote():
    # timeout 收紧到 2 秒：天气服务不可达（尤其是端口被防火墙丢包、表现为挂起）时，
    # 不能把 /api/ai/recommendation 和看板首屏拖住；最坏 2 次×2s + 0.5s ≈ 4.5s，
    # 且失败结果有 60 秒缓存，不会每分钟都卡
    try:
        response = requests.get(config.WEATHER_API_URL, timeout=2)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                data['ok'] = True
                return data
            return {'ok': False, 'error': '天气服务返回格式异常'}
        return {'ok': False, 'error': '天气服务暂时不可用（HTTP %d）' % response.status_code}
    except Exception as e:
        return {'ok': False, 'error': '天气服务连接失败：%s' % e}


# 进程级缓存：成功结果缓存 10 分钟；失败结果 60 秒内直接复用，避免每次打开
# 页面都被"重试 3 次 + sleep"卡住（WEATHER_API_URL 指向的服务未启动时尤其明显）
_cache = {'data': None, 'ts': 0.0}
_TTL_OK = 600.0
_TTL_FAIL = 60.0


def fetch_weather():
    now = time.time()
    cached = _cache['data']
    if cached is not None:
        ttl = _TTL_OK if is_available(cached) else _TTL_FAIL
        if now - _cache['ts'] < ttl:
            return cached
    data = _fetch_remote()
    _cache['data'] = data
    _cache['ts'] = now
    return data
