const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup({history = [], createTask} = {}) {
  const nodes = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: id === 'voiceVisionFilter' ? 'all' : '', textContent: '', children: [],
      disabled: false, dataset: {}, classList: {add() {}},
      append(...items) {this.children.push(...items);},
      replaceChildren(...items) {this.children = items;},
      addEventListener(event, callback) {this[event] = callback;}, focus() {},
    });
    return nodes.get(id);
  }
  const listeners = new Map();
  const requests = [];
  const spoken = [];
  const monitor = {sourceId: 'local_camera', active: () => false};
  const root = {
    document: {getElementById: node, createElement: () => ({children: [], dataset: {}, append(...items) {this.children.push(...items);}})},
    sessionStorage: {getItem: () => 'test-token'}, localStorage: {getItem: () => null},
    setTimeout, clearTimeout, addEventListener(event, callback) {listeners.set(event, callback);},
    speechSynthesis: {cancel() {}, speak(utterance) {spoken.push(utterance);}},
    SpeechSynthesisUtterance: class {constructor(text) {this.text = text;}},
    async fetch(url, options = {}) {
      requests.push({url, options});
      const success = data => ({ok: true, json: async () => data});
      if (url === '/api/voice-vision/capabilities') return success({capabilities: []});
      if (url.startsWith('/api/voice-vision/tasks') && options.method === 'POST' && !url.endsWith('/speak')) {
        return success(await createTask?.(url, options));
      }
      if (url.startsWith('/api/voice-vision/tasks') && !options.method) return success(history);
      return success({status: 'queued'});
    },
  };
  const context = {window: root, module: {exports: {}}, AbortController, URLSearchParams};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/voice-vision.js'), 'utf8'), context);
  const app = context.module.exports.mount({cameraMonitor: monitor});
  return {app, node, listeners, requests, monitor, spoken};
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('history replay uses stored task id and stored source rather than current camera', async () => {
  const h = setup({history: [{id: 42, source_mode: 'hardware', source_id: 'hat-001', intent: 'ENV_QUERY', final_reply: '前方有行人。', created_at: '2026-11-23T00:00:00Z'}]});
  await settle();
  h.node('voiceVisionHistory').children[0].onclick();
  h.app.speakReply();
  await settle();
  assert.ok(h.requests.some(item => item.url === '/api/voice-vision/tasks/42/speak'));
  assert.equal(h.spoken.length, 0);
});

test('source change aborts a pending task and ignores its late reply', async () => {
  let complete;
  const h = setup({createTask: () => new Promise(resolve => {complete = resolve;})});
  h.node('voiceVisionQuestion').value = '前面有什么';
  const pending = h.app.submitTask();
  await settle();
  h.monitor.sourceId = 'hat-002';
  h.listeners.get('qiming-camera-source-changed')();
  const request = h.requests.find(item => item.options.method === 'POST');
  assert.equal(request.options.signal.aborted, true);
  complete({task_id: 10, final_reply: 'old frame reply', intent: 'ENV_QUERY'});
  await pending;
  assert.notEqual(h.node('voiceVisionReply').textContent, 'old frame reply');
  assert.equal(h.node('voiceVisionSubmit').disabled, false);
});

test('new tasks use the newly selected source even when viewing history', async () => {
  const h = setup({history: [{id: 5, source_mode: 'hardware', source_id: 'hat-old', intent: 'ENV_QUERY', final_reply: '历史回复', created_at: '2026-11-23T00:00:00Z'}],
    createTask: async () => ({task_id: 6, intent: 'UNKNOWN', source_mode: 'software', final_reply: '请重新表达', reply_status: 'failed'})});
  await settle();
  h.node('voiceVisionHistory').children[0].onclick();
  h.node('voiceVisionQuestion').value = '随便说点什么';
  await h.app.submitTask();
  const request = h.requests.find(item => item.options.method === 'POST');
  assert.equal(JSON.parse(request.options.body).source_id, 'local_camera');
});
