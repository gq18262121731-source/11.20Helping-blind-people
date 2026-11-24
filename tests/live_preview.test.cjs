const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function flush() {
  await new Promise(resolve => setImmediate(resolve));
}

function previewHarness(versions, failedStylesheets = 0) {
  const timers = new Map();
  const stored = new Map();
  const state = {reloads: 0, requests: 0, stylesheets: []};
  let nextTimer = 0;
  const activePage = {dataset: {page: 'monitor'}};

  function makeLink(href) {
    return {
      href,
      rel: 'stylesheet',
      remove() {
        state.stylesheets = state.stylesheets.filter(link => link !== this);
      },
      cloneNode() {
        return makeLink(this.href);
      },
      after(replacement) {
        state.stylesheets.push(replacement);
        queueMicrotask(() => {
          if (failedStylesheets > 0) {
            failedStylesheets--;
            replacement.onerror();
          } else {
            replacement.onload();
          }
        });
      }
    };
  }

  state.stylesheets.push(makeLink('http://localhost:8765/brand.css'));
  const location = {
    origin: 'http://localhost:8765',
    protocol: 'http:',
    host: 'localhost:8765',
    href: 'http://localhost:8765/dashboard.html',
    reload() { state.reloads++; }
  };
  const sessionStorage = {
    getItem(key) { return stored.get(key) ?? null; },
    setItem(key, value) { stored.set(key, value); },
    removeItem(key) { stored.delete(key); }
  };
  const document = {
    hidden: false,
    addEventListener() {},
    querySelector(selector) { return selector === '.nav-item.active' ? activePage : null; },
    querySelectorAll(selector) {
      return selector === 'link[rel="stylesheet"]' ? state.stylesheets : [];
    }
  };
  const context = {
    document,
    location,
    sessionStorage,
    URL,
    Promise,
    Array,
    AbortController,
    fetch: async () => {
      state.requests++;
      return {ok: true, json: async () => versions[Math.min(state.requests - 1, versions.length - 1)]};
    },
    setTimeout(callback, delay) {
      const id = ++nextTimer;
      timers.set(id, {callback, delay});
      return id;
    },
    clearTimeout(id) { timers.delete(id); }
  };

  vm.runInNewContext(fs.readFileSync('frontend/live-preview.js', 'utf8'), context);
  return {
    state,
    stored,
    async pollAgain() {
      const entry = Array.from(timers.entries()).find(([, timer]) => timer.delay === 1000);
      assert.ok(entry, 'the next preview check is scheduled');
      timers.delete(entry[0]);
      entry[1].callback();
      await flush();
    }
  };
}

test('CSS changes replace the stylesheet without reloading the page', async () => {
  const preview = previewHarness([
    {enabled: true, styles: {'/brand.css': 'old'}, reload: 'same-page'},
    {enabled: true, styles: {'/brand.css': 'new'}, reload: 'same-page'}
  ]);
  await flush();
  await preview.pollAgain();

  assert.equal(preview.state.reloads, 0);
  assert.equal(new URL(preview.state.stylesheets[0].href).searchParams.get('_qiming_preview'), 'new');
});

test('HTML, JavaScript and image changes reload and restore the selected dashboard page', async () => {
  const preview = previewHarness([
    {enabled: true, styles: {}, reload: 'before'},
    {enabled: true, styles: {}, reload: 'after'}
  ]);
  await flush();
  await preview.pollAgain();

  assert.equal(preview.state.reloads, 1);
  assert.equal(preview.stored.get('qh_preview_page'), 'monitor');
});

test('a stylesheet that fails once is retried until the new version loads', async () => {
  const preview = previewHarness([
    {enabled: true, styles: {'/brand.css': 'old'}, reload: 'same-page'},
    {enabled: true, styles: {'/brand.css': 'new'}, reload: 'same-page'},
    {enabled: true, styles: {'/brand.css': 'new'}, reload: 'same-page'}
  ], 1);
  await flush();
  await preview.pollAgain();
  assert.equal(new URL(preview.state.stylesheets[0].href).searchParams.has('_qiming_preview'), false);

  await preview.pollAgain();
  assert.equal(preview.state.reloads, 0);
  assert.equal(new URL(preview.state.stylesheets[0].href).searchParams.get('_qiming_preview'), 'new');
});
