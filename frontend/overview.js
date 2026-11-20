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
    let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 226" role="img" aria-label="近七天预警趋势，按实际采集和模拟设备分别统计">' +
      '<title>近七天预警趋势</title><text x="14" y="17" fill="#6c8491" font-size="12">次数</text>';
    for (let tick = 0; tick <= 4; tick++) {
      const value = tick * step, top = y(value);
      svg += `<line x1="48" y1="${top}" x2="616" y2="${top}" stroke="#d6e5e7" stroke-width="1"/>` +
        `<text x="37" y="${top + 4}" text-anchor="end" fill="#6c8491" font-size="12">${value}</text>`;
    }
    days.forEach((day, index) => {
      svg += `<text x="${x(index)}" y="215" text-anchor="middle" fill="#6c8491" font-size="12">${escapeText(day.date.slice(5).replace('-', '/'))}</text>`;
    });
    for (const [key, color, label] of [['real', '#006d73', '实际采集'], ['simulation', '#a76500', '模拟设备']]) {
      const coordinates = days.map((day, index) => `${x(index)},${y(day[key])}`).join(' ');
      svg += `<polyline points="${coordinates}" fill="none" stroke="${color}" stroke-width="3"${key === 'simulation' ? ' stroke-dasharray="6 5"' : ''}/>`;
      days.forEach((day, index) => {
        svg += `<circle cx="${x(index)}" cy="${y(day[key])}" r="4" fill="#edf5f6" stroke="${color}" stroke-width="2"><title>${escapeText(day.date)} ${label}：${day[key]} 次</title></circle>`;
      });
    }
    if (!model.total) svg += '<text x="332" y="110" text-anchor="middle" fill="#6c8491" font-size="14">近 7 天暂无预警记录</text>';
    return svg + '</svg>';
  }
  function element(document, tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function stamp(value, timeZone, full = false) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '时间未知';
    return new Intl.DateTimeFormat('zh-CN', {timeZone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', ...(full ? {second: '2-digit'} : {}), hourCycle: 'h23'}).format(date);
  }

  function mount(options = {}) {
    const document = options.document || root.document;
    const fetch = options.fetch || root.fetch.bind(root);
    const timeZone = options.timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
    const getCamera = options.getCamera || (() => ({active: false}));
    const getToken = options.getToken || (() => root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token'));
    const $ = id => document.getElementById(id);
    let last = null, pending = false, closed = false, controller = null;

    function cameraStatus() {
      const camera = getCamera();
      const state = $('overviewCameraState'), detail = $('overviewCameraDetail');
      state.textContent = camera.active ? '已开启' : '未开启';
      state.className = 'overview-state ' + (camera.active ? 'ready' : 'idle');
      detail.textContent = camera.active ? '本地摄像头正在采集' : '在实时环境监测中开启';
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
        item.append(top, description, element(document, 'time', 'overview-record-time', stamp(type === 'alert' ? row.detected_at : row.occurred_at, timeZone)));
        container.append(item);
      }
    }
    function render(data) {
      last = data;
      const devices = data.devices;
      const latestAlert = data.recent_alerts && data.recent_alerts[0];
      const latestName = latestAlert && (root.QimingCameraSpeech ? root.QimingCameraSpeech.objectName(latestAlert.object_class) : latestAlert.object_class);
      const latestDirection = latestAlert ? (directions[latestAlert.direction] || '方位未知') : '';
      const realOnline = Number(devices.real_online || 0);
      const hook = $('overviewHook');
      const hookDetail = $('overviewHookDetail');
      if (hook && hookDetail) {
        if (realOnline) {
          hook.textContent = latestAlert ? `设备在线 · 最近发现${latestName}（${latestDirection}）` : '设备在线 · 当前没有待处理告警';
          hookDetail.textContent = latestAlert ? `今天已记录 ${data.today_alerts.total} 次预警，先确认这条告警是否需要介入。` : '视觉链路已连通，保持实时监测即可。';
        } else if (devices.simulation_online) {
          hook.textContent = latestAlert ? `模拟设备在线 · 最近发现${latestName}（${latestDirection}）` : '模拟设备已上线 · 可以开始联调';
          hookDetail.textContent = '当前数据来自软件联调，请在实时监测中切换到帽端或电脑画面。';
        } else {
          hook.textContent = '还没有帽端数据 · 先连接设备或启动电脑摄像头';
          hookDetail.textContent = '收到第一帧后，这里会告诉你今天最该先看什么。';
        }
      }
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
      $('overviewTrend').innerHTML = trendSVG(data.trend);
      const chart = chartModel(data.trend);
      $('overviewTrendSummary').textContent = `7 天共 ${chart.total} 次预警 · 模拟设备单独标记`;
      status('overviewBackend', '已连接', '后端服务响应正常', 'ready');
      status('overviewYolo', data.yolo.label, data.yolo.detail, data.yolo.state === 'ready' ? 'ready' : 'warning');
      status('overviewLlm', data.llm.label, data.llm.detail, data.llm.state === 'available' ? 'ready' : 'warning');
      status('overviewDevice', devices.real_online ? '已连接' : devices.seen ? '离线' : '未接入', devices.real_online ? `在线 ${devices.real_online} 台` : '当前无帽子设备连接', devices.real_online ? 'ready' : 'idle');
      cameraStatus();
      renderRows('overviewAlerts', data.recent_alerts, 'alert');
      renderRows('overviewVoices', data.recent_voices, 'voice');
      $('overviewUpdated').textContent = `更新于 ${stamp(data.generated_at, timeZone, true)} · 按 ${data.timezone} 统计 · 每 10 秒刷新`;
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
        $('overviewUpdated').textContent = error.message + (last ? '；当前保留上次更新的数据' : '');
        $('overviewUpdated').classList.add('bad');
        status('overviewBackend', '更新失败', '请检查后端连接', 'warning');
        if (!last) {
          $('overviewTrend').textContent = '统计暂不可用';
          for (const id of ['overviewAlerts', 'overviewVoices']) $(id).textContent = '记录暂不可用';
        }
      } finally {
        pending = false;
        cameraStatus();
      }
    }
    for (const button of document.querySelectorAll('[data-overview-page]')) {
      button.onclick = () => {
        const target = document.querySelector('nav button[data-page="' + button.dataset.overviewPage + '"]');
        if (target) target.click();
      };
    }
    cameraStatus();
    const interval = root.setInterval(refresh, 10000);
    root.addEventListener('qiming-camera-started', cameraStatus);
    root.addEventListener('qiming-camera-stopped', () => {
      status('overviewCamera', '未开启', '在实时环境监测中开启', 'idle');
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
  const api = {mount, chartModel, trendSVG};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingOverview = api;
})(typeof window === 'undefined' ? globalThis : window);
