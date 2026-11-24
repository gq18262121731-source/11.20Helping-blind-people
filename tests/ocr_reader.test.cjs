const test = require('node:test');
const assert = require('node:assert/strict');
const {Preview, Reader, drawBoxes, splitText} = require('../frontend/ocr-reader.js');

function setup() {
  const spoken = [];
  const statuses = [];
  const objectSpeech = {suspended: false, suspend() {this.suspended = true;}, resume() {this.suspended = false;}};
  const synth = {cancel() {}, resume() {}, getVoices() {return [];}, speak(u) {spoken.push(u); u.onstart();}};
  const reader = new Reader({objectSpeech, synth, Utterance: class {constructor(text) {this.text = text;}},
    onStatus: status => statuses.push(status), onText() {}, onBusy() {}});
  return {reader, spoken, statuses, objectSpeech};
}

test('long text is split without losing characters', () => {
  const text = '启明帽'.repeat(150);
  const chunks = splitText(text);
  assert.equal(chunks.join(''), text);
  assert.ok(chunks.every(chunk => chunk.length <= 100));
});

test('OCR boxes scale to the preview canvas and invalid boxes are skipped', () => {
  const calls = [];
  const context = {
    save() {}, restore() {}, beginPath() {}, closePath() {}, fill() {}, stroke() {},
    moveTo(x, y) {calls.push(['move', x, y]);}, lineTo(x, y) {calls.push(['line', x, y]);},
  };
  const count = drawBoxes(context, {image_width: 100, image_height: 50, boxes: [
    {points: [[10, 10], [40, 10], [40, 20], [10, 20]]},
    {points: [[1, 2]]},
  ]}, 200, 100);
  assert.equal(count, 1);
  assert.deepEqual(calls.slice(0, 2), [['move', 20, 20], ['line', 80, 20]]);
});

test('captured preview freezes the OCR source frame and labels returned word boxes', async () => {
  const calls = [];
  const frame = {width: 400, height: 200, close() {this.closed = true;}};
  const canvas = {width: 0, height: 0, getContext() {return {
    save() {}, restore() {}, beginPath() {}, closePath() {}, fill() {}, stroke() {},
    moveTo(...point) {calls.push(['move', ...point]);}, lineTo(...point) {calls.push(['line', ...point]);},
    drawImage(image, ...size) {calls.push(['image', image, ...size]);},
  };}};
  const legend = {hidden: true};
  const count = {textContent: ''};
  const resumeButton = {hidden: true, onclick: null};
  const preview = new Preview({canvas, legend, count, resumeButton, decodeImage: async () => frame});
  await preview.capture({size: 100});
  preview.setResult({has_text: true, image_width: 400, image_height: 200, boxes: [
    {text: '你好', points: [[20, 20], [100, 20], [100, 50], [20, 50]]},
  ]});
  assert.equal(preview.draw(() => {throw new Error('live frame must remain frozen');}), true);
  assert.deepEqual(calls[0], ['image', frame, 0, 0, 400, 200]);
  assert.equal(legend.hidden, false);
  assert.equal(count.textContent, '本次识别 · 1 处文字');
  assert.equal(resumeButton.hidden, false);
  preview.reset();
  assert.equal(frame.closed, true);
  assert.equal(legend.hidden, true);
  assert.equal(resumeButton.hidden, true);
});

test('recognition speaks sequentially and suspends object announcements', async () => {
  const h = setup();
  await h.reader.recognize(async () => 'image', async () => ({text: '第一句。第二句。', has_text: true}));
  assert.equal(h.objectSpeech.suspended, true);
  assert.equal(h.spoken[0].text, '第一句。');
  h.spoken[0].onend();
  assert.equal(h.spoken[1].text, '第二句。');
  h.spoken[1].onend();
  assert.equal(h.reader.busy, false);
  assert.equal(h.objectSpeech.suspended, false);
});

test('cancelling a pending request ignores late results and aborts fetch', async () => {
  const h = setup();
  let resolve;
  let signal;
  const pending = h.reader.recognize(async () => 'image', async (_, requestSignal) => {
    signal = requestSignal;
    return new Promise(done => {resolve = done;});
  });
  await new Promise(done => setImmediate(done));
  h.reader.cancel();
  assert.equal(signal.aborted, true);
  resolve({text: '过期文字', has_text: true});
  await pending;
  assert.equal(h.spoken.length, 0);
  assert.equal(h.reader.busy, false);
});

test('cancelled speech callbacks cannot start the next paragraph', () => {
  const h = setup();
  h.reader.text = '一。二。';
  h.reader.read();
  h.reader.cancel();
  h.spoken[0].onend();
  assert.equal(h.spoken.length, 1);
  assert.equal(h.objectSpeech.suspended, false);
});

test('no text, truncated output and request failures never auto-read', async () => {
  for (const result of [{text: '未识别到清晰文字', has_text: false}, {text: '部分文字', has_text: true, truncated: true}]) {
    const h = setup();
    await h.reader.recognize(async () => 'image', async () => result);
    assert.equal(h.spoken.length, 0);
    assert.equal(h.reader.busy, false);
  }
  const h = setup();
  await h.reader.recognize(async () => {throw new Error('请先启动摄像头');}, async () => {});
  assert.match(h.statuses.at(-1), /请先启动摄像头/);
  assert.equal(h.objectSpeech.suspended, false);
});

test('speech disabled during recognition still displays text without reading', async () => {
  const h = setup();
  h.reader.configure(false);
  await h.reader.recognize(async () => 'image', async () => ({text: '识别文字', has_text: true}));
  assert.equal(h.reader.text, '识别文字');
  assert.equal(h.spoken.length, 0);
  assert.equal(h.reader.busy, false);
});

test('hat camera OCR reply is routed through device TTS and accepts playback receipts', async () => {
  const h = setup();
  const calls = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok: true, async json() {return {status: 'synthesizing'};}};
  };
  try {
    await h.reader.recognize(async () => 'image', async () => ({
      task_id: 42, device_id: 'hat-01', source_id: 'hat-01', source_mode: 'hardware',
      tts_target: 'device', tts_status: 'synthesizing',
      final_reply: '出口', text: '出口', has_text: true,
    }));
    assert.equal(h.reader.ttsTarget, 'device');
    assert.equal(h.spoken.length, 0);
    assert.match(h.statuses.at(-1), /发送到帽子/);
    h.reader.read();
    await new Promise(done => setImmediate(done));
    assert.match(calls[0].url, /\/api\/voice-vision\/tasks\/42\/speak$/);
    assert.equal(h.spoken.length, 0);
    h.reader.handleTtsEvent({task_id: 42, status: 'completed'});
    assert.match(h.statuses.at(-1), /帽端播报完成/);
    h.reader.cancel();
    await new Promise(done => setImmediate(done));
    assert.match(calls[1].url, /\/api\/tts\/stop$/);
    assert.deepEqual(JSON.parse(calls[1].options.body), {device_id: 'hat-01'});
  } finally {
    global.fetch = originalFetch;
  }
});

test('dedicated reader page captures the selected camera, shows text, replays and stops reading', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const nodes = Object.fromEntries(['ocrCapture', 'ocrReplay', 'ocrStop', 'ocrStatus', 'ocrText', 'ocrResults', 'ocrEmptyState']
    .map(id => ['#' + id, {disabled: false, hidden: true, textContent: ''}]));
  const listeners = new Map();
  const spoken = [];
  const requests = [];
  const edgeTts = {
    supported: true,
    cancel() {},
    speak(text, handlers) {
      return new Promise(resolve => {
        spoken.push({text, onend() { handlers.onend?.(); resolve(); }});
        handlers.onstart?.();
      });
    },
  };
  const video = {readyState: 2, videoWidth: 1280, videoHeight: 720};
  const objectSpeech = {allowed: true, suspend() {this.suspended = true;}, resume() {this.suspended = false;}};
  const fetch = async (url, options) => {
    requests.push({url, options});
    return {ok: true, async json() {return {task_id: 7, ocr_result: {text: '识字成功。', has_text: true, truncated: false}};}};
  };
  const canvas = {
    getContext() {return {drawImage(source) {assert.equal(source, video);}};},
    toBlob(done) {done({size: 100});},
  };
  const context = {
    module: {exports: {}}, AbortController, URLSearchParams, fetch,
    sessionStorage: {getItem() {return 'test-session';}},
    localStorage: {getItem() {return null;}},
    window: {
      fetch,
      sessionStorage: {getItem() {return 'test-session';}},
      localStorage: {getItem() {return null;}},
      QimingEdgeTts: edgeTts,
      addEventListener(event, callback) {listeners.set(event, callback);},
    },
    document: {
      getElementById(id) {assert.equal(id, 'textReader'); return {querySelector(selector) {assert.ok(nodes[selector]); return nodes[selector];}};},
      createElement(tag) {assert.equal(tag, 'canvas'); return canvas;},
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/ocr-reader.js'), 'utf8'), context);
  const reader = context.module.exports.mount({panelId: 'textReader', video, objectSpeech, isCameraActive: () => true});
  assert.equal(nodes['#ocrReplay'].disabled, true);
  await nodes['#ocrCapture'].onclick();
  assert.match(requests[0].url, /^\/api\/voice-vision\/tasks\?/);
  assert.match(requests[0].url, /intent=OCR_READ/);
  assert.equal(requests[0].options.headers.Authorization, 'Bearer test-session');
  assert.equal(nodes['#ocrText'].textContent, '识字成功。');
  assert.equal(nodes['#ocrResults'].hidden, false);
  assert.equal(nodes['#ocrEmptyState'].hidden, true);
  assert.equal(nodes['#ocrCapture'].disabled, true);
  assert.equal(spoken[0].text, '识字成功。');
  spoken[0].onend();
  assert.equal(nodes['#ocrCapture'].disabled, false);
  assert.equal(nodes['#ocrReplay'].disabled, false);
  nodes['#ocrReplay'].onclick();
  assert.equal(spoken.length, 2);
  listeners.get('qiming-camera-stopped')();
  assert.equal(reader.busy, false);
  assert.equal(nodes['#ocrStop'].disabled, true);
  assert.equal(objectSpeech.suspended, false);
  listeners.get('qiming-camera-source-changed')();
  assert.equal(reader.text, '');
  assert.equal(nodes['#ocrText'].textContent, '');
  assert.equal(nodes['#ocrEmptyState'].hidden, false);
});
