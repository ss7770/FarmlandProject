/**
 * 病害识别看板（独立页 /disease）
 *
 * 数据来源：GET /api/disease/overview —— 一次请求拿到「设备状态 + 最新结果 + 记录列表」，
 * 避免页面里并发打三个接口、各自维护刷新节奏。
 *
 * 展示口径（2026-10-01 起，服务端已算好，前端不再自己判）：
 *   - 记录里带 diagnosed / verdict / display_name。
 *   - **未达确诊线的不做诊断**：display_name 是「置信率不足」，不显示病名、不显示档位徽章。
 *     （病名仍存在库里供复盘，只是不给看板/用户看，避免把不确定的结论当结论。）
 *   - 来源用"低调小标签"区分（巡检 / 上传），不强调设备型号。
 *
 * 唯一的动作：
 *   - 上传图片：本机选图走 /api/upload_image（服务端推理）。
 *
 * 【v15 2026-10-02】原「立即抓拍」按钮及其前后端链路（POST/GET /api/edge/command、
 * 板端 poll_command）已**整条删除**。本文件不再有任何"下发指令 / 追结果"的代码，
 * 页面只做「展示 + 上传」两件事。
 */
(function () {
    'use strict';

    var REFRESH_MS = 20000;          // 记录列表刷新间隔（与 APP 30 秒轮询错开）

    var elLatest = document.getElementById('db-latest');
    var elRecords = document.getElementById('db-records');
    var elDot = document.getElementById('db-device-dot');
    var elDevText = document.getElementById('db-device-text');
    var elDevMeta = document.getElementById('db-device-meta');
    var elCount = document.getElementById('db-count');
    var elStatus = document.getElementById('db-upload-status');
    var fileInput = document.getElementById('db-file');

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    /** 是否已确诊（服务端字段优先，缺省回落到 75% 阈值） */
    function isDiagnosed(rec) {
        if (typeof rec.diagnosed === 'boolean') return rec.diagnosed;
        return Number(rec.confidence || 0) >= 75;
    }

    /** 展示名：确诊给病名，未确诊一律「置信率不足」 */
    function displayName(rec) {
        if (rec.display_name) return rec.display_name;
        return isDiagnosed(rec) ? (rec.disease || '--') : '置信率不足';
    }

    /* ---------------- 设备状态 ---------------- */

    function renderDevice(edge) {
        edge = edge || {};
        if (!edge.online) {
            elDot.className = 'db-dot off';
            elDevText.textContent = edge.enabled === false
                ? '边缘识别未启用' : '等待设备上报…';
        } else {
            elDot.className = 'db-dot on';
            elDevText.textContent = '设备在线';
        }
        var meta = [];
        if (edge.report_count) meta.push('累计上报 ' + edge.report_count + ' 次');
        if (edge.last_ts) meta.push('最近 ' + edge.last_ts);
        elDevMeta.textContent = meta.join(' · ');
    }

    /* ---------------- 最新结果 ---------------- */

    /**
     * 最新结果。
     * 图片位与置信度位**无论如何都渲染**（没有数据时显示占位），
     * 这样区块高度稳定：设备还没上报 / 图片还在补传路上时也不塌、不跳版。
     */
    function renderLatest(rec) {
        var conf = rec ? Number(rec.confidence || 0) : null;
        var diagnosed = rec ? isDiagnosed(rec) : false;
        var showName = rec ? displayName(rec) : '暂无识别记录';

        var html = '';
        if (rec && rec.image_url) {
            html += '<div class="db-photo"><img src="' + esc(rec.image_url) +
                    '" alt="识别图" onclick="window.open(this.src)"></div>';
        } else {
            html += '<div class="db-photo db-photo-empty"><span>暂无图片</span></div>';
        }
        html += '<div class="db-latest-body">';
        html += '<div class="db-latest-name' + (rec && diagnosed ? '' : ' muted') + '">'
                + esc(showName) + '</div>';
        html += '<div class="db-latest-conf' + (rec && diagnosed ? '' : ' muted') + '">'
                + (conf === null ? '--%' : conf.toFixed(1) + '%') + '</div>';
        html += '<div class="db-latest-tags">';
        if (rec && diagnosed) {
            html += '<span class="db-verdict v-confirmed">确诊</span>';
        }
        if (rec) {
            html += '<span class="db-src">' + esc(rec.source_text || '') + '</span>';
            html += '<span class="db-time">' + esc(rec.timestamp || '') + '</span>';
        } else {
            html += '<span class="db-time">等待设备上报或手动上传</span>';
        }
        html += '</div></div>';
        elLatest.innerHTML = html;
    }

    /* ---------------- 记录列表 ---------------- */

    function renderRecords(records) {
        records = records || [];
        elCount.textContent = records.length ? '共 ' + records.length + ' 条' : '';
        if (!records.length) {
            elRecords.innerHTML = '<li class="db-empty">暂无记录</li>';
            return;
        }
        var html = '';
        records.forEach(function (rec) {
            var conf = Number(rec.confidence || 0);
            var diagnosed = isDiagnosed(rec);
            var thumb = rec.image_url
                ? '<img class="db-thumb" src="' + esc(rec.image_url) + '" alt="">'
                : '<span class="db-thumb db-thumb-empty"></span>';
            html += '<li class="db-record">' + thumb +
                    '<div class="db-record-main">' +
                    '<div class="db-record-name' + (diagnosed ? '' : ' muted') + '">' +
                    esc(displayName(rec)) + '</div>' +
                    '<div class="db-record-meta">' +
                    (diagnosed ? '<span class="db-verdict v-confirmed">确诊</span>' : '') +
                    ' ' + conf.toFixed(1) + '%' +
                    ' <span class="db-src">' + esc(rec.source_text || '') + '</span>' +
                    '</div></div>' +
                    '<div class="db-record-time">' + esc(rec.timestamp || '') + '</div>' +
                    '</li>';
        });
        elRecords.innerHTML = html;
    }

    /* ---------------- 加载 ---------------- */

    function renderAll(d) {
        renderDevice(d.edge);
        renderLatest(d.latest);
        renderRecords(d.records);
    }

    function fetchOverview(limit) {
        return fetch('/api/disease/overview?limit=' + (limit || 12))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') throw new Error(d.msg || '接口异常');
                return d;
            });
    }

    function load() {
        fetchOverview(12).then(renderAll).catch(function (e) {
            elDot.className = 'db-dot off';
            elDevText.textContent = '连接失败';
            elDevMeta.textContent = '';
            elRecords.innerHTML = '<li class="db-empty">加载失败：' + esc(e.message) + '</li>';
        });
    }

    /* ---------------- 上传识别 ---------------- */

    function upload(file) {
        if (!file) return;
        elStatus.textContent = '识别中…';
        var fd = new FormData();
        fd.append('image', file, 'upload.jpg');
        fetch('/api/upload_image', { method: 'POST', body: fd })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') {
                    elStatus.textContent = '失败：' + (d.msg || '未知错误');
                    return;
                }
                if (d.diagnosed === false) {
                    elStatus.textContent = '置信率不足，已保留图片与记录（不做诊断）';
                } else if (d.hasOwnProperty('disease')) {
                    elStatus.textContent = '识别完成（已写入记录）';
                } else {
                    elStatus.textContent = '图片已保存' + (d.msg ? '（' + d.msg + '）' : '');
                }
                load();
            })
            .catch(function (e) {
                elStatus.textContent = '上传失败：' + e.message;
            });
    }

    document.getElementById('db-btn-upload').addEventListener('click', function () {
        fileInput.click();
    });
    fileInput.addEventListener('change', function () {
        if (fileInput.files && fileInput.files[0]) upload(fileInput.files[0]);
        fileInput.value = '';
    });
    document.getElementById('db-btn-refresh').addEventListener('click', function () {
        elStatus.textContent = '';
        load();
    });

    load();
    window.setInterval(load, REFRESH_MS);
})();
