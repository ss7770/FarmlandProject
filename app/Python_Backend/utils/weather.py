import requests
import config
import functools
import time

def retry(max_retry=3, delay=1):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, max_retry+1):
                result = func(*args, **kwargs)
                if 'reason' not in result:
                    return result
                if i == max_retry:
                    return result
                time.sleep(delay)
            return result
        return wrapper
    return decorator



@retry(max_retry=3, delay=1)
def _fetch_remote():
    try:
        response = requests.get(config.WEATHER_API_URL, timeout=3)
        if response.status_code == 200:
            return response.json()
        else:
            return {'reason': '天气服务暂时不可用'}
    except:
        return {'reason': '天气服务连接失败'}


# 进程级缓存：成功结果缓存 10 分钟；失败结果 60 秒内直接复用，避免每次打开
# 页面都被"重试 3 次 + sleep"卡住（WEATHER_API_URL 指向的服务未启动时尤其明显）
_cache = {'data': None, 'ts': 0.0}
_TTL_OK = 600.0
_TTL_FAIL = 60.0


def fetch_weather():
    now = time.time()
    cached = _cache['data']
    if cached is not None:
        ttl = _TTL_FAIL if 'reason' in cached else _TTL_OK
        if now - _cache['ts'] < ttl:
            return cached
    data = _fetch_remote()
    _cache['data'] = data
    _cache['ts'] = now
    return data
