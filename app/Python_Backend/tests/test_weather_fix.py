# -*- coding: utf-8 -*-
"""
天气可用性契约修复验证（2026-09-20）

背景 bug：Java /api/weather 的成功响应里本来就带 'reason' 字段（灌溉建议文案，
dashboard.html 用它渲染"灌溉建议：..."），而 Python 侧的 retry 装饰器 / 缓存 TTL /
ai.py / main.js 都把 'reason' 当成了失败标记 → 天气服务正常时 AI 面板也报"天气服务不可用"。

本脚本分两部分：
  A. 契约单测（不依赖服务是否在跑，必须全 PASS）
  B. 联机探测（信息性，服务没起也不算失败）
用法：python tests/test_weather_fix.py [live]
"""
import os
import sys
import json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

PASS, FAIL, INFO = [], [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('%s %s%s' % ('[PASS]' if cond else '[FAIL]', name, (' | ' + detail) if detail else ''))


def info(msg):
    INFO.append(msg)
    print('  · ' + msg)


print('=' * 70)
print('A. 契约单测：可用性判定不能再被 reason 字段误导')
print('=' * 70)
from utils.weather import is_available
from utils import weather_available

# Java WeatherController 的真实成功响应（注意 reason 一定存在）
java_ok = {'temperature': 26, 'weather': '晴', 'rain24h': 0, 'humidity': 33,
           'soilHumidity': 45, 'shouldIrrigate': False, 'irrigationAmount': 0,
           'reason': '土壤湿度充足，无需灌溉'}
fail_dict = {'ok': False, 'error': '天气服务连接失败'}
legacy_fail = {'reason': '天气服务连接失败'}          # 历史格式，无任何天气字段

check('Java 成功 payload（含 reason）→ 可用', is_available(java_ok) is True)
check('显式 ok=True → 可用', is_available(dict(java_ok, ok=True)) is True)
check('失败 payload（ok=False + error）→ 不可用', is_available(fail_dict) is False)
check('历史失败格式（只有 reason、无天气字段）→ 不可用', is_available(legacy_fail) is False)
check('None / 非 dict → 不可用', is_available(None) is False and is_available('x') is False)
check('utils.weather_available 与 is_available 口径一致', weather_available is is_available)

print()
print('=' * 70)
print('A2. 决策理由生成：模拟 Java 在线，不应再出现"天气服务不可用"')
print('=' * 70)
from blueprints.ai import _build_recommendation

forecast = {'method': 'model', 'stale': False, 'last_ts': '2026-09-20 15:00:00',
            'soil_now': 14.0, 'soil_30': 9.0, 'soil_60': 7.0}
rec_ok = _build_recommendation(forecast, dict(java_ok, ok=True), None)
print('  模拟在线时的 reasons：')
for t in rec_ok['reasons']:
    print('    · ' + t)
check('在线时不出现"天气服务不可用"',
      not any('天气服务不可用' in t for t in rec_ok['reasons']))
check('在线时给出天气条目', any(('天气：' in t or '预报' in t) for t in rec_ok['reasons']))

rec_bad = _build_recommendation(forecast, fail_dict, None)
check('离线时仍能正确降级并带原因',
      any('天气服务不可用' in t and 'error' not in t for t in rec_bad['reasons'])
      and any('天气服务连接失败' in t for t in rec_bad['reasons']))

# 降雨预报分支：晴→不触发；小雨→触发暂缓
rec_rain = _build_recommendation(forecast, dict(java_ok, rain24h=30, weather='中雨'), None)
check('预报有雨 → 建议暂缓灌溉且不再建议开泵',
      rec_rain['should_irrigate'] is False
      and any('暂缓灌溉' in t for t in rec_rain['reasons']))

print()
print('=' * 70)
print('B. 联机探测（信息性）')
print('=' * 70)
import requests
from config import WEATHER_API_URL

info('config.WEATHER_API_URL = %s' % WEATHER_API_URL)
service_up = False
for base in (WEATHER_API_URL, 'http://127.0.0.1:8080/api/weather', 'http://localhost:8080/api/weather'):
    try:
        r = requests.get(base, timeout=4)
        info('%s -> HTTP %s  %s' % (base, r.status_code, r.text[:120].replace('\n', ' ')))
        if r.status_code == 200:
            service_up = True
            break
    except Exception as e:
        info('%s -> 不可达：%s' % (base, str(e)[:90]))

if service_up:
    from utils.weather import fetch_weather
    w = fetch_weather()
    check('fetch_weather 判定为可用', is_available(w), 'error=%s' % w.get('error', ''))
    check('成功 payload 保留 reason（模板要渲染灌溉建议）', 'reason' in w)

    from app import app
    c = app.test_client()
    body = (c.get('/api/ai/recommendation').get_json() or {})
    live_reasons = body.get('recommendation', {}).get('reasons', [])
    print('  线上接口 reasons：')
    for t in live_reasons:
        print('    · ' + t)
    check('线上接口不再误报天气不可用',
          not any('天气服务不可用' in t for t in live_reasons))
    page = c.get('/').get_data(as_text=True)
    check('看板天气卡片正常渲染（非降级提示）', '请先启动 Java 天气服务' not in page)
else:
    info('（天气服务当前未启动，B 段跳过 —— 不影响 A 段契约单测结论）')

print()
print('=' * 70)
print('结果：PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项：' + ', '.join(FAIL))
if not service_up:
    print('提示：Java 天气服务未监听 8080，请先启动 app/JAVA_Backend（Spring Boot）')
print('=' * 70)
sys.exit(1 if FAIL else 0)
