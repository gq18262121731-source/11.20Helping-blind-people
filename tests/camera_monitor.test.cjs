const test = require('node:test');
const assert = require('node:assert/strict');
global.QimingCameraSpeech ||= require('../frontend/camera-speech.js');
const {Monitor} = require('../frontend/camera-monitor.js');

function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

function response({status = 200, sources, location, seq = 1, blob = {size: 100}} = {}) {
  return {ok: status === 200, status, headers: {get() {return String(seq);}},
    async json() {return location || sources || [];}, async blob() {return blob;}};
}

function harness({saved = null, fetch, getUserMedia, devices = []} = {}) {
  const nodes = new Map(), timers = new Map(), timeouts = new Map(), requests = [], mediaRequests = [], revoked = [], events = new Map();
  const stored = new Map(saved === null ? [] : [['qh_camera_source', saved]]);
  const tracks = [{readyState: 'live', stopped: false, stop() {this.stopped = true;}}];
  const stream = {getTracks: () => tracks, getVideoTracks: () => tracks};
  let timerId = 0, urlId = 0;
  class Node {
    constructor(tag = 'div') {
      this.tag = tag; this.children = []; this._value = ''; this.hidden = false;
      this.style = {}; this.textContent = ''; this.width = 640; this.height = 360;
      this.readyState = 2; this.videoWidth = 1280; this.videoHeight = 720;
      this.naturalWidth = 320; this.naturalHeight = 240;
      const classes = new Set();
      this.classList = {toggle(name, active) {active ? classes.add(name) : classes.delete(name);}, contains: name => classes.has(name)};
    }
    set value(value) {this._value = this.tag === 'select' && !this.children.some(item => item.value === value) ? '' : value;}
    get value() {return this._value;}
    replaceChildren(...children) {
      this.children = children;
      if (this.tag === 'select' && !children.some(item => item.value === this._value)) this._value = children[0]?.value || '';
    }
    removeAttribute(name) {delete this[name];}
    setAttribute(name, value) {this[name] = value;}
    getBoundingClientRect() {return {left: 0, top: 0, width: 640, height: 360};}
    getContext() {return {setTransform() {}, clearRect() {}, strokeRect() {}, fillRect() {}, fillText() {}, drawImage() {},
      measureText: () => ({width: 100}), getImageData: () => ({data: [255, 255, 255, 255]})};}
    toBlob(done) {done({size: 100});}
    play() {return Promise.resolve();}
  }
  const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new Node(id === 'cameraSelect' || id === 'ocrCameraSelect' ? 'select' : 'div'));
    return nodes.get(id);
  }, createElement: tag => new Node(tag),
  addEventListener(name, listener) {events.set(name, listener);},
  removeEventListener(name) {events.delete(name);}};
  const speech = {announced: [], sourceId: null, start() {}, stop() {}, reset() {},
    setSource(id) {this.sourceId = id;}, announce(result) {this.announced.push(result);}};
  const monitor = new Monitor({document, objectSpeech: speech,
    storage: {getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)},
    getToken: () => 'test-token',
    mediaDevices: {enumerateDevices: async () => devices,
      getUserMedia: async constraints => {mediaRequests.push(constraints); return getUserMedia ? getUserMedia(constraints) : stream;}},
    fetch: async (url, options) => {requests.push({url, options}); return fetch ? fetch(url, options) : response();},
    url: {createObjectURL: () => 'blob:frame-' + (++urlId), revokeObjectURL: value => revoked.push(value)},
    setInterval(fn) {timers.set(++timerId, fn); return timerId;}, clearInterval(id) {timers.delete(id);},
    setTimeout(fn) {timeouts.set(++timerId, fn); return timerId;}, clearTimeout(id) {timeouts.delete(id);},
  });
  return {monitor, nodes, requests, mediaRequests, tracks, stream, speech, stored, timers, timeouts, revoked, document, events};
}

test('fullscreen targets the complete camera stage and restores the button after Esc', async () => {
  const h = harness();
  const stage = h.nodes.get('cameraStage');
  const button = h.nodes.get('cameraFullscreen');
  let requests = 0;
  stage.requestFullscreen = async () => {
    requests++;
    h.document.fullscreenElement = stage;
    h.events.get('fullscreenchange')();
  };
  h.document.exitFullscreen = async () => {
    h.document.fullscreenElement = null;
    h.events.get('fullscreenchange')();
  };
  h.monitor.updateFullscreenButton();
  assert.equal(button.disabled, true);
  h.monitor.running = h.monitor.hasFrame = true;
  h.monitor.stage(true, false);
  assert.equal(button.disabled, false);

  await button.onclick();
  assert.equal(requests, 1);
  assert.equal(button.textContent, '退出全屏');
  assert.equal(button['aria-pressed'], 'true');
  await button.onclick();
  assert.equal(button.textContent, '全屏查看');
  assert.equal(button['aria-pressed'], 'false');

  await button.onclick();
  h.document.fullscreenElement = null;
  h.events.get('fullscreenchange')();
  assert.equal(button.textContent, '全屏查看');
  assert.equal(button.disabled, false);
});

test('fullscreen failures keep the control available and provide an actionable message', async () => {
  const h = harness();
  h.nodes.get('cameraStage').requestFullscreen = async () => {throw new Error('denied');};
  h.monitor.running = h.monitor.hasFrame = true;
  h.monitor.stage(true, false);

  await h.nodes.get('cameraFullscreen').onclick();
  assert.equal(h.nodes.get('cameraFullscreen').disabled, false);
  assert.match(h.nodes.get('cameraStatus').textContent, /全屏切换失败.*重试/);
});

test('stopping the camera still allows leaving fullscreen and removes waiting state', async () => {
  const h = harness();
  const stage = h.nodes.get('cameraStage');
  stage.requestFullscreen = async () => {};
  h.document.exitFullscreen = async () => {h.document.fullscreenElement = null;};
  h.document.fullscreenElement = stage;
  h.monitor.running = h.monitor.hasFrame = true;
  stage.classList.toggle('is-waiting', true);

  h.monitor.stop();
  assert.equal(h.nodes.get('cameraFullscreen').disabled, false);
  assert.equal(stage.classList.contains('is-waiting'), false);
  await h.nodes.get('cameraFullscreen').onclick();
  assert.equal(h.nodes.get('cameraFullscreen').disabled, true);
});

test('depth comparison waits for matching frame previews and highlights the selected object', () => {
  const h = harness();
  const monitor = h.monitor;
  monitor.running = true;
  const result = {device_id: 'local_camera', image_seq: 8, depth_frame_id: 8, depth_status: 'ready',
    raw_frame_preview: 'same-frame-jpeg', depth_preview: 'same-frame-depth', frame_width: 640, frame_height: 360,
    objects: [
      {class: 'chair', confidence: 0.94, bbox: [20, 20, 220, 180], direction: 'left', track_id: 4,
        depth: {level: 'near', depth_percentile: 0.82}, risk: {level: 'L2', status: 'valid'}},
      {class: 'chair', confidence: 0.91, bbox: [350, 40, 520, 300], direction: 'right', track_id: 5,
        depth: {level: 'far', depth_percentile: 0.21}, risk: {level: 'L0', status: 'valid'}}
    ]};
  monitor.handleSpatial(result);
  monitor.setViewMode('compare');
  assert.equal(h.nodes.get('compareOriginal').hidden, true);
  assert.equal(h.nodes.get('compareDepth').hidden, true);

  h.nodes.get('compareOriginal').onload();
  assert.equal(h.nodes.get('compareOriginal').hidden, true, 'one loaded preview must not expose an unsynchronized pair');
  h.nodes.get('compareDepth').onload();
  assert.equal(h.nodes.get('compareOriginal').hidden, false);
  assert.equal(h.nodes.get('compareDepth').hidden, false);
  assert.match(h.nodes.get('compareFrameMeta').textContent, /检测帧 8 · 深度帧 8/);
  assert.match(h.nodes.get('compareOriginal').src, /same-frame-jpeg/);
  assert.match(h.nodes.get('compareDepth').src, /same-frame-depth/);

  h.nodes.get('compareDetectionOverlay').onclick({clientX: 100, clientY: 100});
  assert.equal(h.nodes.get('compareTargetName').textContent, '椅子 · 跟踪 #4');
  assert.equal(h.nodes.get('compareTargetDirection').textContent, '方位：左侧');
  assert.equal(h.nodes.get('compareTargetDepth').textContent, '相对深度：较近 · 相对深度 82%');
  assert.equal(h.nodes.get('compareTargetRisk').textContent, '风险等级：L2');
  assert.equal(h.nodes.get('compareDepthOverlay').width, 640, 'the selected region is drawn on the paired depth map');
  assert.deepEqual(h.nodes.get('compareTargets').children.map(button => button['aria-pressed']), ['true', 'false']);
  h.nodes.get('compareTargets').children[1].onclick();
  assert.equal(h.nodes.get('compareTargetDirection').textContent, '方位：右侧');
  assert.equal(h.nodes.get('compareTargetDepth').textContent, '相对深度：较远 · 相对深度 21%');
  assert.deepEqual(h.nodes.get('compareTargets').children.map(button => button['aria-pressed']), ['false', 'true']);
});

test('depth comparison rejects a frame when its image and depth sequence differ', () => {
  const h = harness();
  h.monitor.running = true;
  h.monitor.handleSpatial({device_id: 'local_camera', image_seq: 9, depth_frame_id: 8, depth_status: 'ready',
    raw_frame_preview: 'unmatched-frame', depth_preview: 'unmatched-depth', objects: []});
  h.monitor.setViewMode('compare');
  assert.equal(h.nodes.get('cameraCompare').hidden, false);
  assert.equal(h.nodes.get('compareOriginal').hidden, true);
  assert.equal(h.nodes.get('compareDepth').hidden, true);
  assert.equal(h.nodes.get('compareFrameMeta').textContent, '等待相同帧的检测与深度结果');
});

test('first source list prefers a live hat but an explicit local choice stays local', async () => {
  const sources = [{source_id: 'esp32s3_cam_001', label: '帽端摄像头', kind: 'device', online: true}];
  const first = harness({fetch: async () => response({sources})});
  await first.monitor.refreshSources();
  assert.equal(first.monitor.sourceId, 'esp32s3_cam_001');
  assert.equal(first.nodes.get('cameraSelect').value, 'remote:esp32s3_cam_001');
  assert.equal(first.nodes.get('cameraSelect').hidden, false);
  const local = harness({saved: 'local_camera', fetch: async () => response({sources})});
  await local.monitor.refreshSources();
  assert.equal(local.monitor.sourceId, 'local_camera');
  assert.equal(local.mediaRequests.length, 0);
});

test('starting a hat preview never opens or uploads the computer camera', async () => {
  const h = harness({saved: 'esp32s3_cam_001'});
  await h.monitor.start();
  h.nodes.get('cameraRemote').onload();
  await Promise.resolve();
  await h.monitor.sendLocalFrame();
  assert.equal(h.monitor.active(), true);
  assert.equal(h.speech.sourceId, 'esp32s3_cam_001');
  assert.equal(h.mediaRequests.length, 0);
  const frameRequest = h.requests.find(request => request.url === '/api/tablet/cameras/esp32s3_cam_001/frame');
  assert.ok(frameRequest);
  assert.equal(frameRequest.options.headers.Authorization, 'Bearer test-token');
  const armRequest = h.requests.find(request => request.url === '/api/tablet/motor-monitor');
  assert.equal(JSON.parse(armRequest.options.body).active, true);
  assert.equal(h.monitor.motorMonitorActive, true);
  assert.equal(h.stored.get('qh_camera_source'), 'esp32s3_cam_001');
  h.monitor.stop();
  assert.equal(JSON.parse(h.requests.at(-1).options.body).active, false);
});

test('hat monitor displays authenticated Wi-Fi location and labels the estimate', async () => {
  const h = harness({saved: 'esp32s3_cam_001', fetch: async url => url.includes('/location?')
    ? response({location: {available: true, latitude: 31.2, longitude: 121.5,
      accuracy_m: 85, access_points_used: 3, provider: 'BeaconDB', updated_at: '2026-01-01T00:00:00Z'}})
    : response()});
  await h.monitor.start();
  await Promise.resolve();
  assert.match(h.nodes.get('wifiLocationStatus').textContent, /31\.20000, 121\.50000.*±85 米/);
  assert.match(h.nodes.get('wifiLocationDetail').textContent, /3 个 Wi-Fi 热点估算.*BeaconDB/);
  const request = h.requests.find(item => item.url.includes('/location?'));
  assert.equal(request.options.headers.Authorization, 'Bearer test-token');
  h.monitor.stop();
});

test('source refresh, missing source and stale image retain the selected hat without fallback', async () => {
  let stale = false;
  const h = harness({saved: 'esp32s3_cam_001', fetch: async url =>
    url.endsWith('/frame') ? response({status: stale ? 410 : 200}) : response({sources: []})});
  await h.monitor.start();
  h.nodes.get('cameraRemote').onload();
  h.monitor.handleDetection({device_id: 'esp32s3_cam_001', image_seq: 1, model_status: 'ready', objects: [{class: 'person', confidence: .9, direction: 'front'}]});
  assert.equal(h.nodes.get('objects').children.length, 1);
  stale = true;
  await h.monitor.pollRemote();
  await h.monitor.refreshSources();
  assert.equal(h.monitor.sourceId, 'esp32s3_cam_001');
  assert.equal(h.nodes.get('cameraSelect').value, 'remote:esp32s3_cam_001');
  assert.equal(h.monitor.hasFrame, false);
  assert.equal(h.nodes.get('objects').children.length, 0);
  assert.equal(h.mediaRequests.length, 0);
  assert.match(h.nodes.get('cameraStatus').textContent, /不会切换/);
  assert.equal(h.revoked.length, 1);
});

test('local device selection survives permission label refresh and starting the stream', async () => {
  const h = harness({saved: 'local_camera', devices: [
    {kind: 'videoinput', deviceId: 'integrated', label: 'Integrated Camera'},
    {kind: 'videoinput', deviceId: 'usb', label: 'USB Camera'},
  ]});
  await h.monitor.listLocalCameras();
  h.nodes.get('cameraSelect').value = 'local:usb';
  await h.nodes.get('cameraSelect').onchange();
  await h.monitor.start();
  await h.monitor.listLocalCameras();
  assert.equal(h.nodes.get('cameraSelect').value, 'local:usb');
  assert.equal(h.mediaRequests[0].video.deviceId.exact, 'usb');
  assert.equal(h.monitor.localStream, h.stream);
});

test('the same camera dropdown always offers the ESP32 even without a connection or a source list', async () => {
  const h = harness({saved: 'local_camera', devices: [
    {kind: 'videoinput', deviceId: 'integrated', label: 'Integrated Camera'},
  ], fetch: async () => response({status: 401})});
  await h.monitor.listLocalCameras();
  await h.monitor.refreshSources();
  const select = h.nodes.get('cameraSelect');
  assert.ok(select.children.some(option => option.value === 'local:integrated'));
  assert.ok(select.children.some(option => option.value === 'remote:esp32s3_cam_001' && /ESP32.*等待/.test(option.textContent)));
  select.value = 'remote:esp32s3_cam_001';
  await select.onchange();
  await h.monitor.start();
  assert.equal(h.monitor.sourceId, 'esp32s3_cam_001');
  assert.equal(select.hidden, false);
  assert.equal(select.value, 'remote:esp32s3_cam_001');
  assert.equal(h.mediaRequests.length, 0);
  assert.match(h.nodes.get('cameraStatus').textContent, /登录已失效/);
});

test('reader source controls stay synchronized with the live monitor source', async () => {
  const h = harness();
  const readerSelect = h.nodes.get('ocrCameraSelect');
  const monitorSelect = h.nodes.get('cameraSelect');
  assert.equal(readerSelect.value, monitorSelect.value);
  readerSelect.value = 'remote:esp32s3_cam_001';
  await readerSelect.onchange();
  assert.equal(readerSelect.value, 'remote:esp32s3_cam_001');
  assert.equal(monitorSelect.value, readerSelect.value);
  assert.match(h.nodes.get('ocrCameraStatus').textContent, /已选择：ESP32 帽端摄像头/);
});

test('one dropdown switches a running local camera to ESP32 and back to the selected computer device', async () => {
  const h = harness({saved: 'local_camera', devices: [
    {kind: 'videoinput', deviceId: 'integrated', label: 'Integrated Camera'},
  ]});
  await h.monitor.listLocalCameras();
  const select = h.nodes.get('cameraSelect');
  select.value = 'local:integrated';
  await select.onchange();
  await h.monitor.start();
  select.value = 'remote:esp32s3_cam_001';
  await select.onchange();
  h.nodes.get('cameraRemote').onload();
  assert.equal(h.tracks[0].stopped, true);
  assert.equal(h.monitor.localStream, null);
  assert.equal(h.monitor.active(), true);
  await h.monitor.listLocalCameras();
  await h.monitor.refreshSources();
  assert.equal(select.value, 'remote:esp32s3_cam_001');
  assert.equal(h.mediaRequests.length, 1);
  select.value = 'local:integrated';
  await select.onchange();
  assert.equal(h.monitor.sourceId, 'local_camera');
  assert.equal(h.mediaRequests.length, 2);
  assert.equal(h.mediaRequests[1].video.deviceId.exact, 'integrated');
  assert.equal(select.value, 'local:integrated');
});

test('switching to the hat releases a local permission request that resolves late', async () => {
  const pending = deferred();
  const h = harness({saved: 'local_camera', getUserMedia: () => pending.promise});
  const starting = h.monitor.start();
  await h.monitor.selectSource('esp32s3_cam_001');
  pending.resolve(h.stream);
  await starting;
  assert.equal(h.monitor.sourceId, 'esp32s3_cam_001');
  assert.equal(h.monitor.localStream, null);
  assert.equal(h.tracks[0].stopped, true);
  assert.equal(h.nodes.get('cameraVideo').srcObject, null);
});

test('late image responses cannot overwrite the newly selected source', async () => {
  const pending = deferred();
  const h = harness({saved: 'first_hat', fetch: async url => url.includes('first_hat') ? pending.promise : response({seq: 22})});
  const first = h.monitor.start();
  await h.monitor.selectSource('second_hat');
  h.nodes.get('cameraRemote').onload();
  const visible = h.nodes.get('cameraRemote').src;
  pending.resolve(response({seq: 1}));
  await first;
  assert.equal(h.monitor.sourceId, 'second_hat');
  assert.equal(h.nodes.get('cameraRemote').src, visible);
  assert.equal(h.monitor.frameSeq, '22');
  assert.equal(h.mediaRequests.length, 0);
});

test('only one remote request is in flight and stop ignores a late response', async () => {
  const pending = deferred();
  const h = harness({saved: 'esp32s3_cam_001', fetch: () => pending.promise});
  const starting = h.monitor.start();
  await h.monitor.pollRemote();
  assert.equal(h.requests.filter(request => request.url.endsWith('/frame')).length, 1);
  h.monitor.stop();
  pending.resolve(response());
  await starting;
  assert.equal(h.monitor.running, false);
  assert.equal(h.monitor.hasFrame, false);
  assert.equal(h.nodes.get('cameraRemote').src, undefined);
  assert.equal(h.timers.size, 0);
});

test('detections and OCR use the selected remote source and clear on source change', async () => {
  const blob = {size: 123};
  const h = harness({saved: 'esp32s3_cam_001', fetch: async () => response({blob})});
  await h.monitor.start();
  h.nodes.get('cameraRemote').onload();
  const result = {image_seq: 1, model_status: 'ready', objects: []};
  h.monitor.handleDetection({...result, device_id: 'local_camera'});
  h.monitor.handleDetection({...result, device_id: 'other_hat'});
  assert.equal(h.speech.announced.length, 0);
  h.monitor.handleDetection({...result, device_id: 'esp32s3_cam_001'});
  assert.equal(h.speech.announced.length, 1);
  assert.equal(await h.monitor.captureFrame(), blob);
  h.monitor.stop();
  await assert.rejects(h.monitor.captureFrame(), /等待画面/);
});

test('reader preview mirrors the active selected camera without opening a second stream', () => {
  const h = harness();
  const canvas = h.document.createElement('canvas');
  assert.equal(h.monitor.drawPreview(canvas), false);
  h.monitor.running = h.monitor.hasFrame = true;
  assert.equal(h.monitor.drawPreview(canvas), true);
  assert.equal(canvas.width, 1280);
  assert.equal(canvas.height, 720);
  assert.equal(h.mediaRequests.length, 0);
});

test('a hung remote request times out and retains the source without opening local hardware', async () => {
  const h = harness({saved: 'esp32s3_cam_001', fetch: (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  })});
  const starting = h.monitor.start();
  [...h.timeouts.values()][0]();
  await starting;
  assert.equal(h.monitor.sourceId, 'esp32s3_cam_001');
  assert.equal(h.monitor.hasFrame, false);
  assert.equal(h.mediaRequests.length, 0);
  assert.match(h.nodes.get('cameraStatus').textContent, /超时.*保留当前来源/);
});

test('dashboard scripts compile and the source controller is wired to all required DOM nodes', () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  const source = fs.readFileSync(path.join(__dirname, '../frontend/camera-monitor.js'), 'utf8');
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  for (const match of source.matchAll(/this\.get\('([^']+)'\)/g)) assert.ok(html.includes('id="' + match[1] + '"'), match[1]);
  assert.match(html, /<script src="\/camera-monitor\.js"><\/script>/);
  assert.match(html, /cameraMonitor\.handleDetection\(x\)/);
  assert.match(html, /captureFrame:\(\)=>cameraMonitor\.captureFrame\(\)/);
  assert.doesNotMatch(html, /async function startCamera\(/);
});
