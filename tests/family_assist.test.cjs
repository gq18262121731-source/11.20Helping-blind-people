const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {create, deliveryLabel, uniqueDevices, isOwnerDevice} = require('../frontend/family-assist.js');

function harness(handler, options = {}) {
  const requests = [];
  const timers = new Map();
  const revoked = [];
  let timerId = 0;
  let urlId = 0;
  const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data, blob: async () => data});
  const controller = create({
    ...options,
    token: () => 'session-one',
    urls: {createObjectURL: () => 'blob:' + (++urlId), revokeObjectURL: value => revoked.push(value)},
    setTimeout: (callback, delay) => {timers.set(++timerId, {callback, delay}); return timerId;},
    clearTimeout: id => timers.delete(id),
    fetch: async (url, init) => {
      requests.push({url, init});
      const custom = handler && await handler(url, init, response);
      if (custom) return custom;
      if (url === '/api/family/bindings') return response([
        {id: 1, device_id: 'hat_a', family_username: 'family'},
        {id: 2, device_id: 'hat_b', family_username: 'family'}
      ]);
      if (url.startsWith('/api/family/overview')) return response({permissions: {camera: true, messages: true}, device: {online: true}});
      if (url.startsWith('/api/family/messages')) return response([]);
      return response('jpeg');
    }
  });
  return {
    controller, requests, timers, revoked,
    async tick(delay) {
      const found = [...timers.entries()].find(([, value]) => value.delay === delay);
      assert.ok(found, `timer ${delay} exists`);
      timers.delete(found[0]);
      await found[1].callback();
      await settle();
    }
  };
}

async function settle() {
  for (let index = 0; index < 30; index++) await Promise.resolve();
}

test('family device options are deduplicated and delivery does not imply actual playback', () => {
  assert.deepEqual(uniqueDevices([{device_id: 'hat_a'}, {device_id: 'hat_a'}, {device_id: 'hat_b'}]).map(item => item.device_id), ['hat_a', 'hat_b']);
  assert.match(deliveryLabel({status: 'queued'}), /尚未确认实际出声/);
  assert.match(deliveryLabel({status: 'offline'}), /本次未播报/);
  assert.match(deliveryLabel({status: 'speech_disabled'}), /开启/);
  assert.match(deliveryLabel({status: 'queue_full'}), /稍后重发/);
  assert.match(deliveryLabel({}), /未收到播报回执/);
});

test('an owner can add a hat without supplying a second family account', async () => {
  let added = false;
  const selfBinding = {id: 3, device_id: 'my_hat', owner_username: 'owner', family_username: 'owner', is_owner_device: true};
  const h = harness((url, init, response) => {
    if (url === '/api/family/devices' && init.method === 'POST') {
      added = true;
      return response({created: true, device_id: 'my_hat'});
    }
    if (url === '/api/family/bindings') return response(added ? [selfBinding] : []);
    return null;
  });
  h.controller.setActive(true);
  await settle();
  assert.equal(h.controller.state.selectedDevice, '');
  await h.controller.addDevice(' my_hat ');
  const sent = h.requests.find(item => item.url === '/api/family/devices');
  assert.deepEqual(JSON.parse(sent.init.body), {device_id: 'my_hat'});
  assert.equal(h.controller.state.selectedDevice, 'my_hat');
  assert.equal(h.controller.state.overview.permissions.messages, true);
  assert.equal(isOwnerDevice(selfBinding), true);
  assert.equal(isOwnerDevice({owner_username: 'Owner', family_username: 'owner'}), true);
  assert.equal(isOwnerDevice({owner_username: 'owner', family_username: 'family'}), false);
  h.controller.dispose();
});

test('family frame polling uses authorization and runs independently of metadata', async () => {
  const h = harness();
  assert.equal(h.requests.length, 0);
  h.controller.setActive(true);
  await settle();
  await h.tick(0);
  assert.equal(h.controller.state.frameUrl, 'blob:1');
  await h.tick(700);
  assert.equal(h.controller.state.frameUrl, 'blob:2');
  assert.deepEqual(h.revoked, ['blob:1']);
  assert.equal(h.requests.filter(item => item.url.startsWith('/api/family/overview')).length, 1);
  for (const request of h.requests) assert.equal(request.init.headers.Authorization, 'Bearer session-one');
  h.controller.setActive(false);
  assert.equal(h.controller.state.frameUrl, '');
  assert.deepEqual(h.revoked, ['blob:1', 'blob:2']);
  assert.equal(h.timers.size, 0);
});

test('an expired frame clears the picture and retains controlled retry', async () => {
  let expired = false;
  const h = harness((url, _, response) => url.endsWith('/frame') && expired ? response({detail: 'expired'}, 410) : null);
  h.controller.setActive(true);
  await settle();
  await h.tick(0);
  expired = true;
  await h.tick(700);
  assert.equal(h.controller.state.frameUrl, '');
  assert.match(h.controller.state.frameStatus, /暂无新画面/);
  assert.ok([...h.timers.values()].some(item => item.delay === 700));
  h.controller.dispose();
});

test('late frames from the previous device cannot replace the selected device', async () => {
  let finishFrame;
  const pending = new Promise(resolve => {finishFrame = resolve;});
  const h = harness((url) => url === '/api/family/cameras/hat_a/frame' ? pending : null);
  h.controller.setActive(true);
  await settle();
  const initialTimer = [...h.timers.entries()].find(([, value]) => value.delay === 0);
  h.timers.delete(initialTimer[0]);
  const firstFrame = initialTimer[1].callback();
  await settle();
  h.controller.selectDevice('hat_b');
  await settle();
  await h.tick(0);
  assert.equal(h.controller.state.frameUrl, 'blob:1');
  finishFrame({ok: true, blob: async () => 'old frame'});
  await firstFrame;
  assert.equal(h.controller.state.selectedDevice, 'hat_b');
  assert.equal(h.controller.state.frameUrl, 'blob:1');
  h.controller.dispose();
});

test('pause aborts the in-flight frame and does not show its late reply', async () => {
  let finishFrame;
  const pending = new Promise(resolve => {finishFrame = resolve;});
  const h = harness(url => url.endsWith('/frame') ? pending : null);
  h.controller.setActive(true);
  await settle();
  const initialTimer = [...h.timers.entries()].find(([, value]) => value.delay === 0);
  h.timers.delete(initialTimer[0]);
  const firstFrame = initialTimer[1].callback();
  await settle();
  const frameRequest = h.requests.find(item => item.url.endsWith('/frame'));
  h.controller.setPaused(true);
  assert.equal(frameRequest.init.signal.aborted, true);
  finishFrame({ok: true, blob: async () => 'late frame'});
  await firstFrame;
  assert.equal(h.controller.state.frameUrl, '');
  assert.equal(h.controller.state.frameStatus, '画面已暂停');
  assert.equal([...h.timers.values()].some(item => item.delay === 700), false);
  h.controller.dispose();
});

test('revoked camera authorization clears the image and stops frame requests', async () => {
  let revoked = false;
  const h = harness((url, _, response) => url.endsWith('/frame') && revoked ? response({detail: '撤销'}, 403) : null);
  h.controller.setActive(true);
  await settle();
  await h.tick(0);
  revoked = true;
  await h.tick(700);
  assert.equal(h.controller.state.frameUrl, '');
  assert.match(h.controller.state.frameStatus, /授权已撤销/);
  assert.equal([...h.timers.values()].some(item => item.delay === 700), false);
  h.controller.dispose();
});

test('voice recognition produces an editable draft and only explicit submit sends it', async () => {
  const engines = [];
  class Recognition {
    constructor() {engines.push(this);}
    start() {}
    abort() {this.aborted = true;}
  }
  const h = harness((url, init, response) => init.method === 'POST' ? response({id: 4, body: '请向右走', delivery: {status: 'queued'}}) : null, {SpeechRecognition: Recognition});
  h.controller.setActive(true);
  await settle();
  h.controller.startRecognition();
  const result = [{transcript: '请向右走'}];
  result.isFinal = true;
  engines[0].onresult({results: [result]});
  engines[0].onend();
  assert.equal(h.controller.state.transcript, '请向右走');
  assert.equal(h.requests.some(item => item.init.method === 'POST'), false);
  await h.controller.sendMessage();
  const sent = h.requests.find(item => item.init.method === 'POST');
  assert.deepEqual(JSON.parse(sent.init.body), {device_id: 'hat_a', body: '请向右走', message_type: 'voice_transcript'});
  assert.equal(h.controller.state.delivery.status, 'queued');
  assert.equal(h.controller.state.transcript, '');
  h.controller.dispose();
});

test('leaving the page aborts voice recognition and ignores a captured late callback', async () => {
  let engine;
  class Recognition {
    constructor() {engine = this;}
    start() {}
    abort() {this.aborted = true;}
  }
  const h = harness(null, {SpeechRecognition: Recognition});
  h.controller.setActive(true);
  await settle();
  h.controller.startRecognition();
  const stale = engine.onresult;
  h.controller.setActive(false);
  stale({results: [[{transcript: '迟到文字'}]]});
  assert.equal(engine.aborted, true);
  assert.equal(h.controller.state.transcript, '');
  assert.equal(h.requests.some(item => item.init.method === 'POST'), false);
});

test('automatic device change after revocation cancels old work and clears the voice draft', async () => {
  let revoked = false;
  let engine;
  let finishFrame;
  const pending = new Promise(resolve => {finishFrame = resolve;});
  class Recognition {
    constructor() {engine = this;}
    start() {}
    abort() {this.aborted = true;}
  }
  const h = harness((url, _, response) => {
    if (url === '/api/family/bindings' && revoked) return response([{id: 2, device_id: 'hat_b', family_username: 'family'}]);
    if (url === '/api/family/cameras/hat_a/frame') return pending;
    return null;
  }, {SpeechRecognition: Recognition});
  h.controller.setActive(true);
  await settle();
  h.controller.startRecognition();
  const staleResult = engine.onresult;
  const result = [{transcript: '给原设备的留言'}];
  result.isFinal = true;
  engine.onresult({results: [result]});
  assert.equal(h.controller.state.voiceDraft, true);
  const initialTimer = [...h.timers.entries()].find(([, value]) => value.delay === 0);
  h.timers.delete(initialTimer[0]);
  const firstFrame = initialTimer[1].callback();
  await settle();
  const oldFrame = h.requests.find(item => item.url === '/api/family/cameras/hat_a/frame');
  revoked = true;
  await h.controller.refresh();
  assert.equal(h.controller.state.selectedDevice, 'hat_b');
  assert.equal(oldFrame.init.signal.aborted, true);
  assert.equal(engine.aborted, true);
  assert.equal(h.controller.state.listening, false);
  assert.equal(h.controller.state.transcript, '');
  assert.equal(h.controller.state.voiceDraft, false);
  staleResult({results: [[{transcript: '迟到文字'}]]});
  assert.equal(h.controller.state.transcript, '');
  await h.controller.sendMessage();
  assert.equal(h.requests.some(item => item.init.method === 'POST'), false);
  finishFrame({ok: true, blob: async () => 'old frame'});
  await firstFrame;
  assert.equal(h.controller.state.frameUrl, '');
  await h.tick(0);
  assert.equal(h.controller.state.frameUrl, 'blob:1');
  assert.equal([...h.timers.values()].filter(item => item.delay === 700).length, 1);
  h.controller.dispose();
});

test('PC page registers a dedicated family entry and all requested assets', () => {
  const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  assert.match(html, /data-page="family"/);
  assert.match(html, /family: '家属协助'/);
  assert.match(html, /familyAssist\?\.setActive\(page === 'family'\)/);
  assert.match(html, /src="\/family-assist.js"/);
  assert.match(html, /href="\/family-assist.css"/);
  assert.match(html, /id="familyDraft"/);
  assert.match(html, /id="familyAddDevice"[^>]*type="button"/);
});
