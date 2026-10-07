let btn6h = document.getElementById('btn-6h');
let btn24h = document.getElementById('btn-24h');
let btn3d = document.getElementById('btn-3d');
let refreshBtn = document.getElementById('btn-refresh');
let select = document.getElementById('select');

let chartDom = document.getElementById('chart');
let myChart = echarts.init(chartDom);

// 2026-10-05：手机 App 的 WebView 里容器尺寸会变（切 Tab、键盘弹出、横竖屏），
// ECharts 自己不会跟着重算，画布会错位/被裁。这里补一个 resize 监听；
// 安卓端切回「数据」Tab 时还会主动派发一次 resize 事件做兜底。
window.addEventListener('resize', function () {
    if (myChart) myChart.resize();
});


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
                    color: '#3dbe7b',
                    width: 2
                },
                itemStyle: {
                    color: '#fff',
                    borderColor: '#3dbe7b',
                    borderWidth: 2
                },
                areaStyle: {
                    color: 'rgba(61, 190, 123, 0.2)'
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

// ===== 病害识别（2026-10-01 起全部在同级页 /disease） =====
// 本页原先的「AI 病害识别（K230 边缘推理）」面板、以及后来那张「病害识别 ›」入口卡都已移除，
// 病害识别的设备状态 / 最新结果（含图片与置信度占位）/ 记录列表 / 上传识别，
// 全部由 templates/disease_board.html + static/js/disease_board.js 负责。
// 本页不再轮询病害接口，避免两处各拉一份 overview。

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
            // 2026-10-06：把这份结果广播给同页的「AI 墒情短期预测」卡（dashboard.html 内联脚本监听）。
            // 走事件而不是让那边再 fetch 一次：零额外请求、单一数据源，图表与本面板永不一致不了。
            try {
                window.dispatchEvent(new CustomEvent('airec', { detail: d }));
            } catch (e) { }
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