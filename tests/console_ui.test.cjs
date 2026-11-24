const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {
  formatTimestamp, validateSettings, alertRows, voiceRows, deviceRows
} = require('../frontend/console-ui.js');

test('timestamps use an explicit time zone and a readable seconds format', () => {
  assert.equal(formatTimestamp('2026-11-20T07:23:26.742Z', 'Asia/Shanghai'), '2026-11-20 15:23:26');
  assert.equal(formatTimestamp('2026-11-20T07:23:26.742Z', 'UTC'), '2026-11-20 07:23:26');
  assert.equal(formatTimestamp('invalid timestamp', 'Asia/Shanghai'), '时间未知');
  assert.equal(formatTimestamp(null, 'Asia/Shanghai'), '时间未知');
});

test('alert rows omit confidence and retain the direction tag', () => {
  const result = alertRows([{
    detected_at: '2026-11-20T07:23:26Z', object_class: 'person', direction: 'left',
    voice_text: '左侧有行人，请注意', confidence: 0.742
  }]);
  assert.equal((result.match(/<td\b/g) || []).length, 4);
  assert.match(result, /class="direction-tag">左侧<\/span>/);
  assert.match(result, /左侧有行人，请注意/);
  assert.doesNotMatch(result, /meter|74\.2%|置信度/i);
  assert.match(alertRows([]), /colspan="4"/);
});

test('alerts page has four columns and no confidence copy', () => {
  const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  const match = html.match(/<section\b(?=[^>]*\bid="alerts")[\s\S]*?<\/section>/);
  assert.ok(match, '告警记录页面存在');
  const section = match[0];
  const table = section.match(/<table\b[^>]*class="[^"]*\balerts-table\b[^"]*"[^>]*>[\s\S]*?<\/table>/);
  assert.ok(table, '告警记录表格带有 alerts-table 类');
  const headers = [...table[0].matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g)].map(item => item[1].trim());
  assert.deepEqual(headers, ['时间', '目标', '方位', '播报']);
  const columns = [...table[0].matchAll(/<col\b[^>]*class="([^"]+)"[^>]*>/g)].map(item => item[1]);
  assert.deepEqual(columns, ['alert-col-time', 'alert-col-target', 'alert-col-direction', 'alert-col-message']);
  const css = fs.readFileSync(path.join(__dirname, '../frontend/overview-theme.css'), 'utf8');
  const widths = columns.map(column => {
    const selector = '.alerts-table .' + column;
    const rule = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .find(item => item[1].split(',').some(value => value.trim() === selector));
    assert.ok(rule, column + ' 的列宽样式存在');
    const width = rule[2].match(/\bwidth:\s*(\d+)%/);
    assert.ok(width, column + ' 使用百分比列宽');
    return Number(width[1]);
  });
  assert.deepEqual(widths, [25, 15, 15, 45]);
  assert.doesNotMatch(section, /置信度/);
});

test('voice types are Chinese and simulated states use warning tags', () => {
  const result = voiceRows([{
    occurred_at: '2026-11-20T07:23:26Z', user_text: '前面有什么？', reply_text: '前方有书本',
    request_type: 'environment_query', tts_status: 'simulation'
  }]);
  assert.match(result, /环境查询/);
  assert.doesNotMatch(result, /environment_query/);
  assert.match(result, /class="status-tag warning"/);
  assert.match(result, /模拟/);
  assert.match(result, /chat-bubble-user/);
  assert.match(result, /chat-bubble-reply/);
});

test('record and device text is escaped before insertion into HTML', () => {
  const untrusted = '<img src=x onerror="alert(1)">';
  const result = [
    alertRows([{detected_at: untrusted, object_class: untrusted, direction: untrusted,
      voice_text: untrusted, confidence: 0.742}]),
    voiceRows([{occurred_at: untrusted, user_text: untrusted, reply_text: untrusted,
      request_type: untrusted, tts_status: 'failed'}]),
    deviceRows([{name: untrusted, last_heartbeat: untrusted, online: false}])
  ];
  for (const html of result) {
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
    assert.doesNotMatch(html, /onerror="alert/);
  }
  assert.match(alertRows([]), /暂无预警记录/);
  assert.match(voiceRows([]), /暂无语音交互记录/);
  assert.match(deviceRows([]), /等待设备接入/);
});

test('settings accept confidence endpoints and reject invalid ranges and cooldown values', () => {
  assert.deepEqual(validateSettings('0', '1'), {});
  assert.deepEqual(validateSettings('1', '1500'), {});
  assert.deepEqual(validateSettings('0.742', '800'), {});
  for (const confidence of ['', '-0.01', '1.01', 'NaN', 'Infinity', 'abc']) {
    assert.ok(validateSettings(confidence, '800').confidence);
  }
  for (const cooldown of ['', '0', '-1', '1.5', '1e3', 'Infinity', 'NaN', '9007199254740992']) {
    assert.ok(validateSettings('0.5', cooldown).cooldown);
  }
});

function mountHarness(customFetch) {
  const nodes = new Map();
  const requests = [];
  let config = {confidence: 0.35, alert_cooldown_ms: 1500, obstacle_enabled: true, voice_enabled: true};
  class Node {
    constructor() {
      this.listeners = new Map();
      this.attributes = new Map();
      this.value = '';
      this.checked = false;
      this.disabled = false;
      this.textContent = '';
      this.innerHTML = '';
      this.className = '';
      this.classList = {add() {}, remove() {}};
    }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    setCustomValidity(value) { this.validationMessage = value; }
    reportValidity() { this.reported = true; }
    focus() { this.focused = true; }
  }
  const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new Node());
    return nodes.get(id);
  }};
  const storage = {getItem() {return 'test-session';}};
  const context = {window: {document, sessionStorage: storage, localStorage: storage}, module: {exports: {}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/console-ui.js'), 'utf8'), context);
  const mounted = context.module.exports.mount({document, onConfig() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      const custom = customFetch && await customFetch(url, options);
      return custom || {ok: true, json: async () => url === '/api/config' ? config : []};
    }
  });
  return {nodes, requests, mounted, setConfig(value) {config = value;},
    input(id, value) {
      const node = document.getElementById(id);
      node.value = value;
      node.listeners.get('input')();
    },
    submit() {return nodes.get('configForm').listeners.get('submit')({preventDefault() {}});}
  };
}

test('a failed save shows the backend error and leaves settings editable', async () => {
  const h = mountHarness(async (url, options) => options.method === 'PUT'
    ? {ok: false, status: 503, json: async () => ({detail: '暂时无法保存'})} : null);
  await h.mounted.refresh();
  h.input('confidence', '0.6');
  await h.submit();
  assert.equal(h.nodes.get('saveMsg').textContent, '暂时无法保存');
  assert.equal(h.nodes.get('saveMsg').className, 'form-error');
  assert.equal(h.nodes.get('saveConfig').disabled, false);
  assert.equal(h.nodes.get('saveConfig').textContent, '保存设置');
  assert.equal(h.nodes.get('confidence').value, '0.6');
  assert.equal(h.requests.find(item => item.options.method === 'PUT').options.headers.Authorization, 'Bearer test-session');
});

test('periodic refresh does not overwrite unsaved parameter edits', async () => {
  const h = mountHarness();
  await h.mounted.refresh();
  h.input('confidence', '0.66');
  h.setConfig({confidence: 0.9, alert_cooldown_ms: 3000, obstacle_enabled: false, voice_enabled: false});
  await h.mounted.refresh();
  assert.equal(h.nodes.get('confidence').value, '0.66');
  assert.equal(h.nodes.get('cooldown').value, 1500);
  assert.equal(h.nodes.get('obstacle').checked, true);
  assert.equal(h.nodes.get('saveMsg').textContent, '有尚未保存的修改');
});

test('invalid settings do not send a save request', async () => {
  const h = mountHarness();
  await h.mounted.refresh();
  h.input('confidence', '1.3');
  await h.submit();
  assert.equal(h.requests.some(item => item.options.method === 'PUT'), false);
  assert.equal(h.nodes.get('confidence').attributes.get('aria-invalid'), 'true');
  assert.equal(h.nodes.get('confidence').reported, true);
  assert.equal(h.nodes.get('saveMsg').className, 'form-error');
});

test('new input during an in-flight save remains dirty after the earlier save completes', async () => {
  let finishSave;
  const pending = new Promise(resolve => {finishSave = resolve;});
  const h = mountHarness(async (url, options) => options.method === 'PUT' ? pending : null);
  await h.mounted.refresh();
  h.input('confidence', '0.6');
  const saving = h.submit();
  h.input('confidence', '0.8');
  finishSave({ok: true, json: async () => ({})});
  await saving;
  h.setConfig({confidence: 0.6, alert_cooldown_ms: 1500, obstacle_enabled: true, voice_enabled: true});
  await h.mounted.refresh();
  assert.equal(h.nodes.get('confidence').value, '0.8');
  assert.equal(h.nodes.get('saveMsg').textContent, '设置已保存，仍有新的修改待保存');
});
