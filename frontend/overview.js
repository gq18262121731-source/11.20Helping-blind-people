(function (root) {
  'use strict';

  const directions = {left: '左侧', front: '前方', right: '右侧'};
  function escapeText(value) {
    return String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }
  function amount(value) { return Number.isFinite(Number(value)) ? Math.max(0, Math.floor(Number(value))) : 0; }
  function chartModel(points) {
    const days = points.map(point => ({date: /^\d{4}-\d{2}-\d{2}$/.test(point.date) ? point.date : '', real: amount(point.real), simulation: amount(point.simulation)}));
    const step = Math.max(1, Math.ceil(Math.max(1, ...days.flatMap(day => [day.real, day.simulation])) / 4));
    return {days, step, maximum: step * 4, total: days.reduce((sum, day) => sum + day.real + day.simulation, 0)};
  }
  function trendSVG(points) {
    const model = chartModel(points);
    const {days, maximum, step} = model;
    const x = index => 48 + index * (568 / Math.max(1, days.length - 1));
    const y = value => 190 - value / maximum * 154;
    let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 226" role="group" aria-label="近七天预警趋势，实线为实际采集，虚线为模拟设备。可聚焦日期查看数值，或展开下方趋势明细。">' +
      '<title>近七天预警趋势</title><defs>' +
      '<linearGradient id="qiming-trend-real-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#1677FF" stop-opacity=".2"/><stop offset="100%" stop-color="#1677FF" stop-opacity=".015"/></linearGradient>' +
      '<linearGradient id="qiming-trend-simulation-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#129B95" stop-opacity=".1"/><stop offset="100%" stop-color="#129B95" stop-opacity="0"/></linearGradient>' +
      '</defs><text x="14" y="17" fill="#667085" font-size="12">次数</text>';
    for (let tick = 0; tick <= 4; tick++) {
      const value = tick * step, top = y(value);
      svg += `<line x1="48" y1="${top}" x2="616" y2="${top}" stroke="#E5EAF1" stroke-opacity=".65" stroke-width="1"/>` +
        `<text x="37" y="${top + 4}" text-anchor="end" fill="#667085" font-size="12">${value}</text>`;
    }
    days.forEach((day, index) => {
      svg += `<text x="${x(index)}" y="215" text-anchor="middle" fill="#667085" font-size="12">${escapeText(day.date.slice(5).replace('-', '/'))}</text>`;
    });
    if (days.length) {
      for (const key of ['real', 'simulation']) {
        const line = days.map((day, index) => `${x(index)},${y(day[key])}`).join(' L');
        svg += `<path d="M${line} L${x(days.length - 1)},190 L${x(0)},190 Z" fill="url(#qiming-trend-${key}-fill)" stroke="none"/>`;
      }
    }
    for (const [key, color, label] of [['real', '#1677FF', '实际采集'], ['simulation', '#129B95', '模拟设备']]) {
      const coordinates = days.map((day, index) => `${x(index)},${y(day[key])}`).join(' ');
      svg += `<polyline points="${coordinates}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"${key === 'simulation' ? ' stroke-dasharray="6 5"' : ''}/>`;
      days.forEach((day, index) => {
        svg += `<circle cx="${x(index)}" cy="${y(day[key])}" r="4" fill="#FFFFFF" stroke="${color}" stroke-width="2"><title>${escapeText(day.date)} ${label}：${day[key]} 次</title></circle>`;
      });
    }
    days.forEach((day, index) => {
      const left = index === 0 ? 40 : (x(index - 1) + x(index)) / 2;
      const right = index === days.length - 1 ? 624 : (x(index) + x(index + 1)) / 2;
      const label = `${day.date || '日期未知'}，实际采集 ${day.real} 次，模拟设备 ${day.simulation} 次`;
      svg += `<g class="trend-hit" tabindex="0" data-trend-index="${index}" aria-label="${escapeText(label)}" aria-describedby="overviewTrendTooltip">` +
        `<line class="trend-cursor" x1="${x(index)}" y1="30" x2="${x(index)}" y2="190" stroke="#A9C8F6" stroke-dasharray="3 4"/>` +
        `<rect x="${left}" y="26" width="${right - left}" height="174" fill="transparent"/></g>`;
    });
    if (!model.total) svg += '<text x="332" y="110" text-anchor="middle" fill="#667085" font-size="14">近 7 天暂无预警记录</text>';
    return svg + '</svg>';
  }
  function element(document, tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function stamp(value, timeZone, full = false) {
    if (value === null || value === undefined || value === '') return '时间未知';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '时间未知';
    return new Intl.DateTimeFormat('zh-CN', {timeZone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', ...(full ? {year: 'numeric', second: '2-digit'} : {}), hourCycle: 'h23'}).format(date);
  }
  function timeOnly(value, timeZone) {
    if (value === null || value === undefined || value === '') return '时间未知';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '时间未知';
    return new Intl.DateTimeFormat('zh-CN', {timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}).format(date);
  }
  function relativeTime(value, now = Date.now()) {
    if (value === null || value === undefined || value === '') return '';
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp) || !Number.isFinite(now)) return '';
    const elapsed = now - timestamp;
    const seconds = Math.abs(elapsed) / 1000;
    if (seconds < 60) return elapsed < 0 ? '片刻后' : '刚刚';
    const units = [[31536000, '年'], [2592000, '个月'], [86400, '天'], [3600, '小时'], [60, '分钟']];
    const [size, label] = units.find(([size]) => seconds >= size);
    return `${Math.floor(seconds / size)}${label}${elapsed < 0 ? '后' : '前'}`;
  }

  function trendTable(points) {
    const {days} = chartModel(points);
    if (!days.length) return '';
    return '<details class="overview-chart-data"><summary>查看趋势明细</summary><div class="overview-chart-table-wrap"><table><caption>近 7 天预警次数</caption><thead><tr><th scope="col">日期</th><th scope="col">实际采集</th><th scope="col">模拟设备</th></tr></thead><tbody>' +
      days.map(day => `<tr><th scope="row">${escapeText(day.date || '日期未知')}</th><td>${day.real}</td><td>${day.simulation}</td></tr>`).join('') + '</tbody></table></div></details>';
  }

  function mount(options = {}) {
    const document = options.document || root.document;
    const fetch = options.fetch || root.fetch.bind(root);
    const timeZone = options.timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
    const getCamera = options.getCamera || (() => ({active: false}));
    const getToken = options.getToken || (() => root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token'));
    const $ = id => document.getElementById(id);
    let last = null, pending = false, closed = false, latestError = false, controller = null, clearTrendFocusListeners = null;

    function renderTrend(points) {
      const container = $('overviewTrend');
      if (clearTrendFocusListeners) clearTrendFocusListeners();
      container.innerHTML = trendSVG(points) + trendTable(points);
      const tooltip = element(document, 'div', 'trend-tooltip');
      tooltip.id = 'overviewTrendTooltip';
      tooltip.role = 'tooltip';
      tooltip.hidden = true;
      container.append(tooltip);
      const {days} = chartModel(points);
      function showTooltip(event) {
        const hit = event.target.closest && event.target.closest('[data-trend-index]');
        if (!hit || !container.contains(hit)) return;
        const day = days[Number(hit.dataset.trendIndex)];
        if (!day) return;
        tooltip.replaceChildren(
          element(document, 'strong', 'trend-tooltip-date', day.date || '日期未知'),
          element(document, 'span', 'trend-tooltip-real', `实际采集 ${day.real} 次`),
          element(document, 'span', 'trend-tooltip-simulation', `模拟设备 ${day.simulation} 次`)
        );
        tooltip.hidden = false;
        const chartBounds = container.getBoundingClientRect();
        const pointBounds = hit.getBoundingClientRect();
        const center = pointBounds.left - chartBounds.left + pointBounds.width / 2;
        tooltip.style.left = Math.max(8, Math.min(chartBounds.width - tooltip.offsetWidth - 8, center - tooltip.offsetWidth / 2)) + 'px';
      }
      container.onpointermove = showTooltip;
      container.onpointerdown = showTooltip;
      container.onpointerleave = () => { tooltip.hidden = true; };
      container.onkeydown = event => { if (event.key === 'Escape') tooltip.hidden = true; };
      if (typeof container.addEventListener === 'function') {
        const onFocusIn = event => showTooltip(event);
        const onFocusOut = event => {
          const next = event.relatedTarget;
          if (!next || !container.contains(next)) tooltip.hidden = true;
        };
        container.addEventListener('focusin', onFocusIn);
        container.addEventListener('focusout', onFocusOut);
        clearTrendFocusListeners = () => {
          container.removeEventListener('focusin', onFocusIn);
          container.removeEventListener('focusout', onFocusOut);
        };
      }
    }

    function enhanceMetricCards() {
      const destinations = ['monitor', 'alerts', 'voices', 'monitor'];
      const labels = ['查看设备实时监测', '查看告警记录', '查看语音交互', '查看视觉模型实时监测'];
      const icons = [
        '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M9 17h6M8 7h8M8 11h8"/>',
        '<path d="m12 3 9 17H3L12 3Z"/><path d="M12 9v4M12 16h.01"/>',
        '<path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h5"/>',
        '<rect x="5" y="5" width="14" height="14" rx="3"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>'
      ];
      document.querySelectorAll('.overview-metrics .metric-card').forEach((card, index) => {
        if (!destinations[index]) return;
        card.dataset.overviewPage = destinations[index];
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        card.setAttribute('aria-label', labels[index]);
        const icon = element(document, 'span', 'metric-art');
        icon.ariaHidden = 'true';
        icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${icons[index]}</svg>`;
        card.append(icon);
        card.onkeydown = event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            card.click();
          }
        };
      });
    }

    function cameraStatus() {
      const camera = getCamera();
      const state = $('overviewCameraState'), detail = $('overviewCameraDetail');
      state.textContent = camera.active ? '已开启' : '未开启';
      state.className = 'overview-state ' + (camera.active ? 'ready' : 'idle');
      detail.textContent = camera.active ? '本地摄像头正在采集' : '在实时环境监测中开启';
    }
    function updateDutyNotice() {
      const notice = $('overviewNotice'), hook = $('overviewHook');
      if (!notice || !hook) return;
      const realOnline = Boolean(last && Number(last.devices.real_online) > 0);
      notice.hidden = !latestError && (realOnline || Boolean(getCamera().active));
      const label = latestError ? '状态更新失败' : last ? '未连接设备' : '正在检查设备连接…';
      if (hook.textContent !== label) hook.textContent = label;
    }
    function status(id, label, detail, tone) {
      $(id + 'State').textContent = label;
      $(id + 'State').className = 'overview-state ' + tone;
      $(id + 'Detail').textContent = detail;
    }
    function renderRows(id, rows, type) {
      const container = $(id);
      container.replaceChildren();
      if (!rows.length) {
        container.append(element(document, 'li', 'overview-empty', type === 'alert' ? '暂无预警记录' : '暂无交互记录'));
        return;
      }
      for (const row of rows) {
        const item = element(document, 'li', 'overview-record');
        const top = element(document, 'div', 'overview-record-top');
        const name = type === 'alert' ? (root.QimingCameraSpeech ? root.QimingCameraSpeech.objectName(row.object_class) : row.object_class) : (row.user_text || '未提供问题');
        const title = element(document, 'strong', 'overview-record-title', name);
        title.title = name;
        top.append(title, element(document, 'span', 'overview-source ' + (row.source === 'simulation' ? 'simulation' : 'real'), row.source === 'simulation' ? (type === 'alert' ? '模拟设备' : '软件联调') : (type === 'alert' ? '实际采集' : '设备交互')));
        const body = type === 'alert' ? `${directions[row.direction] || '方位未知'} · 置信度 ${Math.round(Math.max(0, Math.min(1, Number(row.confidence) || 0)) * 100)}%` : (row.reply_text || '暂无回复');
        const description = element(document, 'p', 'overview-record-description', body);
        description.title = body;
        const timestamp = type === 'alert' ? row.detected_at : row.occurred_at;
        const time = element(document, 'time', 'overview-record-time', stamp(timestamp, timeZone));
        const parsed = new Date(timestamp);
        if (timestamp && Number.isFinite(parsed.getTime())) time.dateTime = parsed.toISOString();
        time.title = timestamp ? stamp(timestamp, timeZone, true) : '时间未知';
        const relative = relativeTime(timestamp);
        if (relative) time.append(element(document, 'span', 'overview-relative-time', `（${relative}）`));
        const icon = element(document, 'span', 'overview-record-icon');
        icon.ariaHidden = 'true';
        icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + (type === 'alert' ? '<path d="m12 4 8 15H4l8-15Z"/><path d="M12 9v4M12 16h.01"/>' : '<path d="M4 5h16v11H9l-5 3V5Z"/><path d="M8 9h8M8 12h5"/>') + '</svg>';
        item.append(top, description, time, icon);
        container.append(item);
      }
    }
    function render(data) {
      last = data;
      latestError = false;
      const devices = data.devices;
      updateDutyNotice();
      if ($('overviewTimezone')) $('overviewTimezone').textContent = data.timezone || timeZone;
      $('device').textContent = devices.real_online ? '在线' : devices.seen ? '离线' : '未接入';
      $('deviceNote').textContent = devices.real_online ? `帽子设备在线 ${devices.real_online} 台` : devices.simulation_online ? `模拟设备在线 ${devices.simulation_online} 台` : '暂无帽子设备连接';
      $('alertCount').textContent = data.today_alerts.total;
      $('voiceCount').textContent = data.today_voices.total;
      $('alertNote').textContent = `实际采集 ${data.today_alerts.real} · 模拟设备 ${data.today_alerts.simulation}`;
      $('voiceNote').textContent = `设备交互 ${data.today_voices.real} · 软件联调 ${data.today_voices.simulation}`;
      $('model').textContent = data.mode === 'simulation' ? '模拟模式' : data.yolo.state === 'ready' ? '检测就绪' : '检测未就绪';
      $('modelNote').textContent = data.mode === 'simulation' ? '模拟设备使用模拟结果' : '实际图像检测模式';
      if ($('navAlertCount')) $('navAlertCount').textContent = data.today_alerts.total;
      renderTrend(data.trend);
      const chart = chartModel(data.trend);
      $('overviewTrendSummary').textContent = `7 天共 ${chart.total} 次预警 · 模拟设备单独标记`;
      status('overviewBackend', '已连接', '后端服务响应正常', 'ready');
      status('overviewYolo', data.yolo.label, data.yolo.detail, data.yolo.state === 'ready' ? 'ready' : 'error');
      status('overviewLlm', data.llm.label, data.llm.detail, data.llm.state === 'available' ? 'ready' : data.llm.state === 'error' || data.llm.state === 'offline' ? 'error' : 'warning');
      status('overviewDevice', devices.real_online ? '已连接' : devices.seen ? '离线' : '未接入', devices.real_online ? `在线 ${devices.real_online} 台` : '当前无帽子设备连接', devices.real_online ? 'ready' : 'idle');
      cameraStatus();
      renderRows('overviewAlerts', data.recent_alerts, 'alert');
      renderRows('overviewVoices', data.recent_voices, 'voice');
      $('overviewUpdated').textContent = `更新于 ${timeOnly(data.generated_at, timeZone)}`;
      $('overviewUpdated').classList.remove('bad');
    }
    async function refresh() {
      if (closed || pending) return;
      pending = true;
      controller = new AbortController();
      try {
        const response = await fetch('/api/overview?tz=' + encodeURIComponent(timeZone), {
          headers: {Authorization: 'Bearer ' + (getToken() || '')}, signal: controller.signal,
        });
        if (!response.ok) throw new Error(response.status === 401 ? '登录已过期，请重新登录' : '统计服务暂不可用，请确认已重启后端');
        const data = await response.json();
        if (!closed) render(data);
      } catch (error) {
        if (closed || error.name === 'AbortError') return;
        latestError = true;
        updateDutyNotice();
        $('overviewUpdated').textContent = error.message + (last ? '；当前保留上次更新的数据' : '');
        $('overviewUpdated').classList.add('bad');
        status('overviewBackend', '更新失败', '请检查后端连接', 'error');
        if (!last) {
          $('overviewTrend').textContent = '统计暂不可用';
          for (const id of ['overviewAlerts', 'overviewVoices']) $(id).textContent = '记录暂不可用';
        }
      } finally {
        pending = false;
        cameraStatus();
      }
    }
    enhanceMetricCards();
    for (const button of document.querySelectorAll('[data-overview-page]')) {
      button.onclick = () => {
        const target = document.querySelector('nav button[data-page="' + button.dataset.overviewPage + '"]');
        if (target) target.click();
      };
    }
    cameraStatus();
    const interval = root.setInterval(refresh, 10000);
    root.addEventListener('qiming-camera-started', () => {
      cameraStatus();
      updateDutyNotice();
    });
    root.addEventListener('qiming-camera-stopped', () => {
      status('overviewCamera', '未开启', '在实时环境监测中开启', 'idle');
      updateDutyNotice();
    });
    root.addEventListener('qiming-device-status', refresh);
    root.addEventListener('pagehide', () => {
      closed = true;
      root.clearInterval(interval);
      if (controller) controller.abort();
    });
    const ready = refresh();
    return {refresh, ready};
  }
  const api = {mount, chartModel, trendSVG, trendTable, relativeTime, stamp, timeOnly};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingOverview = api;
})(typeof window === 'undefined' ? globalThis : window);
