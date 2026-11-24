(function (root) {
  'use strict';

  function splitText(text, limit = 100) {
    const chunks = [];
    let chunk = '';
    for (const character of text) {
      chunk += character;
      if (chunk.length >= limit || /[。！？!?\n]/.test(character)) {
        if (chunk.trim()) chunks.push(chunk.trim());
        chunk = '';
      }
    }
    if (chunk.trim()) chunks.push(chunk.trim());
    return chunks;
  }

  function drawBoxes(context, result, width, height) {
    if (!result || !Array.isArray(result.boxes)) return 0;
    const sourceWidth = Number(result.image_width);
    const sourceHeight = Number(result.image_height);
    if (!(sourceWidth > 0 && sourceHeight > 0)) return 0;
    const scaleX = width / sourceWidth, scaleY = height / sourceHeight;
    let count = 0;
    context.save();
    context.lineJoin = 'round';
    for (const box of result.boxes) {
      if (!Array.isArray(box?.points) || box.points.length < 4) continue;
      const points = box.points.map(point => [Number(point?.[0]) * scaleX, Number(point?.[1]) * scaleY]);
      if (points.some(point => !Number.isFinite(point[0]) || !Number.isFinite(point[1]))) continue;
      context.beginPath();
      context.moveTo(...points[0]);
      for (const point of points.slice(1)) context.lineTo(...point);
      context.closePath();
      context.fillStyle = 'rgba(22, 119, 255, .12)';
      context.fill();
      context.lineWidth = Math.max(4, width / 240);
      context.strokeStyle = 'rgba(255, 255, 255, .94)';
      context.stroke();
      context.lineWidth = Math.max(2, width / 480);
      context.strokeStyle = '#1677FF';
      context.stroke();
      count += 1;
    }
    context.restore();
    return count;
  }

  class Preview {
    constructor({canvas, legend, count, resumeButton, decodeImage}) {
      Object.assign(this, {canvas, legend, count, resumeButton});
      this.decodeImage = decodeImage || (blob => {
        if (root.createImageBitmap) return root.createImageBitmap(blob);
        return new Promise((resolve, reject) => {
          const image = new root.Image();
          const url = root.URL.createObjectURL(blob);
          image.onload = () => { root.URL.revokeObjectURL(url); resolve(image); };
          image.onerror = () => { root.URL.revokeObjectURL(url); reject(new Error('图片解码失败')); };
          image.src = url;
        });
      });
      this.generation = 0;
      this.frame = this.result = null;
      this.loading = false;
      if (resumeButton) resumeButton.onclick = () => this.reset();
    }

    reset() {
      this.generation += 1;
      this.frame?.close?.();
      this.frame = this.result = null;
      this.loading = false;
      if (this.legend) this.legend.hidden = true;
      if (this.resumeButton) this.resumeButton.hidden = true;
    }

    async capture(blob) {
      this.reset();
      if (!blob) return;
      const generation = this.generation;
      this.loading = true;
      try {
        const frame = await this.decodeImage(blob);
        if (generation !== this.generation) { frame.close?.(); return; }
        this.frame = frame;
        this.loading = false;
        if (this.resumeButton) this.resumeButton.hidden = false;
      } catch (_) {
        if (generation === this.generation) this.reset();
      }
    }

    setResult(result) {
      if (this.frame || this.loading) this.result = result;
    }

    draw(fallback) {
      if (!this.frame) return fallback();
      const scale = Math.min(1, 1600 / this.frame.width);
      this.canvas.width = Math.round(this.frame.width * scale);
      this.canvas.height = Math.round(this.frame.height * scale);
      const context = this.canvas.getContext('2d');
      context.drawImage(this.frame, 0, 0, this.canvas.width, this.canvas.height);
      const count = drawBoxes(context, this.result, this.canvas.width, this.canvas.height);
      if (this.legend) this.legend.hidden = false;
      if (this.count) this.count.textContent = !this.result ? '本次截图 · 正在识字' : this.result.failed ? '本次识字 · 已中断' : count
        ? `本次识别 · ${count} 处文字` : this.result.has_text ? '本次识别 · 无位置框' : '本次识别 · 未发现文字';
      return true;
    }
  }

  class Reader {
    constructor(options) {
      Object.assign(this, options);
      this.synth = options.synth;
      this.Utterance = options.Utterance;
      this.tts = options.tts || root.QimingEdgeTts;
      this.allowInjectedSynth = Boolean(this.synth && this.Utterance);
      this.supported = Boolean(this.tts?.supported || this.allowInjectedSynth);
      this.generation = 0;
      this.busy = false;
      this.controller = null;
      this.utterance = null;
      this.text = '';
      this.taskId = null;
      this.deviceId = null;
      this.ttsTarget = 'browser';
      this.ttsState = 'not_requested';
      this.allowed = true;
    }

    setBusy(value) {
      this.busy = value;
      if (value) this.objectSpeech.suspend();
      else this.objectSpeech.resume();
      this.onBusy(value);
    }

    cancel(message = '已停止识字或朗读') {
      this.generation += 1;
      if (this.controller) this.controller.abort();
      this.controller = null;
      if (this.utterance) this.updateTts('interrupted');
      if (this.ttsTarget === 'device' && this.deviceId) {
        const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
        root.fetch?.('/api/tts/stop', {
          method: 'POST', headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
          body: JSON.stringify({device_id: this.deviceId}), keepalive: true,
        }).catch(() => {});
      }
      this.tts?.cancel?.();
      if (this.busy && this.allowInjectedSynth) this.synth.cancel();
      this.utterance = null;
      this.setBusy(false);
      this.onStatus(message);
    }

    async recognize(capture, request) {
      if (this.busy) return;
      this.cancel('正在拍摄并识字，首次加载模型可能较慢…');
      this.onCapture?.(null);
      this.text = '';
      this.onText('');
      this.setBusy(true);
      const generation = this.generation;
      this.controller = new AbortController();
      const signal = this.controller.signal;
      try {
        const blob = await capture();
        if (generation !== this.generation) return;
        this.onCapture?.(blob);
        const result = await request(blob, signal);
        if (generation !== this.generation) return;
        this.taskId = result.task_id || null;
        this.deviceId = result.device_id || result.source_id || null;
        this.ttsTarget = result.tts_target || (result.source_mode === 'hardware' ? 'device' : 'browser');
        this.ttsState = result.tts_status || 'not_requested';
        this.onTask?.(result);
        this.text = result.has_text ? result.text : '';
        this.onText(result.text, result);
        this.controller = null;
        if (!result.has_text) {
          this.setBusy(false);
          this.onStatus('未识别到清晰文字，请靠近文字并保持画面稳定');
        } else if (result.truncated) {
          this.setBusy(false);
          this.onStatus('文字较多，结果可能不完整。请分区域拍摄；可点击“重新朗读”播放当前结果');
        } else if (this.ttsTarget === 'device') {
          this.setBusy(false);
          this.onStatus(this.ttsState === 'synthesizing'
            ? '文字已识别，正在合成语音并发送到帽子…'
            : '文字已识别，帽端正在播报');
        } else {
          this.read();
        }
      } catch (error) {
        if (generation !== this.generation) return;
        this.controller = null;
        this.onText('', {has_text: false, boxes: [], failed: true});
        this.setBusy(false);
        this.onStatus(error.name === 'AbortError' ? '识字已取消' : '识字失败：' + error.message);
      }
    }

    read() {
      if (!this.text) return;
      if (this.ttsTarget === 'device') {
        if (!this.deviceId || !this.taskId) return this.onStatus('帽端播报任务信息缺失，请重新识字');
        const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
        this.onStatus('正在重新发送到帽端…');
        root.fetch?.(`/api/voice-vision/tasks/${this.taskId}/speak`, {
          method: 'POST', headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
        }).then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || '帽端播报失败');
          this.ttsState = data.status || 'queued';
          this.onStatus(this.ttsState === 'synthesizing' ? '正在重新合成语音…' : '已发送，等待帽端播报回执');
        }).catch(error => this.onStatus('帽端播报失败：' + error.message));
        return;
      }
      this.cancel('准备朗读');
      if (!this.allowed) { this.updateTts('disabled'); return this.onStatus('系统语音播报已关闭，识别文字已保留'); }
      if (!this.supported) { this.updateTts('unavailable'); return this.onStatus('Edge TTS 音频服务不可用，请检查电脑后端'); }
      this.setBusy(true);
      this.updateTts('pending');
      const generation = this.generation;
      const chunks = splitText(this.text);
      let index = 0;
      const next = () => {
        if (generation !== this.generation) return;
        if (index === chunks.length) {
          this.utterance = null;
          this.setBusy(false);
          this.updateTts('completed');
          this.onStatus('朗读完成');
          return;
        }
        const chunk = chunks[index++];
        const onstart = () => {
          if (generation === this.generation) { this.updateTts('speaking'); this.onStatus(`正在朗读（${index}/${chunks.length}）`); }
        };
        const onerror = event => {
          if (generation !== this.generation) return;
          this.updateTts('failed');
          this.cancel(`Edge TTS 朗读失败${event?.message ? '：' + event.message : ''}`);
        };
        if (this.tts?.supported) {
          const playback = {cancel: () => this.tts.cancel?.()};
          this.utterance = playback;
          this.tts.speak(chunk, {onstart, onend: next, onerror})
            .catch(error => onerror(error));
        } else if (this.allowInjectedSynth) {
          const utterance = new this.Utterance(chunk);
          this.utterance = utterance;
          utterance.onstart = onstart;
          utterance.onend = next;
          utterance.onerror = onerror;
          this.synth.speak(utterance);
        } else {
          onerror(new Error('Edge TTS 音频服务不可用'));
        }
      };
      next();
    }

    updateTts(status) {
      this.ttsState = status;
      if (this.ttsTarget !== 'browser' || !this.taskId || typeof root.fetch !== 'function') return;
      const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
      root.fetch(`/api/voice-vision/tasks/${this.taskId}/tts`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
        body: JSON.stringify({status, target: this.ttsTarget}), keepalive: true,
      }).catch(() => {});
    }

    handleTtsEvent(event) {
      if (!event || event.task_id !== this.taskId || this.ttsTarget !== 'device') return;
      this.ttsState = event.status;
      const messages = {
        queued: '语音已合成并发送，等待帽端播放',
        synthesizing: '正在合成语音并发送到帽子…',
        speaking: '帽端正在朗读识别到的文字',
        completed: '帽端播报完成并已回执',
        interrupted: '帽端播报已停止',
        failed: `帽端播报失败${event.detail ? `：${event.detail}` : ''}`,
      };
      this.onStatus(messages[event.status] || `帽端播报状态：${event.status}`);
    }

    configure(allowed) {
      this.allowed = allowed;
      if (!allowed && this.utterance) this.cancel('系统语音播报已关闭，识别文字已保留');
      this.onBusy(this.busy);
    }
  }

  function mount({video, objectSpeech, isCameraActive, captureFrame, getSource, panelId = 'monitor', preview}) {
    const panel = document.getElementById(panelId) || document.getElementById('monitor');
    const captureButton = panel.querySelector('#ocrCapture');
    const replayButton = panel.querySelector('#ocrReplay');
    const stopButton = panel.querySelector('#ocrStop');
    const emptyState = panelId === 'monitor' ? null : panel.querySelector('#ocrEmptyState');
    const reader = new Reader({
      objectSpeech,
      onStatus: message => { panel.querySelector('#ocrStatus').textContent = message; },
      onCapture: blob => preview?.capture(blob),
      onText: (text, result) => {
        panel.querySelector('#ocrText').textContent = text;
        panel.querySelector('#ocrResults').hidden = !text;
        if (emptyState) emptyState.hidden = Boolean(text);
        preview?.setResult(result);
      },
      onTask: task => root.dispatchEvent?.(new root.CustomEvent('qiming-voice-task-completed', {detail: task})),
      onBusy: busy => {
        captureButton.disabled = busy;
        stopButton.disabled = !busy;
        replayButton.disabled = busy || !reader.text || !reader.allowed ||
          (reader.ttsTarget !== 'device' && !reader.supported);
      },
    });
    const capture = async () => {
      if (captureFrame) {
        if (!isCameraActive()) throw new Error('请先启动所选来源，并等待画面出现');
        const blob = await captureFrame();
        if (!blob) throw new Error('当前来源没有可用画面');
        if (blob.size > 6000000) throw new Error('图片过大，请降低摄像头分辨率');
        return blob;
      }
      if (!isCameraActive() || video.readyState < 2 || !video.videoWidth) {
        throw new Error('请先启动摄像头，并等待画面出现');
      }
      const canvas = document.createElement('canvas');
      canvas.width = Math.min(video.videoWidth, 1600);
      canvas.height = Math.round(video.videoHeight * canvas.width / video.videoWidth);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92));
      if (!blob) throw new Error('拍摄失败，请重新启动摄像头');
      if (blob.size > 6000000) throw new Error('图片过大，请降低摄像头分辨率');
      return blob;
    };
    const request = async (blob, signal) => {
      const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
      const source = getSource?.() || {source_id: 'upload', source_mode: 'software'};
      const sourceMode = source.source_mode || 'software';
      const query = new URLSearchParams({intent: 'OCR_READ', text: '读一下画面里的文字', source_id: source.source_id || 'upload', source_mode: sourceMode, tts_target: sourceMode === 'hardware' ? 'device' : 'browser'});
      if (source.frame_seq != null) query.set('frame_seq', source.frame_seq);
      const response = await root.fetch('/api/voice-vision/tasks?' + query.toString(), {
        method: 'POST', signal, body: blob,
        headers: {'Content-Type': 'image/jpeg', Authorization: 'Bearer ' + (token || '')},
      });
      const task = await response.json();
      if (!response.ok) throw new Error(task.detail || '服务请求失败');
      return {...task, ...(task.ocr_result || {has_text: false, text: task.final_reply || '未识别到清晰文字', truncated: false}), task_id: task.task_id};
    };
    objectSpeech.onConfigure = config => reader.configure(config.speech_enabled !== false);
    reader.configure(objectSpeech.allowed);
    captureButton.onclick = () => reader.recognize(capture, request);
    replayButton.onclick = () => reader.read();
    stopButton.onclick = () => reader.cancel();
    window.addEventListener('qiming-camera-stopped', () => {
      reader.cancel('摄像头已停止，识字和朗读已取消');
      preview?.setResult({has_text: false, boxes: [], failed: true});
    });
    window.addEventListener('qiming-camera-source-changed', () => {
      reader.cancel('画面来源已切换，请重新拍摄识字');
      preview?.reset();
      reader.text = '';
      reader.onText('');
    });
    window.addEventListener('pagehide', () => reader.cancel());
    return reader;
  }

  const api = {Reader, Preview, drawBoxes, splitText, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingOCR = api;
})(typeof window === 'undefined' ? globalThis : window);
