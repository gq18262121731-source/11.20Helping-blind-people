(function (root) {
  'use strict';

  class EdgeTtsAudioClient {
    constructor(options = {}) {
      this.fetch = options.fetch || root.fetch?.bind(root);
      this.Audio = options.Audio || root.Audio;
      this.URL = options.URL || root.URL;
      this.audio = null;
      this.url = null;
      this.requestController = null;
      this.rejectCurrent = null;
      this.generation = 0;
      this.supported = Boolean(this.fetch && this.Audio);
    }

    headers(extra = {}) {
      const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
      return {...(token ? {Authorization: 'Bearer ' + token} : {}), ...extra};
    }

    cancel() {
      this.generation += 1;
      this.requestController?.abort();
      this.requestController = null;
      const reject = this.rejectCurrent;
      this.rejectCurrent = null;
      if (reject) {
        const error = new Error('Edge TTS playback cancelled');
        error.name = 'AbortError';
        reject(error);
      }
      if (this.audio) {
        this.audio.pause?.();
        this.audio.src = '';
        this.audio = null;
      }
      if (this.url?.startsWith('blob:')) this.URL.revokeObjectURL(this.url);
      this.url = null;
    }

    async speak(text, handlers = {}) {
      if (!this.supported) throw new Error('当前浏览器不支持 Edge TTS 音频播放');
      const value = String(text || '').trim();
      if (!value) return;
      this.cancel();
      const generation = this.generation;
      const controller = new AbortController();
      this.requestController = controller;
      const response = await this.fetch('/api/tts/browser-ticket', {
        method: 'POST',
        headers: this.headers({'Content-Type': 'application/json'}),
        body: JSON.stringify({text: value}),
        signal: controller.signal,
      });
      if (generation !== this.generation) return;
      this.requestController = null;
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Edge TTS 请求失败（${response.status}）`);
      }
      const ticket = await response.json();
      if (generation !== this.generation) return;
      if (!ticket.url) throw new Error('Edge TTS 未返回可播放链接');
      const url = ticket.url;
      const audio = new this.Audio(url);
      this.audio = audio;
      this.url = url;
      return new Promise((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
          if (this.audio === audio) this.audio = null;
          if (this.url === url) this.url = null;
          if (url.startsWith('blob:')) this.URL.revokeObjectURL(url);
          if (this.rejectCurrent === rejectOnce) this.rejectCurrent = null;
        };
        const resolveOnce = () => {
          if (settled) return;
          settled = true;
          cleanup();
          handlers.onend?.();
          resolve();
        };
        const rejectOnce = error => {
          if (settled) return;
          settled = true;
          cleanup();
          handlers.onerror?.(error);
          reject(error instanceof Error ? error : new Error('Edge TTS audio playback failed'));
        };
        this.rejectCurrent = rejectOnce;
        audio.onplay = () => handlers.onstart?.();
        audio.onended = resolveOnce;
        audio.onerror = rejectOnce;
        Promise.resolve(audio.play()).catch(rejectOnce);
      });
    }
  }

  const client = new EdgeTtsAudioClient();
  if (typeof module !== 'undefined' && module.exports) module.exports = {EdgeTtsAudioClient};
  else root.QimingEdgeTts = client;
})(typeof window === 'undefined' ? globalThis : window);
