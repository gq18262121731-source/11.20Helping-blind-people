const test = require('node:test');
const assert = require('node:assert/strict');
const {create, objectName} = require('../frontend/camera-speech.js');

function setup() {
  let time = 100000;
  let nextTimer = 0;
  const timers = new Map();
  const spoken = [];
  const statuses = [];
  const synth = {
    cancelCount: 0,
    cancel() { this.cancelCount += 1; },
    resume() {},
    getVoices() { return [{name: 'Chinese', lang: 'zh-CN', localService: true}]; },
    speak(utterance) { spoken.push(utterance); utterance.onstart(); }
  };
  class Utterance { constructor(text) { this.text = text; } }
  const announcer = create({synth, Utterance, now: () => time, onStatus: value => statuses.push(value),
    schedule(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, at: time + delay}); return id; },
    unschedule(id) { timers.delete(id); }
  });
  announcer.active = true;
  return {announcer, synth, spoken, statuses, timers,
    advance(ms) { time += ms; for (const [id, timer] of [...timers]) if (timer.at <= time) { timers.delete(id); timer.fn(); } },
    finish() { spoken.at(-1).onend(); }
  };
}

function detection(objects, extra = {}) {
  return {device_id: 'local_camera', model_status: 'ready', objects, ...extra};
}
function item(label, direction = 'front', confidence = 0.9) { return {class: label, direction, confidence}; }

test('changing the selected source cancels old speech and only announces that source', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.announcer.setSource('esp32s3_cam_001');
  h.announcer.announce(detection([item('cat')]));
  assert.equal(h.spoken.length, 1);
  h.announcer.announce(detection([item('person')], {device_id: 'esp32s3_cam_001'}));
  assert.equal(h.spoken.length, 2);
  assert.equal(h.spoken[1].text, '前方有行人。');
  assert.ok(h.synth.cancelCount > 0);
});

test('Chinese names and direction grouping deduplicate objects in a frame', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup'), item('cup'), item('cell phone', 'left')]));
  assert.equal(h.spoken.length, 1);
  assert.equal(h.spoken[0].text, '前方有水杯，左侧有手机。');
  assert.equal(h.spoken[0].lang, 'zh-CN');
  assert.equal(h.spoken[0].rate, 0.88);
  assert.equal(h.spoken[0].voice.lang, 'zh-CN');
  assert.equal(objectName('person'), '行人');
  assert.equal(objectName('公交车'), '公交车');
});

test('ignores other cameras, simulation, failed detection and low confidence', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')], {device_id: 'simulator_001'}));
  h.announcer.announce(detection([item('cup')], {model_status: 'simulation'}));
  h.announcer.announce(detection([item('cup')], {simulation: true}));
  h.announcer.announce(detection([item('cup')], {model_status: 'error'}));
  h.announcer.announce(detection([item('cup', 'front', 0.2)]));
  assert.equal(h.spoken.length, 0);
});

test('same object is announced only once and utterances never overlap', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.announcer.announce(detection([item('cup'), item('bottle')]));
  assert.equal(h.spoken.length, 1);
  h.finish();
  h.announcer.announce(detection([item('bottle')]));
  h.advance(1999);
  assert.equal(h.spoken.length, 1);
  h.advance(1);
  assert.equal(h.spoken.length, 2);
  assert.equal(h.spoken[1].text, '前方有瓶子。');
  h.finish();
  h.announcer.announce(detection([item('cup')]));
  assert.equal(h.spoken.length, 2);
  h.advance(20000);
  h.announcer.announce(detection([item('cup')]));
  assert.equal(h.spoken.length, 2);
});

test('pending speech uses latest objects and does not announce disappeared objects', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.finish();
  h.advance(500);
  h.announcer.announce(detection([item('bottle')]));
  h.announcer.announce(detection([]));
  h.advance(2500);
  assert.equal(h.spoken.length, 1);
});

test('stale detections are not spoken after long audio playback', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.announcer.announce(detection([item('bottle')]));
  h.advance(5000);
  h.finish();
  assert.equal(h.spoken.length, 1);
});

test('stopping camera cancels active speech and ignores late callbacks', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  const active = h.spoken[0];
  h.announcer.stop();
  active.onend();
  h.announcer.announce(detection([item('bottle')]));
  h.advance(10000);
  assert.equal(h.spoken.length, 1);
  assert.equal(h.announcer.active, false);
  assert.ok(h.synth.cancelCount > 0);
});

test('turning off speech cancels scheduled utterance', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.finish();
  h.announcer.announce(detection([item('bottle')]));
  h.announcer.setEnabled(false);
  h.advance(10000);
  h.announcer.announce(detection([item('chair')]));
  assert.equal(h.spoken.length, 1);
  assert.equal(h.timers.size, 0);
});

test('server speech setting and confidence threshold are respected', () => {
  const h = setup();
  h.announcer.configure({speech_enabled: false});
  h.announcer.setEnabled(true);
  h.announcer.announce(detection([item('cup')]));
  assert.equal(h.spoken.length, 0);
  h.announcer.configure({speech_enabled: true, confidence: 0.8});
  h.announcer.announce(detection([item('cup', 'front', 0.5)]));
  assert.equal(h.spoken.length, 0);
});

test('permission failures show an actionable message and manual test retries', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.spoken[0].onerror({error: 'not-allowed'});
  h.announcer.announce(detection([item('bottle')]));
  assert.equal(h.spoken.length, 1);
  assert.match(h.statuses.at(-1), /测试播报/);
  h.announcer.test();
  assert.equal(h.spoken.length, 2);
  assert.equal(h.announcer.failed, false);
});

test('unsupported browser reports a useful status without throwing', () => {
  const statuses = [];
  const a = create({synth: null, onStatus: message => statuses.push(message)});
  a.start();
  a.test();
  assert.equal(a.supported, false);
  assert.match(statuses.at(-1), /不支持语音播报/);
});

test('OCR suspension blocks object speech and does not cancel external reading', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.announcer.suspend();
  const cancellations = h.synth.cancelCount;
  h.announcer.announce(detection([item('bottle')]));
  h.announcer.test();
  h.announcer.configure({speech_enabled: true});
  assert.equal(h.spoken.length, 1);
  assert.equal(h.synth.cancelCount, cancellations);
  h.announcer.resume();
  h.announcer.announce(detection([item('bottle')]));
  assert.equal(h.spoken.length, 2);
});

test('depth risk alerts use a distinct phrase and suppress the generic target announcement', () => {
  const h = setup();
  h.announcer.announce(detection([{
    ...item('chair', 'left'), risk: {status: 'valid', level: 'L2'}
  }]));
  assert.equal(h.spoken.length, 0);
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L2',
    track_id: 4, object: 'chair', direction: 'left', speech_priority: 2,
    relative_depth: {level: 'near'}, event_id: 'event-1'});
  assert.equal(h.spoken.length, 1);
  assert.equal(h.spoken[0].text, '注意，左侧有椅子，距离较近');
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L2',
    track_id: 4, object: 'chair', direction: 'left', speech_priority: 2,
    event_id: 'event-2'});
  assert.equal(h.spoken.length, 1);
});

test('disabled risk alerts leave ordinary object speech available and honor the speech gate', () => {
  const h = setup();
  h.announcer.announce(detection([{
    ...item('chair'), risk: {status: 'valid', level: 'L2'}
  }], {risk_alerts_enabled: false}));
  assert.equal(h.spoken.length, 1);
  assert.equal(h.spoken[0].text, '前方有椅子。');
  h.finish();
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L3',
    track_id: 4, object: 'person', speech_enabled: false});
  assert.equal(h.spoken.length, 1);
});

test('L3 depth alert interrupts ordinary speech and ignores its late callback', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  const ordinary = h.spoken[0];
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L3',
    track_id: 9, object: 'person', direction: 'front', speech_priority: 3});
  assert.equal(h.spoken.length, 2);
  assert.equal(h.spoken[1].text, '危险，前方有行人，请注意');
  assert.ok(h.synth.cancelCount > 0);
  ordinary.onend();
  assert.equal(h.announcer.activeAlertPriority, 3);
});

test('queued depth warnings keep the highest pending level', () => {
  const h = setup();
  h.announcer.announce(detection([item('cup')]));
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L1',
    track_id: 1, object: 'chair', direction: 'left', relative_depth: {level: 'far'}});
  h.announcer.announceAlert({device_id: 'local_camera', alert_state: 'L2',
    track_id: 2, object: 'person', direction: 'front'});
  h.finish();
  assert.equal(h.spoken.length, 2);
  assert.equal(h.spoken[1].text, '注意，前方有行人，距离较近');
});
