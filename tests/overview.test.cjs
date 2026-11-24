const test = require('node:test');
const assert = require('node:assert/strict');
const {chartModel, trendSVG, trendTable, relativeTime, stamp} = require('../frontend/overview.js');

test('trend includes zero, separates sources and scales above the largest count', () => {
  const data = chartModel([{date: '2026-11-17', real: 134, simulation: 8}]);
  assert.equal(data.total, 142);
  assert.ok(data.maximum >= 134);
  const svg = trendSVG(data.days);
  assert.match(svg, /polyline/);
  assert.match(svg, /stroke-dasharray/);
  assert.match(svg, /实际采集：134/);
  assert.match(svg, />0<\/text>/);
});

test('trend lines use the branded blue and readable aqua palette', () => {
  const svg = trendSVG([
    {date: '2026-11-19', real: 1, simulation: 2},
    {date: '2026-11-20', real: 3, simulation: 4}
  ]);
  assert.match(svg, /stroke="#1677FF"/);
  assert.match(svg, /stroke="#129B95"/);
  assert.match(svg, /linearGradient id="qiming-trend-real-fill"/);
  assert.match(svg, /fill="url\(#qiming-trend-real-fill\)"/);
  assert.match(svg, /class="trend-hit" tabindex="0"/);
  assert.match(svg, /role="group" aria-label="近七天预警趋势/);
});

test('trend keeps a tabular fallback and exposes relative time labels', () => {
  const table = trendTable([{date: '2026-11-20', real: 12, simulation: 3}]);
  assert.match(table, /<details class="overview-chart-data">/);
  assert.match(table, /<summary>查看趋势明细<\/summary>/);
  assert.match(table, /<td>12<\/td><td>3<\/td>/);
  assert.doesNotMatch(trendTable([{date: '<script>bad</script>', real: 0, simulation: 0}]), /<script>/);
  const epoch = Date.parse('2026-11-20T07:23:26Z');
  assert.equal(relativeTime(epoch, epoch), '刚刚');
  assert.equal(relativeTime(epoch - 60_000, epoch), '1分钟前');
  assert.equal(relativeTime(epoch - 7_200_000, epoch), '2小时前');
  assert.equal(relativeTime(epoch + 300_000, epoch), '5分钟后');
  assert.equal(relativeTime('invalid', epoch), '');
  assert.equal(stamp('', 'Asia/Shanghai'), '时间未知');
});

test('empty and invalid dates never create broken SVG or inject markup', () => {
  const svg = trendSVG([{date: '<script>alert(1)</script>', real: -1, simulation: NaN}]);
  assert.match(svg, /暂无预警记录/);
  assert.doesNotMatch(svg, /<script|NaN|Infinity/);
});

function harness(fetchResult, {cameraActive = true} = {}) {
  const vm = require('node:vm');
  const fs = require('node:fs');
  const path = require('node:path');
  class Node {
    constructor() {this.children = []; this.className = ''; this.classList = {add() {}, remove() {}};}
    append(...nodes) {this.children.push(...nodes);}
    replaceChildren(...nodes) {this.children = nodes;}
  }
  const nodes = new Map();
  const events = new Map();
  const camera = {active: cameraActive};
  const document = {getElementById(id) {if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id);},
    createElement() {return new Node();}, querySelectorAll() {return [];}};
  const requests = [];
  const window = {document, setInterval() {return 1;}, clearInterval() {}, addEventListener(name, callback) {events.set(name, callback);}};
  const context = {window, module: {exports: {}}, AbortController, Intl, Date};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/overview.js'), 'utf8'), context);
  const api = context.module.exports;
  const mounted = api.mount({document, timeZone: 'Asia/Shanghai', getToken: () => 'test-session', getCamera: () => camera,
    fetch: async (url, options) => {requests.push({url, options}); return fetchResult();}});
  return {nodes, events, requests, mounted, camera};
}

test('overview renders actual totals, distinguishes simulated devices and uses safe text nodes', async () => {
  const data = {devices: {real_online: 0, simulation_online: 1, seen: 0},
    today_alerts: {total: 134, real: 130, simulation: 4}, today_voices: {total: 2, real: 0, simulation: 2},
    mode: 'simulation', trend: [{date: '2026-11-17', real: 130, simulation: 4}],
    yolo: {state: 'ready', label: '已加载', detail: 'test'}, llm: {state: 'offline', label: '服务未连接', detail: 'test'},
    recent_alerts: [], recent_voices: [{user_text: '<img src=x onerror=alert(1)>', reply_text: '<script>bad</script>', source: 'simulation', occurred_at: '2026-11-17T00:00:00Z'}],
    generated_at: '2026-11-17T01:00:00Z', timezone: 'Asia/Shanghai'};
  const h = harness(async () => ({ok: true, json: async () => data}));
  await h.mounted.ready;
  assert.equal(h.nodes.get('alertCount').textContent, 134);
  assert.equal(h.nodes.get('device').textContent, '未接入');
  assert.equal(h.nodes.get('model').textContent, '模拟模式');
  assert.equal(h.nodes.get('overviewLlmState').textContent, '服务未连接');
  assert.equal(h.nodes.get('overviewCameraState').textContent, '已开启');
  const record = h.nodes.get('overviewVoices').children[0];
  assert.equal(record.children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(record.children[1].textContent, '<script>bad</script>');
  assert.match(h.requests[0].url, /tz=Asia%2FShanghai/);
  assert.equal(h.requests[0].options.headers.Authorization, 'Bearer test-session');
  h.camera.active = false;
  h.events.get('qiming-camera-stopped')();
  assert.equal(h.nodes.get('overviewCameraState').textContent, '未开启');
  h.events.get('pagehide')();
});

test('overview notice tracks device connection and keeps simulation visible', async () => {
  let data = {devices: {real_online: 0, simulation_online: 0, seen: 0},
    today_alerts: {total: 0, real: 0, simulation: 0}, today_voices: {total: 0, real: 0, simulation: 0},
    mode: 'real', trend: [], yolo: {state: 'ready', label: '就绪', detail: ''},
    llm: {state: 'offline', label: '未连接', detail: ''}, recent_alerts: [], recent_voices: [],
    generated_at: '2026-11-17T01:02:03Z', timezone: 'Asia/Shanghai'};
  const h = harness(async () => ({ok: true, json: async () => data}), {cameraActive: false});
  await h.mounted.ready;
  const notice = h.nodes.get('overviewNotice');
  assert.equal(notice.hidden, false);
  assert.equal(h.nodes.get('overviewHook').textContent, '未连接设备');
  assert.equal(h.nodes.get('overviewUpdated').textContent, '更新于 09:02:03');
  assert.match(h.nodes.get('overviewUpdated').textContent, /^更新于 \d{2}:\d{2}:\d{2}$/);
  assert.doesNotMatch(h.nodes.get('overviewUpdated').textContent, /2026|Asia\/Shanghai|刷新/);

  data = {...data, devices: {...data.devices, simulation_online: 1}};
  await h.mounted.refresh();
  assert.equal(notice.hidden, false);

  data = {...data, devices: {...data.devices, real_online: 1}};
  await h.mounted.refresh();
  assert.equal(notice.hidden, true);

  data = {...data, devices: {...data.devices, real_online: 0}};
  await h.mounted.refresh();
  assert.equal(notice.hidden, false);
  h.events.get('pagehide')();
});

test('overview notice updates immediately when the local camera starts and stops', async () => {
  const data = {devices: {real_online: 0, simulation_online: 0, seen: 0},
    today_alerts: {total: 0, real: 0, simulation: 0}, today_voices: {total: 0, real: 0, simulation: 0},
    mode: 'real', trend: [], yolo: {state: 'ready', label: '就绪', detail: ''},
    llm: {state: 'offline', label: '未连接', detail: ''}, recent_alerts: [], recent_voices: [],
    generated_at: '2026-11-17T01:02:03Z', timezone: 'Asia/Shanghai'};
  const h = harness(async () => ({ok: true, json: async () => data}), {cameraActive: false});
  await h.mounted.ready;
  const notice = h.nodes.get('overviewNotice');
  assert.equal(notice.hidden, false);

  h.camera.active = true;
  h.events.get('qiming-camera-started')();
  assert.equal(notice.hidden, true);

  h.camera.active = false;
  h.events.get('qiming-camera-stopped')();
  assert.equal(notice.hidden, false);
  h.events.get('pagehide')();
});

test('overview notice reappears with an error when status refresh fails', async () => {
  const data = {devices: {real_online: 1, simulation_online: 0, seen: 1},
    today_alerts: {total: 0, real: 0, simulation: 0}, today_voices: {total: 0, real: 0, simulation: 0},
    mode: 'real', trend: [], yolo: {state: 'ready', label: '就绪', detail: ''},
    llm: {state: 'offline', label: '未连接', detail: ''}, recent_alerts: [], recent_voices: [],
    generated_at: '2026-11-17T01:02:03Z', timezone: 'Asia/Shanghai'};
  let fail = false;
  const h = harness(async () => fail ? {ok: false, status: 503} : {ok: true, json: async () => data},
    {cameraActive: false});
  await h.mounted.ready;
  const notice = h.nodes.get('overviewNotice');
  assert.equal(notice.hidden, true);

  fail = true;
  await h.mounted.refresh();
  assert.equal(notice.hidden, false);
  assert.equal(h.nodes.get('overviewHook').textContent, '状态更新失败');
  assert.match(h.nodes.get('overviewUpdated').textContent, /统计服务暂不可用/);
  assert.equal(h.nodes.get('overviewBackendState').textContent, '更新失败');

  fail = false;
  await h.mounted.refresh();
  assert.equal(notice.hidden, true);
  assert.equal(h.nodes.get('overviewUpdated').textContent, '更新于 09:02:03');
  assert.equal(h.nodes.get('overviewBackendState').textContent, '已连接');
  h.events.get('pagehide')();
});

test('failed requests show an explicit failure instead of zero or healthy status', async () => {
  const h = harness(async () => ({ok: false, status: 401}));
  await h.mounted.ready;
  assert.match(h.nodes.get('overviewUpdated').textContent, /登录已过期/);
  assert.equal(h.nodes.get('overviewNotice').hidden, false);
  assert.equal(h.nodes.get('overviewHook').textContent, '状态更新失败');
  assert.equal(h.nodes.get('overviewBackendState').textContent, '更新失败');
  assert.equal(h.nodes.get('overviewAlerts').textContent, '记录暂不可用');
  h.events.get('pagehide')();
});
