const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load({fetch, Audio, URL}) {
  const context = {module: {exports: {}}, fetch, Audio, URL, AbortController, sessionStorage: {getItem() {return 'token';}}, localStorage: {getItem() {return null;}}};
  vm.runInNewContext(fs.readFileSync('frontend/tts-client.js', 'utf8'), context);
  return context.module.exports.EdgeTtsAudioClient;
}

test('Edge TTS client requests a one-use stream ticket for each computer playback', async () => {
  let requests = 0;
  const audios = [];
  const FakeAudio = class {
    constructor(url) { this.url = url; audios.push(this); }
    play() { this.onplay?.(); return Promise.resolve(); }
    pause() {}
  };
  const Client = load({
    Audio: FakeAudio,
    URL: {},
    fetch: async (url, options) => {
      requests += 1;
      assert.equal(url, '/api/tts/browser-ticket');
      assert.equal(options.headers.Authorization, 'Bearer token');
      return {ok: true, async json() {return {url: `/api/tts/browser-stream/ticket-${requests}`, cached: requests > 1};}};
    },
  });
  const client = new Client();
  let starts = 0;
  const first = client.speak('前方有出口。', {onstart() {starts += 1;}});
  while (!audios[0]?.onended) await new Promise(resolve => setImmediate(resolve));
  audios[0].onended();
  await first;
  const second = client.speak('前方有出口。');
  while (!audios[1]?.onended) await new Promise(resolve => setImmediate(resolve));
  audios[1].onended();
  await second;
  assert.equal(requests, 2);
  assert.equal(starts, 1);
  assert.deepEqual(audios.map(audio => audio.url), [
    '/api/tts/browser-stream/ticket-1', '/api/tts/browser-stream/ticket-2',
  ]);
});

test('cancelling Edge TTS aborts an in-flight request without falling back to browser speech', async () => {
  let aborted = false;
  let resolveFetch;
  const Client = load({
    Audio: class {},
    URL: {},
    fetch: async (_url, options) => {
      return new Promise((_resolve, reject) => {
        resolveFetch = () => {};
        options.signal.addEventListener('abort', () => {
          aborted = true;
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        });
      });
    },
  });
  const client = new Client();
  const pending = client.speak('需要取消的语音');
  client.cancel();
  resolveFetch?.({ok: true, async blob() {return {};}});
  await assert.rejects(pending, error => error.name === 'AbortError');
  assert.equal(aborted, true);
});
