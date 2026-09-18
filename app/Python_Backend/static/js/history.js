// 历史数据看板：曲线 + 筛选 + 数据列表
let btn6h = document.getElementById('btn-6h');
let btn24h = document.getElementById('btn-24h');
let btn3d = document.getElementById('btn-3d');
let refreshBtn = document.getElementById('btn-refresh');
let select = document.getElementById('select');

let chartDom = document.getElementById('chart');
let myChart = echarts.init(chartDom);

let currentLimit = 6;

function fieldName() {
    switch (select.value) {
        case 'temp': return '温度(℃)';
        case 'humi': return '湿度(%)';
        case 'light': return '光照强度(lux)';
        case 'soil': return '土壤湿度(%)';
        case 'water': return '水位(cm)';
        default: return '数值';
    }
}

function loadData() {
    let field = select.value;
    fetch('/history?limit=' + currentLimit + '&field=' + field)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            let times = data.times || [];
            let values = data.values || [];
            let name = fieldName();

            myChart.setOption({
                title: {
                    text: '传感器历史数据',
                    left: 'center',
                    textStyle: { color: '#555555', fontSize: 18 }
                },
                xAxis: {
                    type: 'category',
                    data: times,
                    axisLabel: { color: '#888888', fontSize: 12, rotate: 30 }
                },
                yAxis: {
                    type: 'value',
                    name: name,
                    nameLocation: 'middle',
                    nameGap: 30,
                    nameTextStyle: { color: '#888888', fontSize: 12 },
                    axisLabel: { color: '#888888', fontSize: 12 }
                },
                series: [{
                    data: values,
                    type: 'line',
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 6,
                    lineStyle: { color: '#5cb85c', width: 2 },
                    itemStyle: { color: '#fff', borderColor: '#5cb85c', borderWidth: 2 },
                    areaStyle: { color: 'rgba(92, 184, 92, 0.2)' }
                }],
                tooltip: {
                    trigger: 'axis',
                    formatter: function (params) {
                        const p = params[0];
                        return p.name + '<br/>' + name + ': ' + p.value;
                    }
                }
            });

            // 数据列表（最新在上）
            let tbody = document.getElementById('table-body');
            document.getElementById('th-value').textContent = name;
            tbody.innerHTML = '';
            if (times.length === 0) {
                tbody.innerHTML = '<tr><td colspan="2" style="padding:8px;color:#999;">暂无数据</td></tr>';
                return;
            }
            for (let i = times.length - 1; i >= 0; i--) {
                let tr = document.createElement('tr');
                tr.innerHTML = '<td style="padding:6px;border-bottom:1px solid #f5f5f5;">' + times[i] +
                    '</td><td style="padding:6px;border-bottom:1px solid #f5f5f5;text-align:right;">' + values[i] + '</td>';
                tbody.appendChild(tr);
            }
        })
        .catch(function () {
            document.getElementById('table-body').innerHTML =
                '<tr><td colspan="2" style="padding:8px;color:#c00;">数据加载失败</td></tr>';
        });
}

function setRange(limit) {
    currentLimit = limit;
    btn6h.classList.toggle('active', limit === 6);
    btn24h.classList.toggle('active', limit === 24);
    btn3d.classList.toggle('active', limit === 72);
    loadData();
}

btn6h.onclick = function () { setRange(6); };
btn24h.onclick = function () { setRange(24); };
btn3d.onclick = function () { setRange(72); };
select.onchange = loadData;
refreshBtn.onclick = loadData;

window.onload = function () {
    setRange(6);
};
