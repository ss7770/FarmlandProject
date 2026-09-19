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

// ===== 病害巡检记录（ESP32-CAM 自动上传） =====
function loadDiseaseRecords() {
    fetch('/api/disease/records?limit=8')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var ul = document.getElementById('disease-list');
            if (!ul) return;
            ul.innerHTML = '';
            if (!d.records || d.records.length === 0) {
                ul.innerHTML = '<li>暂无记录（等待 ESP32-CAM 上传）</li>';
                return;
            }
            d.records.forEach(function(rec) {
                var li = document.createElement('li');
                var img = rec.image_path
                    ? '<img src="/api/' + rec.image_path + '" style="width:60px;vertical-align:middle;margin-right:8px;border-radius:4px;">'
                    : '';
                li.innerHTML = img + rec.timestamp + ' — <b>' + rec.disease + '</b>（' +
                               rec.confidence + '%，' + (rec.source === 'auto' ? '自动巡检' : '手动') + '）';
                ul.appendChild(li);
            });
        })
        .catch(function() {
            var ul = document.getElementById('disease-list');
            if (ul) ul.innerHTML = '<li>病害记录加载失败</li>';
        });
}

window.addEventListener('load', loadDiseaseRecords);

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
            if (d.weather && !d.weather.reason) {
                chips.push(['天气', (d.weather.weather || '--') + ' / 24h雨 ' +
                    (d.weather.rain24h === undefined ? '--' : d.weather.rain24h) + 'mm', '']);
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