(function (root) {
  'use strict';

  const SAMPLE_RATE = 16000;

  function downsample(input, inputRate) {
    if (inputRate === SAMPLE_RATE) return input;
    const ratio = inputRate / SAMPLE_RATE;
    const length = Math.round(input.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(Math.floor((i + 1) * ratio), input.length);
      let total = 0;
      for (let j = start; j < end; j += 1) total += input[j];
      output[i] = total / Math.max(1, end - start);
    }
    return output;
  }

  function pcm16(samples) {
    const output = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      const value = Math.max(-1, Math.min(1, samples[i]));
      output[i] = value < 0 ? value * 32768 : value * 32767;
    }
    return output.buffer;
  }

  class VoiceInputController {
    constructor({document = root.document, cameraMonitor} = {}) {
      this.document = document;
      this.cameraMonitor = cameraMonitor;
      this.button = document?.getElementById('voiceInputToggle');
      this.source = document?.getElementById('voiceInputSource');
      this.statusNode = document?.getElementById('voiceInputStatus');
      this.transcriptNode = document?.getElementById('voiceInputTranscript');
      this.replyNode = document?.getElementById('voiceInputReply');
      this.ws = null;
      this.stream = null;
      this.context = null;
      this.processor = null;
      this.inputNode = null;
      this.flushWaiter = null;
      this.sessionId = '';
      this.ending = false;
      this.active = false;
      this.replyAudio = null;
      this.replyUrl = null;
      if (this.button) this.button.onclick = () => this.active ? this.stop() : this.start();
      if (this.source) this.source.onchange = () => this.updateStatus();
      root.addEventListener?.('qiming-camera-stopped', () => {
        if (this.active) this.cancel('摄像头已停止，语音对话已取消');
      });
      root.addEventListener?.('qiming-camera-source-changed', () => {
        if (this.active) this.cancel('画面来源已切换，语音对话已取消');
        else this.updateStatus('画面来源已切换');
      });
      root.addEventListener?.('pagehide', () => this.cancel('页面已离开，语音对话已取消'));
    }

    sourceInfo() {
      const sourceId = this.cameraMonitor?.sourceId || 'local_camera';
      const sourceMode = sourceId.startsWith('simulator_') ? 'simulation'
        : sourceId === 'local_camera' ? 'software' : 'hardware';
      const ttsTarget = sourceMode === 'hardware' ? 'device' : sourceMode === 'simulation' ? 'simulation' : 'browser';
      const rawFrameSeq = this.cameraMonitor?.lastDetection?.image_seq ??
        (sourceId === 'local_camera' ? this.cameraMonitor?.seq : this.cameraMonitor?.frameSeq);
      const frameSeq = rawFrameSeq == null || rawFrameSeq === '' || !Number.isFinite(Number(rawFrameSeq))
        ? null : Number(rawFrameSeq);
      return {sourceId, sourceMode, ttsTarget, frameSeq};
    }

    setButton(label, disabled = false) {
      if (!this.button) return;
      this.button.textContent = label;
      this.button.disabled = disabled;
    }

    updateStatus(message) {
      if (this.statusNode) this.statusNode.textContent = message || (this.active ? '正在聆听，请说完后点击停止' : '电脑麦克风未启动');
    }

    async start() {
      if (this.active) return;
      if (this.source?.value === 'hat') {
        this.updateStatus('帽端麦克风将在硬件接入后启用');
        return;
      }
      if (!this.cameraMonitor?.active?.()) {
        this.updateStatus('请先启动实时监测并等待画面，再开始语音对话');
        return;
      }
      if (!root.navigator?.mediaDevices?.getUserMedia) {
        this.updateStatus('当前浏览器不支持麦克风，请使用 Chrome 或 Edge');
        return;
      }
      try {
        this.stream = await root.navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        this.context = new (root.AudioContext || root.webkitAudioContext)();
        await this.context.resume?.();
        if (!this.context.audioWorklet?.addModule || typeof root.AudioWorkletNode !== 'function') {
          throw new Error('当前浏览器不支持 AudioWorklet，请使用最新版 Chrome 或 Edge');
        }
        await this.context.audioWorklet.addModule('/voice-input-worklet.js');
        this.inputNode = this.context.createMediaStreamSource(this.stream);
        this.processor = new root.AudioWorkletNode(this.context, 'qiming-pcm-capture', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
          channelCount: 1,
          channelCountMode: 'explicit',
          channelInterpretation: 'discrete',
        });
        this.processor.port.onmessage = event => {
          const message = event.data || {};
          if (message.type === 'audio' && this.active && this.ws?.readyState === 1) {
            const samples = downsample(message.samples, this.context.sampleRate);
            if (samples.length) this.ws.send(pcm16(samples));
          } else if (message.type === 'flushed') {
            this.flushWaiter?.();
          }
        };
        this.inputNode.connect(this.processor);
        this.processor.connect(this.context.destination);
        this.sessionId = `browser-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        this.active = true;
        this.ending = false;
        this.button?.parentElement?.setAttribute?.('data-state', 'recording');
        this.setButton('停止并处理', false);
        const info = this.sourceInfo();
        const protocol = root.location?.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new root.WebSocket(`${protocol}//${root.location.host}/ws/audio`);
        this.ws.onopen = () => {
          if (!this.active || this.ending) {
            this.ws.close();
            return;
          }
          this.ws.send(JSON.stringify({type: 'audio_start', device_id: 'browser_mic', session_id: this.sessionId,
            source_id: info.sourceId, source_mode: info.sourceMode, frame_seq: info.frameSeq, tts_target: info.ttsTarget,
            input_source: 'computer',
            sample_rate: SAMPLE_RATE, channels: 1, bits_per_sample: 16}));
          this.updateStatus('正在聆听，请说完后点击停止');
        };
        this.ws.onmessage = event => this.handleMessage(event.data);
        this.ws.onerror = () => this.cleanup('语音连接失败，请检查后端服务');
        this.ws.onclose = () => { if (this.active && !this.ending) this.cleanup('语音连接已断开'); };
      } catch (error) {
        this.cleanup(error.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器中允许访问' : `麦克风启动失败：${error.message}`);
      }
    }

    async stop() {
      if (!this.active || this.ending) return;
      this.ending = true;
      this.setButton('正在识别…', true);
      this.updateStatus('正在识别语音并组织当前画面回复…');
      await this.flushCapture();
      if (!this.active || !this.ending) return;
      if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({type: 'audio_end', session_id: this.sessionId}));
      this.releaseCapture();
    }

    flushCapture() {
      const port = this.processor?.port;
      if (!port) return Promise.resolve();
      return new Promise(resolve => {
        let settled = false;
        let timer;
        const finish = () => {
          if (settled) return;
          settled = true;
          root.clearTimeout?.(timer);
          this.flushWaiter = null;
          resolve();
        };
        this.flushWaiter = finish;
        timer = root.setTimeout?.(finish, 500);
        try { port.postMessage({type: 'flush'}); } catch (_) { finish(); }
      });
    }

    releaseCapture() {
      this.processor?.disconnect();
      this.inputNode?.disconnect();
      this.stream?.getTracks?.().forEach(track => track.stop());
      this.context?.close?.();
      this.processor = this.inputNode = this.stream = this.context = null;
    }

    cancel(message = '语音对话已取消') {
      if (!this.active) return;
      if (this.ws?.readyState === 1 && this.sessionId) {
        this.ws.send(JSON.stringify({type: 'audio_cancel', session_id: this.sessionId}));
      }
      this.cleanup(message);
    }

    handleMessage(data) {
      let message;
      try { message = typeof data === 'string' ? JSON.parse(data) : data; } catch (_) { return; }
      if (message.type === 'audio_started') this.updateStatus('正在聆听，请说完后点击停止');
      if (message.type === 'asr_partial' && message.text) {
        if (this.transcriptNode) this.transcriptNode.textContent = `正在识别：“${message.text}”`;
        this.updateStatus('正在识别语音…');
      }
      if (message.type === 'error') this.cleanup(message.detail || '语音处理失败');
      if (message.type === 'voice_result') {
        if (message.asr_status !== 'ok') return this.cleanup(message.error || '没有识别到语音');
        if (this.transcriptNode) this.transcriptNode.textContent = `刚才说：“${message.text}”`;
        if (this.replyNode) this.replyNode.textContent = message.final_reply || message.reply || '';
        root.dispatchEvent?.(new root.CustomEvent('qiming-live-voice-task-completed', {detail: message}));
        this.updateStatus(`已识别意图：${message.intent || '处理中'} · 正在朗读回复`);
        this.cleanup('电脑麦克风未启动');
      }
    }

    stopReply() {
      root.QimingEdgeTts?.cancel?.();
      if (this.replyAudio) {
        this.replyAudio.pause();
        this.replyAudio.src = '';
        this.replyAudio = null;
      }
      if (this.replyUrl) root.URL.revokeObjectURL(this.replyUrl);
      this.replyUrl = null;
    }

    cleanup(message = '电脑麦克风未启动') {
      this.active = false;
      this.ending = false;
      this.flushWaiter?.();
      this.processor?.disconnect?.();
      this.inputNode?.disconnect?.();
      this.stream?.getTracks?.().forEach(track => track.stop());
      this.context?.close?.();
      if (this.ws && this.ws.readyState < 2) this.ws.close();
      this.ws = this.processor = this.inputNode = this.stream = this.context = null;
      this.setButton('启动语音对话', false);
      this.button?.parentElement?.removeAttribute?.('data-state');
      this.updateStatus(message);
    }
  }

  const api = {VoiceInputController, downsample, pcm16};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingVoiceInput = api;
})(typeof window === 'undefined' ? globalThis : window);
