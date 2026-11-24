const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('computer microphone controller sends PCM session metadata for the selected live source', async () => {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: id === 'voiceInputSource' ? 'computer' : '', textContent: '', disabled: false,
      parentElement: {setAttribute() {}, removeAttribute() {}}, setAttribute() {}, removeAttribute() {}});
    return nodes.get(id);
  };
  const messages = [];
  const events = [];
  class FakeWebSocket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; FakeWebSocket.instance = this; setImmediate(() => {this.readyState = 1; this.onopen?.();}); }
    send(value) { messages.push(value); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  const track = {stop() {this.stopped = true;}};
  class FakeContext {
    constructor() { this.sampleRate = 48000; this.destination = {}; }
    resume() {return Promise.resolve();}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
    audioWorklet = {addModule: async url => {this.workletModule = url;}};
    close() {this.closed = true;}
  }
  class FakeAudioWorkletNode {
    constructor(context, name, options) {
      this.context = context; this.name = name; this.options = options;
      this.port = {onmessage: null, postMessage: message => {
        if (message.type === 'flush') {
          this.port.onmessage?.({data: {type: 'audio', samples: new Float32Array([1, 1, 1, -1, -1, -1])}});
          this.port.onmessage?.({data: {type: 'flushed'}});
        }
      }};
    }
    connect() {}
    disconnect() {this.disconnected = true;}
  }
  const root = {
    document: {getElementById: node}, navigator: {mediaDevices: {getUserMedia: async () => ({getTracks: () => [track]})}},
    AudioContext: FakeContext, webkitAudioContext: FakeContext, AudioWorkletNode: FakeAudioWorkletNode, WebSocket: FakeWebSocket,
    location: {protocol: 'http:', host: 'localhost:8765'}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    addEventListener() {}, CustomEvent: class {constructor(type, init) {this.type = type; this.detail = init.detail;}},
    setTimeout, clearTimeout,
  };
  const context = {window: root, module: {exports: {}}, AbortController};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/voice-input.js'), 'utf8'), context);
  const monitor = {sourceId: 'local_camera', seq: 42, active: () => true};
  const controller = new context.module.exports.VoiceInputController({document: root.document, cameraMonitor: monitor});
  await controller.start();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(JSON.parse(messages[0]).type, 'audio_start');
  assert.equal(JSON.parse(messages[0]).source_id, 'local_camera');
  assert.equal(JSON.parse(messages[0]).source_mode, 'software');
  assert.equal(JSON.parse(messages[0]).frame_seq, 42);
  assert.equal(JSON.parse(messages[0]).tts_target, 'browser');
  assert.equal(controller.processor.name, 'qiming-pcm-capture');
  await controller.stop();
  assert.deepEqual(Array.from(new Int16Array(messages[1])), [32767, -32768]);
  assert.equal(JSON.parse(messages[2]).type, 'audio_end');
  assert.equal(track.stopped, true);
  assert.equal(controller.active, true);
});

test('AudioWorklet batches captured samples and flushes the final partial chunk', () => {
  const {readFileSync} = require('node:fs');
  const workletSource = readFileSync(path.join(__dirname, '../frontend/voice-input-worklet.js'), 'utf8');
  let Processor;
  class FakeAudioWorkletProcessor {constructor() {this.port = {onmessage: null, postMessage: (...args) => posted.push(args)};}}
  const posted = [];
  vm.runInNewContext(workletSource, {
    AudioWorkletProcessor: FakeAudioWorkletProcessor,
    registerProcessor(name, implementation) {assert.equal(name, 'qiming-pcm-capture'); Processor = implementation;},
  });
  const processor = new Processor();
  processor.process([[new Float32Array(3000).fill(0.25)]], [[new Float32Array(128)]]);
  assert.equal(posted.length, 0);
  processor.process([[new Float32Array(1500).fill(-0.5)]], [[new Float32Array(128)]]);
  assert.equal(posted.length, 1);
  assert.equal(posted[0][0].samples.length, 4096);
  processor.port.onmessage({data: {type: 'flush'}});
  assert.equal(posted.length, 3);
  assert.equal(posted[1][0].type, 'audio');
  assert.equal(posted[1][0].samples.length, 404);
  assert.equal(posted[2][0].type, 'flushed');
});

test('PCM helpers downsample and clamp microphone samples', () => {
  const {downsample, pcm16} = require('../frontend/voice-input.js');
  const values = downsample(new Float32Array([1, 1, -1, -1]), 32000);
  assert.deepEqual(Array.from(values), [1, -1]);
  assert.deepEqual(Array.from(new Int16Array(pcm16(new Float32Array([-2, 0, 2])))), [-32768, 0, 32767]);
});
