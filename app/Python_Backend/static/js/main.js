let btn6h = document.getElementById('btn-6h');
let btn24h = document.getElementById('btn-24h');
let btn3d = document.getElementById('btn-3d');
let refreshBtn = document.getElementById('btn-refresh');
let select = document.getElementById('select');

let chartDom = document.getElementById('chart');
let myChart = echarts.init(chartDom);


window.onload = function () {
    btn6h.classList.add('active');
    loadData('6h');
}

btn6h.onclick = function() {
    btn6h.classList.remove('active');
    btn24h.classList.remove('active');
    btn3d.classList.remove('active');
    this.classList.add('active');
    
    loadData('6h');
};

btn24h.onclick = function() {
    btn6h.classList.remove('active');
    btn24h.classList.remove('active');
    btn3d.classList.remove('active');
    this.classList.add('active');
    
    loadData('24h');
};

btn3d.onclick = function() {
    btn6h.classList.remove('active');
    btn24h.classList.remove('active');
    btn3d.classList.remove('active');
    this.classList.add('active');
    
    loadData('3d');
};

function getBtnColor() {
    if (btn6h.classList.contains('active')) {
        return '6h';
    } else if (btn24h.classList.contains('active')) {
        return '24h';
    } else if (btn3d.classList.contains('active')) {
        return '3d';
    } else {
        return '6h';
    }
}

select.onchange = function() {
    loadData(getBtnColor());
}

refreshBtn.onclick = function() {
    loadData(getBtnColor());
};

function loadData(range) {
    let limit = 6;
    if(range == '6h') {
        limit = 6;
    } else if(range == '24h') {
        limit = 24;
    } else if(range == '3d') {
        limit = 72;
    }
    let field = document.getElementById('select').value;
    fetch('/history?limit=' + limit + '&field=' + field)
        .then(function(response) {
            return response.json()
        })
        .then(function(data) {
            let times = data.times;
            let values = data.values;

            let yAxisName = '温度(℃)'
            if(select.value == 'temp') {
                yAxisName = '温度(℃)'
            } else if(select.value == 'humi') {
                yAxisName = '湿度(%)'
            } else if(select.value == 'light') {
                yAxisName = '光照强度(lux)'
            } else if(select.value == 'soil') {
                yAxisName = '土壤湿度(%)'
            } else if(select.value == 'water') {
                yAxisName = '水位(cm)'
            }

            let option = {
            title: {
                text: '传感器历史数据',
                left: 'center',
                textStyle: {
                    color: '#555555',
                    fontSize: 18
                }
            },
            xAxis: {
                type: 'category',
                data: times,
                axisLabel: {
                    color: '#888888',
                    fontSize: 12,
                    rotate: 30
                }
            },
            yAxis: {
                type: 'value',
                name: yAxisName,
                nameLocation: 'middle',
                nameGap: 30,
                nameTextStyle: {
                    color: '#888888',
                    fontSize: 12
                },
                axisLabel: {
                    color: '#888888',
                    fontSize: 12
                }
            },
            series: [{
                data: values,
                type: 'line',
                smooth: true,
                symbol: 'circle',
                symbolSize: 6,
                lineStyle: {
                    color: '#5cb85c',
                    width: 2
                },
                itemStyle: {
                    color: '#fff',
                    borderColor: '#5cb85c',
                    borderWidth: 2
                },
                areaStyle: {
                    color: 'rgba(92, 184, 92, 0.2)'
                }
            }],
            tooltip: {
                trigger: 'axis',
                formatter: function(params) {
                    const p = params[0];
                    return `${p.name}<br/>${yAxisName}: ${p.value}`;
                }
            }
    };
    myChart.setOption(option);
})
}

// ===== 病害巡检记录（ESP32-CAM 自动上传 / D200 摄像头自动巡检） =====
var SOURCE_LABEL = {
    'auto': '自动上传',
    'd200': 'D200 巡检',
    'manual': '手动'
};
/** 病害记录列表刷新间隔（与 APP 端 30 秒轮询错开，避免同时打后端） */
var DISEASE_LIST_MS = 20000;

function loadDiseaseRecords() {
    fetch('/api/disease/records?limit=8')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var ul = document.getElementById('disease-list');
            if (!ul) return;
            ul.innerHTML = '';
            if (!d.records || d.records.length === 0) {
                ul.innerHTML = '<li>暂无记录（等待 D200 巡检或拍照上传）</li>';
                return;
            }
            d.records.forEach(function(rec) {
                var li = document.createElement('li');
                var img = rec.image_path
                    ? '<img src="/api/' + rec.image_path + '" style="width:60px;vertical-align:middle;margin-right:8px;border-radius:4px;">'
                    : '';
                li.innerHTML = img + rec.timestamp + ' — <b>' + rec.disease + '</b>（' +
                               rec.confidence + '%，' + (SOURCE_LABEL[rec.source] || rec.source || '手动') + '）';
                ul.appendChild(li);
            });
        })
        .catch(function() {
            var ul = document.getElementById('disease-list');
            if (ul) ul.innerHTML = '<li>病害记录加载失败</li>';
        });
}

window.addEventListener('load', loadDiseaseRecords);
// 病害记录列表必须自动轮询：否则打开看板那一刻的列表（含缩略图）就定死了。
// 后端新记录入库、APP 通知也响，看板里的图片却始终不变——之前缺的就是这个 setInterval。
window.setInterval(loadDiseaseRecords, DISEASE_LIST_MS);

// ===== D200 摄像头巡检面板 =====
var CAMERA_STATUS_MS = 10000;
var CAMERA_RESULT_MS = 15000;

function verdictBadge(verdict, text) {
    var label = { confirmed: '确诊', suspected: '疑似', rejected: '拒识' }[verdict] || verdict;
    return '<span class="verdict-badge verdict-' + verdict + '">' + label + '</span>' + (text || '');
}

function loadCameraStatus() {
    fetch('/api/camera/status')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var cam = (d.camera || {});
            var el = document.getElementById('camera-status');
            if (!el) return;
            if (!cam.enabled) {
                el.className = 'camera-status offline';
                el.textContent = '摄像头未启用（config.D200_ENABLED=false）';
            } else if (cam.online) {
                el.className = 'camera-status online';
                el.textContent = '在线 ' + cam.host;
            } else {
                el.className = 'camera-status offline';
                el.textContent = '离线 ' + cam.host +
                    (cam.last_error ? '（' + cam.last_error + '）' : '');
            }
            var st = cam.state || {};
            var meta = [];
            if (cam.frame_seq) meta.push('第 ' + cam.frame_seq + ' 帧');
            if (cam.last_frame_age_sec !== null && cam.last_frame_age_sec !== undefined)
                meta.push('最近一帧 ' + cam.last_frame_age_sec + 's 前');
            meta.push('已巡检 ' + (st.inspection_count || 0) + ' 次');
            if (st.inspect && cam.inspect && cam.inspect.enabled === false) meta.push('自动巡检已关闭');
            document.getElementById('camera-meta').textContent = meta.join(' · ');
            noteCameraSeq(cam.frame_seq);   // 帧号不前进 → 看门狗重连推流
        })
        .catch(function() {
            var el = document.getElementById('camera-status');
            if (el) { el.className = 'camera-status offline'; el.textContent = '状态获取失败'; }
        });
}

function renderCameraResult(res) {
    var box = document.getElementById('camera-result');
    if (!box) return;
    if (!res) { box.textContent = '暂无巡检结果'; return; }
    if (res.status !== 'ok') {
        box.innerHTML = '<b>抓拍失败：</b>' + (res.msg || '') +
            (res.detail ? '（' + res.detail + '）' : '');
        return;
    }
    if (!res.verdict) {   // 识别服务不可用等异常：只给提示，不渲染三档徽章
        box.innerHTML = '<b>已抓拍</b>（' + (res.image || '') + '），但识别未返回结果：' +
            (res.msg || '见服务端日志');
        return;
    }
    var head = verdictBadge(res.verdict, res.verdict_text || '');
    var line = head + '<b>' + (res.disease || '--') + '</b>';
    if (res.confidence !== undefined) line += '　置信度 ' + res.confidence + '%';
    line += '　<span style="color:#999">' + (res.timestamp || '') +
        '（' + (res.trigger === 'manual' ? '手动抓拍' : '自动巡检') + '）</span>';
    if (res.recorded) line += '　<span style="color:#d9534f">已写入巡检记录</span>';
    var img = res.image ? '<img src="/api/uploads/' + res.image + '" alt="抓拍图">' : '';
    box.innerHTML = line + img;
}

function loadCameraLatest() {
    fetch('/api/camera/latest')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.status === 'ok') renderCameraResult(d.result);
            else renderCameraResult(null);
        })
        .catch(function() { renderCameraResult(null); });
}

function doCapture() {
    var btn = document.getElementById('btn-capture');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '识别中…';
    fetch('/api/camera/capture', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            renderCameraResult(d.status === 'ok' ? d : { status: 'error', msg: d.msg, detail: d.detail });
            loadCameraStatus();
            loadDiseaseRecords();
        })
        .catch(function(e) {
            renderCameraResult({ status: 'error', msg: e.message });
        })
        .then(function() {
            btn.disabled = false;
            btn.textContent = '立即抓拍识别';
        });
}

// ===== 推流看门狗 =====
// 为什么不能只靠 img.onerror：MJPEG 是 multipart/x-mixed-replace，
// 画面冻结或服务端主动结束流时，多数浏览器不会触发 error，
// <img> 会一动不动地停在最后一帧（这正是"看板图片根本不改变"的现象）。
// 所以改用 /api/camera/status 的 frame_seq 判断画面是否还在推进。
var CAMERA_STALL_POLLS = 3;      // 连续 3 次 status（约 30 秒）帧号不前进就重连
var streamSeq = null;
var streamStall = 0;

function reloadCameraStream() {
    var img = document.getElementById('camera-stream');
    if (!img) return;
    streamSeq = null;
    streamStall = 0;
    img.src = '/api/camera/stream?t=' + Date.now();
}

function noteCameraSeq(seq) {
    if (seq === undefined || seq === null) return;
    if (streamSeq !== null && seq <= streamSeq) {
        streamStall++;
        if (streamStall >= CAMERA_STALL_POLLS) reloadCameraStream();
    } else {
        streamStall = 0;
    }
    streamSeq = seq;
}

// 推流断了（板子重启/被挤掉）时自动重连
function bindCameraStream() {
    var img = document.getElementById('camera-stream');
    var btn = document.getElementById('btn-capture');
    if (btn) btn.onclick = doCapture;
    if (!img) return;
    img.onerror = function() {
        setTimeout(reloadCameraStream, 3000);
    };
}

if (document.readyState === 'complete') {
    bindCameraStream();
} else {
    window.addEventListener('load', bindCameraStream);
}
window.addEventListener('load', loadCameraStatus);
window.addEventListener('load', loadCameraLatest);
window.setInterval(loadCameraStatus, CAMERA_STATUS_MS);
window.setInterval(loadCameraLatest, CAMERA_RESULT_MS);

// ===== AI 灌溉决策面板（P2：融合墒情预测 + 天气 + 病害，可解释输出） =====
var AI_REFRESH_MS = 60000;

function riskBadge(level) {
    var cls = 'risk-low';
    if (level === '高') cls = 'risk-high';
    else if (level === '中') cls = 'risk-mid';
    return '<span class="risk-badge ' + cls + '">' + level + '风险</span>';
}

function fmtPct(v) {
    return (v === null || v === undefined) ? '--' : Number(v).toFixed(1) + '%';
}

function methodLabel(f) {
    if (f.method === 'model') return '模型：' + (f.model || 'GBDT/RF');
    if (f.method === 'linear_fallback') return '线性外推（历史不足兜底）';
    return '不可用';
}

function loadAiRecommendation() {
    fetch('/api/ai/recommendation')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.status !== 'ok') throw new Error(d.msg || '接口异常');
            var rec = d.recommendation || {};
            var fc = d.forecast || {};
            var sensor = d.sensor || {};
            var disease = d.disease;

            var action = rec.should_irrigate
                ? '建议灌溉：小水量分阶段运行约 ' + rec.suggested_pump_seconds + ' 秒，目标湿度 ' + rec.target_soil + '%'
                : '暂无需灌溉';
            document.getElementById('ai-summary').innerHTML =
                riskBadge(rec.risk_level || '低') +
                '<b>' + action + '</b>' +
                '<span class="ai-time">' + (d.generated_at || '') + '</span>';

            var chips = [
                ['当前土壤', fmtPct(sensor.soil), ''],
                ['当前温度', (sensor.temp === undefined ? '--' : sensor.temp) + '℃', ''],
                ['预测+30min', fmtPct(fc.soil_30), ''],
                ['预测+60min', fmtPct(fc.soil_60), ''],
                ['预测方式', methodLabel(fc), ''],
                ['数据时间', fc.last_ts || sensor.timestamp || '--', '']
            ];
            if (fc.stale) chips.push(['数据状态', '陈旧（检查链路）', 'warn']);
            if (fc.note) chips.push(['预测可信度', '低（模型域外，已线性兜底）', 'warn']);
            // 天气可用性：看 ok/error 显式标记，不能用 reason 判断
            // （Java /api/weather 成功响应里本来就带 reason，那是灌溉建议文案）
            if (d.weather && d.weather.ok !== false && !d.weather.error) {
                chips.push(['天气', (d.weather.weather || '--') + ' / 24h雨 ' +
                    (d.weather.rain24h === undefined ? '--' : d.weather.rain24h) + 'mm', '']);
            } else {
                chips.push(['天气', '服务不可用', 'warn']);
            }
            if (disease) {
                var conf = Number(disease.confidence || 0);
                chips.push(['最新病害', disease.disease + '（' + conf.toFixed(0) + '%）', 'warn']);
            }
            var chipsHtml = '';
            chips.forEach(function (c) {
                chipsHtml += '<span class="ai-chip ' + c[2] + '"><i>' + c[0] + '</i>' + c[1] + '</span>';
            });
            document.getElementById('ai-chips').innerHTML = chipsHtml;

            var ul = document.getElementById('ai-reasons');
            ul.innerHTML = '';
            (rec.reasons || []).forEach(function (t) {
                var li = document.createElement('li');
                li.textContent = '· ' + t;
                ul.appendChild(li);
            });

            document.getElementById('ai-note').textContent = rec.safety_note || '';
        })
        .catch(function (e) {
            var s = document.getElementById('ai-summary');
            if (s) s.innerHTML = '<span class="risk-badge risk-mid">不可用</span> AI 建议加载失败：' + e.message;
        });
}

window.addEventListener('load', loadAiRecommendation);
window.setInterval(loadAiRecommendation, AI_REFRESH_MS);