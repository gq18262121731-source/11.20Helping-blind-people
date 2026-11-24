const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function makeNode() {
  const classes = new Set();
  return {
    hidden: false, disabled: false, textContent: '', innerHTML: '', value: '', className: '',
    dataset: {}, children: [], attributes: {},
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); }
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); return item; },
    replaceChildren(...items) { this.children = items; },
    querySelector() { return makeNode(); },
    addEventListener() {}
  };
}

function mountGuard({owner = true, getPosition, locationSource = null, mapAk = '', activeGuard = false, activeAssistant = false} = {}) {
  const nodes = new Map();
  const getNode = id => {
    if (!nodes.has(id)) nodes.set(id, makeNode());
    return nodes.get(id);
  };
  if (activeGuard) getNode('guardPage').classList.add('active');
  if (activeAssistant) getNode('assistantPage').classList.add('active');
  const requests = [];
  const appendedResources = [];
  const watchCallbacks = {};
  const clearedWatches = [];
  let positionCalls = 0;
  const position = {coords: {latitude: 31.2304, longitude: 121.4737, accuracy: 11, speed: null, heading: null}, timestamp: Date.now()};
  const geolocation = {
    getCurrentPosition(success, failure, options) {
      positionCalls += 1;
      if (getPosition) return getPosition(success, failure, options);
      success(position);
    },
    watchPosition(success, failure, options) {
      Object.assign(watchCallbacks, {success, failure, options});
      return 42;
    },
    clearWatch(id) { clearedWatches.push(id); }
  };
  const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body});
  const root = {
    document: {
      getElementById: getNode,
      createElement(tag) { const node = makeNode(); node.tagName = String(tag).toUpperCase(); return node; },
      querySelectorAll: () => [],
      querySelector: () => makeNode(),
      head: {appendChild(node) {
        appendedResources.push(node);
        if (node.tagName === 'SCRIPT' && node.src.includes('/getscript?')) {
          root.BMapGL = {Map: function Map() {}, Convertor: function Convertor() {}, Point: function Point() {}};
        }
        if (typeof node.onload === 'function') node.onload();
      }}
    },
    localStorage: {getItem: key => key === 'qh_guard_device' ? 'hat-local' : null, setItem() {}},
    sessionStorage: {getItem: () => 'session-test'},
    location: {hostname: '127.0.0.1', protocol: 'http:', host: '127.0.0.1:8765'},
    navigator: {geolocation},
    fetch: async (url, init = {}) => {
      requests.push({url, init});
      const body = init.body ? JSON.parse(init.body) : null;
      if (url === '/api/v1/guard/devices') return response([{device_id: 'hat-local', name: '本机设备', owner, sharing: true, location_source: locationSource}]);
      if (url === '/api/v1/map/config') return response({configured: Boolean(mapAk), js_provider: 'baidu', js_key: mapAk});
      if (url.startsWith('/api/v1/location/latest')) return response({available: false, location: null});
      if (url.startsWith('/api/v1/navigation/status')) return response({status: 'IDLE'});
      if (url.startsWith('/api/v1/devices/status')) return response({online: false});
      if (url.startsWith('/api/v1/events')) return response([]);
      if (url === '/api/v1/location/report') return response({accepted: true});
      return response({ok: true, device_id: body && body.device_id, source: body && body.source, enabled: body && body.enabled});
    },
    setInterval() { return 1; },
    setTimeout() { return 1; },
    clearTimeout() {},
    addEventListener() {},
    WebSocket: function () { this.close = () => {}; this.send = () => {}; }
  };
  root.WebSocket.prototype = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/remote-guard.js'), 'utf8'), {
    window: root, Promise, Set, Date, Math, Number, String, Boolean, Array, Object, JSON, Error,
    Blob, Float32Array, DataView, ArrayBuffer
  });
  root.QimingRemoteGuard.mount();
  return {root, nodes, requests, watchCallbacks, clearedWatches, appendedResources, get positionCalls() { return positionCalls; }};
}

async function settle() {
  for (let index = 0; index < 30; index += 1) await Promise.resolve();
}

test('computer location stays opt-in, reports a real fix, and stops cleanly', async () => {
  const h = mountGuard();
  await settle();
  const button = h.nodes.get('guardUseLocation');
  assert.equal(button.hidden, false);
  assert.equal(button.textContent, '启用本机定位');
  assert.equal(h.positionCalls, 0);

  await button.onclick();
  const providerOn = h.requests.find(item => item.url === '/api/v1/location/provider' && JSON.parse(item.init.body).enabled);
  const report = h.requests.find(item => item.url === '/api/v1/location/report');
  assert.ok(providerOn);
  assert.equal(JSON.parse(providerOn.init.body).source, 'browser_geolocation');
  const reportBody = JSON.parse(report.init.body);
  assert.equal(reportBody.device_id, 'hat-local');
  assert.equal(reportBody.latitude, 31.2304);
  assert.equal(reportBody.longitude, 121.4737);
  assert.equal(reportBody.accuracy, 11);
  assert.equal(reportBody.source, 'browser_geolocation');
  assert.equal(reportBody.mode, 'real');
  assert.equal(button.textContent, '停止本机定位');
  assert.equal(typeof h.watchCallbacks.success, 'function');
  assert.match(h.nodes.get('guardLocationSourceStatus').textContent, /同步到当前设备/);

  await button.onclick();
  const providerOff = h.requests.find(item => item.url === '/api/v1/location/provider' && !JSON.parse(item.init.body).enabled);
  assert.ok(providerOff);
  assert.deepEqual(h.clearedWatches, [42]);
  assert.equal(button.textContent, '启用本机定位');
});

test('Baidu GL loads the full SDK instead of the document.write bootstrap', async () => {
  const h = mountGuard({mapAk: 'browser-ak-test'});
  await settle();
  const scripts = h.appendedResources.filter(item => item.tagName === 'SCRIPT');
  const styles = h.appendedResources.filter(item => item.tagName === 'LINK');
  assert.equal(scripts.length, 1);
  assert.match(scripts[0].src, /api\.map\.baidu\.com\/getscript\?type=webgl/);
  assert.match(scripts[0].src, /ak=browser-ak-test/);
  assert.equal(scripts[0].src.includes('/api?v=1.0'), false);
  assert.equal(styles[0].href, 'https://api.map.baidu.com/res/webgl/10/bmap.css');
});

test('opening the owner remote-guard page automatically requests and syncs computer location', async () => {
  const h = mountGuard({activeGuard: true});
  await settle();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.positionCalls, 1);
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/report'), true);
  assert.equal(h.nodes.get('guardUseLocation').textContent, '停止本机定位');
});

test('opening the owner AI map assistant also requests computer location', async () => {
  const h = mountGuard({activeAssistant: true});
  await settle();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.positionCalls, 1);
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/report'), true);
});

test('nearby assistant query waits for the first computer location fix', async () => {
  let resolvePosition;
  const h = mountGuard({
    activeAssistant: true,
    getPosition: success => { resolvePosition = success; }
  });
  await settle();
  await new Promise(resolve => setTimeout(resolve, 0));
  h.nodes.get('guardAssistantInput').value = '附近有没有医院？';
  h.nodes.get('guardAssistantForm').onsubmit({preventDefault() {}});
  await settle();
  assert.equal(h.requests.some(item => item.url === '/api/v1/map/assistant'), false);
  resolvePosition({coords: {latitude: 31.2304, longitude: 121.4737, accuracy: 11, speed: null, heading: null}, timestamp: Date.now()});
  await new Promise(resolve => setTimeout(resolve, 0));
  await settle();
  const reportIndex = h.requests.findIndex(item => item.url === '/api/v1/location/report');
  const assistantIndex = h.requests.findIndex(item => item.url === '/api/v1/map/assistant');
  assert.ok(reportIndex >= 0);
  assert.ok(assistantIndex > reportIndex);
});

test('location permission denial does not enable a backend location source', async () => {
  const h = mountGuard({getPosition: (_success, failure) => failure({code: 1})});
  await settle();
  await h.nodes.get('guardUseLocation').onclick();
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/provider' && JSON.parse(item.init.body).enabled), false);
  assert.match(h.nodes.get('guardLocationSourceStatus').textContent, /未获定位权限/);
  assert.equal(h.nodes.get('guardUseLocation').textContent, '重试本机定位');
});

test('permission denial remains visible when another source is active', async () => {
  const h = mountGuard({locationSource: 'mock', getPosition: (_success, failure) => failure({code: 1})});
  await settle();
  await h.nodes.get('guardUseLocation').onclick();
  assert.match(h.nodes.get('guardLocationSourceStatus').textContent, /未获定位权限/);
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/provider' && JSON.parse(item.init.body).enabled), false);
});

test('computer location does not start from a remote host', async () => {
  const h = mountGuard();
  h.root.location.hostname = '192.168.1.22';
  await settle();
  await h.nodes.get('guardUseLocation').onclick();
  assert.equal(h.positionCalls, 0);
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/provider'), false);
  assert.match(h.nodes.get('guardLocationSourceStatus').textContent, /运行服务的电脑上打开 localhost/);
});

test('family viewers cannot start computer geolocation for the device', async () => {
  const h = mountGuard({owner: false});
  await settle();
  assert.equal(h.nodes.get('guardUseLocation').hidden, true);
  assert.equal(h.requests.some(item => item.url === '/api/v1/location/report'), false);
});
