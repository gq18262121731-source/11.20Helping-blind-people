(function (root) {
  'use strict';

  const names = {
    person: '行人', bicycle: '自行车', car: '汽车', motorcycle: '摩托车', airplane: '飞机',
    bus: '公交车', train: '火车', truck: '卡车', boat: '船', 'traffic light': '红绿灯',
    'fire hydrant': '消防栓', 'stop sign': '停车标志', 'parking meter': '停车计时器', bench: '长椅',
    bird: '鸟', cat: '猫', dog: '狗', horse: '马', sheep: '羊', cow: '牛', elephant: '大象',
    bear: '熊', zebra: '斑马', giraffe: '长颈鹿', backpack: '背包', umbrella: '雨伞',
    handbag: '手提包', tie: '领带', suitcase: '行李箱', frisbee: '飞盘', skis: '滑雪板',
    snowboard: '单板滑雪板', 'sports ball': '球', kite: '风筝', 'baseball bat': '棒球棒',
    'baseball glove': '棒球手套', skateboard: '滑板', surfboard: '冲浪板', 'tennis racket': '网球拍',
    bottle: '瓶子', 'wine glass': '高脚杯', cup: '水杯', fork: '叉子', knife: '刀', spoon: '勺子',
    bowl: '碗', banana: '香蕉', apple: '苹果', sandwich: '三明治', orange: '橙子', broccoli: '西兰花',
    carrot: '胡萝卜', 'hot dog': '热狗', pizza: '披萨', donut: '甜甜圈', cake: '蛋糕',
    chair: '椅子', couch: '沙发', 'potted plant': '盆栽', bed: '床', 'dining table': '餐桌',
    toilet: '马桶', tv: '电视', laptop: '笔记本电脑', mouse: '鼠标', remote: '遥控器',
    keyboard: '键盘', 'cell phone': '手机', microwave: '微波炉', oven: '烤箱', toaster: '烤面包机',
    sink: '水槽', refrigerator: '冰箱', book: '书', clock: '时钟', vase: '花瓶', scissors: '剪刀',
    'teddy bear': '玩具熊', 'hair drier': '吹风机', toothbrush: '牙刷'
  };
  const directions = {front: '前方', left: '左侧', right: '右侧'};

  function objectName(label) {
    const value = String(label || '').trim().slice(0, 60);
    return names[value.toLowerCase()] || value;
  }

  class Announcer {
    constructor(options = {}) {
      this.synth = options.synth;
      this.Utterance = options.Utterance;
      this.tts = options.tts || root.QimingEdgeTts;
      this.allowInjectedSynth = Boolean(this.synth && this.Utterance);
      this.fetch = options.fetch || root.fetch?.bind(root);
      this.now = options.now || Date.now;
      this.schedule = options.schedule || root.setTimeout.bind(root);
      this.unschedule = options.unschedule || root.clearTimeout.bind(root);
      this.status = options.onStatus || (() => {});
      this.sourceId = options.sourceId || 'local_camera';
      this.supported = Boolean(this.tts?.supported || this.allowInjectedSynth);
      this.enabled = true;
      this.allowed = true;
      this.active = false;
      this.suspended = false;
      this.threshold = 0.35;
      this.gap = 2000;
      this.repeat = Infinity;
      this.latest = [];
      this.lastSpoken = new Map();
      this.lastEnded = -Infinity;
      this.busy = false;
      this.failed = false;
      this.generation = 0;
      this.timer = null;
      this.utterance = null;
      this.pendingAlert = null;
      this.activeAlertPriority = 0;
      this.alertLastSpoken = new Map();
    }

    configure(config) {
      const allowed = config.speech_enabled !== false;
      if (this.allowed && !allowed) this.reset();
      this.allowed = allowed;
      if (Number.isFinite(config.confidence)) this.threshold = config.confidence;
      if (Number.isFinite(config.alert_cooldown_ms)) this.gap = Math.max(2000, config.alert_cooldown_ms);
      if (!allowed) this.status('系统语音播报已关闭');
      if (this.onConfigure) this.onConfigure(config);
    }

    setEnabled(enabled) {
      this.enabled = Boolean(enabled);
      this.reset();
      if (!this.enabled) this.status('识别播报已关闭');
      else if (!this.allowed) this.status('系统语音播报已关闭');
      else if (this.active && !this.isHardwareSource()) this.say('物品识别语音播报已开启');
      else if (this.active && this.isHardwareSource()) this.status('帽端目标播报由电脑 Edge TTS 下发');
      else this.status('识别播报已开启，启动摄像头后自动播报');
    }

    start() {
      this.reset();
      this.active = true;
      if (this.enabled && this.allowed && !this.isHardwareSource()) this.say('物品识别语音播报已开启');
      else if (this.isHardwareSource()) this.status('帽端目标播报由电脑 Edge TTS 下发');
    }

    stop() {
      this.cancelDevice();
      this.active = false;
      this.reset();
      this.status(this.enabled ? '摄像头未启动，识别播报已暂停' : '识别播报已关闭');
    }

    cancelDevice() {
      if (!this.isHardwareSource() || !this.fetch) return;
      const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
      this.fetch('/api/tts/stop', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
        body: JSON.stringify({device_id: this.sourceId}),
        keepalive: true,
      }).catch(() => {});
    }

    suspend() {
      this.reset();
      this.suspended = true;
    }

    resume() {
      this.suspended = false;
    }

    reset() {
      this.generation += 1;
      if (this.timer !== null) this.unschedule(this.timer);
      this.timer = null;
      this.latest = [];
      this.lastSpoken.clear();
      this.lastEnded = -Infinity;
      this.busy = false;
      this.failed = false;
      this.utterance = null;
      this.pendingAlert = null;
      this.activeAlertPriority = 0;
      this.alertLastSpoken.clear();
      this.tts?.cancel?.();
      if (this.allowInjectedSynth && !this.suspended) this.synth.cancel();
    }

    cancelCurrent() {
      this.generation += 1;
      if (this.timer !== null) this.unschedule(this.timer);
      this.timer = null;
      this.busy = false;
      this.utterance = null;
      this.activeAlertPriority = 0;
      this.tts?.cancel?.();
      if (this.allowInjectedSynth && !this.suspended) this.synth.cancel();
    }

    test() {
      this.reset();
      if (!this.allowed) return this.status('系统语音播报已关闭');
      if (this.isHardwareSource() && this.fetch) {
        const token = root.sessionStorage?.getItem('qh_token') || root.localStorage?.getItem('qh_token');
        this.status('正在通过 Edge TTS 向帽端发送测试语音…');
        this.fetch('/api/tts/speak', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
          body: JSON.stringify({device_id: this.sourceId, text: '语音播报测试，识别到物品后将自动播报名称。', priority: 1}),
        }).then(async response => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || '帽端测试语音发送失败');
          this.status(data.status === 'synthesizing' ? 'Edge TTS 正在合成，完成后发送到帽端' : '测试语音已加入帽端队列');
        }).catch(error => this.status('帽端测试语音失败：' + error.message));
        return;
      }
      this.say('语音播报测试，识别到物品后将自动播报名称。');
    }

    isHardwareSource() {
      return Boolean(this.sourceId && this.sourceId !== 'local_camera' && !String(this.sourceId).startsWith('simulator_'));
    }

    setSource(sourceId) {
      if (sourceId === this.sourceId) return;
      this.reset();
      this.sourceId = sourceId;
    }

    announce(result) {
      if (!this.active || !this.enabled || !this.allowed || this.failed || this.suspended) return;
      if (result.device_id !== this.sourceId) return;
      if (result.model_status !== 'ready' || result.simulation) {
        this.latest = [];
        return;
      }
      if (this.isHardwareSource() && this.tts?.supported) {
        this.latest = [];
        return;
      }
      const unique = new Map();
      for (const item of result.objects || []) {
        if (!Number.isFinite(item.confidence) || item.confidence < this.threshold) continue;
        // A depth-aware L1/L2/L3 event has its own priority speech channel.
        // Do not also emit the generic object phrase for the same target.
        if (result.risk_alerts_enabled !== false &&
            ['L1', 'L2', 'L3'].includes(String(item.risk?.level || ''))) continue;
        const name = objectName(item.class);
        if (!name) continue;
        const direction = directions[item.direction] ? item.direction : 'front';
        const key = direction + ':' + name;
        unique.set(key, {key, name, direction});
      }
      this.latest = Array.from(unique.values());
      this.latestAt = this.now();
      this.drain();
    }

    drain() {
      if (!this.active || !this.enabled || !this.allowed || this.failed || this.busy || this.suspended) return;
      const now = this.now();
      if (now - this.latestAt > 4000) return;
      const items = this.latest.filter(item => !this.lastSpoken.has(item.key) || now - this.lastSpoken.get(item.key) >= this.repeat).slice(0, 6);
      if (!items.length) return;
      const wait = this.gap - (now - this.lastEnded);
      if (wait > 0) {
        if (this.timer === null) this.timer = this.schedule(() => { this.timer = null; this.drain(); }, wait);
        return;
      }
      const text = Object.entries(directions).map(([direction, label]) => {
        const group = items.filter(item => item.direction === direction);
        return group.length ? label + '有' + group.map(item => item.name).join('、') : '';
      }).filter(Boolean).join('，') + '。';
      this.say(text, items);
    }

    announceAlert(alert) {
      if (!this.active || !this.enabled || !this.allowed || this.failed || this.suspended) return;
      if (alert.speech_enabled === false) return;
      if (alert.device_id !== this.sourceId) return;
      if (this.isHardwareSource() && this.tts?.supported) return;
      const level = String(alert.alert_state || alert.level || 'L0');
      if (!['L1', 'L2', 'L3'].includes(level)) return;
      const priority = Number.isFinite(Number(alert.speech_priority))
        ? Number(alert.speech_priority) : ({L1: 1, L2: 2, L3: 3}[level] || 1);
      const key = `${alert.device_id}:${alert.track_id ?? 'unknown'}:${level}:${alert.object || 'object'}:${alert.direction || 'front'}`;
      const now = this.now();
      const previous = this.alertLastSpoken.get(key);
      if (previous != null && now - previous < (level === 'L3' ? 1000 : this.gap)) return;
      const text = String(alert.voice_text || this.alertText(alert));
      if (!text) return;
      if (this.busy) {
        if (priority >= 3) {
          this.pendingAlert = null;
          this.latest = [];
          this.cancelCurrent();
        } else if (this.activeAlertPriority > priority) {
          return;
        } else if (!this.pendingAlert || priority > this.pendingAlert.priority) {
          this.pendingAlert = {alert, priority};
          return;
        } else return;
      }
      this.alertLastSpoken.set(key, now);
      if (this.alertLastSpoken.size > 256) {
        for (const [oldKey, at] of this.alertLastSpoken) {
          if (now - at > 60000) this.alertLastSpoken.delete(oldKey);
        }
      }
      this.activeAlertPriority = priority;
      this.say(text, [{key}]);
    }

    alertText(alert) {
      const side = directions[alert.direction] || '前方';
      const name = objectName(alert.object || alert.class || '障碍物');
      const level = String(alert.alert_state || alert.level || 'L1');
      if (level === 'L3') return `危险，${side}有${name}，请注意`;
      if (level === 'L2') return `注意，${side}有${name}，距离较近`;
      const depth = alert.relative_depth?.level;
      const depthText = {near: '较近', mid: '中等距离', far: '较远'}[depth];
      return `注意，${side}有${name}${depthText ? '，相对距离' + depthText : ''}`;
    }

    say(text, items = []) {
      if (this.suspended) return;
      if (this.tts?.supported) {
        const generation = this.generation;
        this.busy = true;
        this.utterance = {cancel: () => this.tts.cancel?.()};
        this.tts.speak(text, {
          onstart: () => {
            if (generation !== this.generation) return;
            const started = this.now();
            for (const item of items) this.lastSpoken.set(item.key, started);
            this.status('正在使用 Edge TTS 播报：' + text);
          },
          onend: () => {
            if (generation !== this.generation) return;
            this.busy = false;
            this.utterance = null;
            this.lastEnded = this.now();
            this.status('上次播报：' + text);
            const pending = this.pendingAlert;
            this.pendingAlert = null;
            this.activeAlertPriority = 0;
            if (pending) this.announceAlert(pending.alert);
            else this.drain();
          },
          onerror: () => {
            if (generation !== this.generation) return;
            this.busy = false;
            this.utterance = null;
            this.failed = true;
            this.status('Edge TTS 播报失败，请检查后端连接');
          },
        }).catch(() => {
          if (generation !== this.generation) return;
          this.busy = false;
          this.utterance = null;
          this.failed = true;
          this.status('Edge TTS 播报失败，请检查后端连接');
        });
        return;
      }
      if (!this.allowInjectedSynth) {
        return this.status(this.supported
          ? 'Edge TTS 音频服务不可用，请检查电脑后端'
          : '当前环境不支持语音播报');
      }
      if (!this.supported) return this.status('当前环境不支持语音播报');
      const generation = this.generation;
      const utterance = new this.Utterance(text);
      utterance.lang = 'zh-CN';
      utterance.rate = 0.88;
      utterance.volume = 1;
      const voices = this.synth.getVoices();
      const chinese = voices.filter(voice => /^zh[-_]/i.test(voice.lang));
      const voice = chinese.find(item => /^zh[-_]CN$/i.test(item.lang) && item.localService) ||
        chinese.find(item => /^zh[-_]CN$/i.test(item.lang)) || chinese[0];
      if (voice) utterance.voice = voice;
      this.busy = true;
      this.utterance = utterance;
      utterance.onstart = () => {
        if (generation !== this.generation) return;
        const started = this.now();
        for (const item of items) this.lastSpoken.set(item.key, started);
        this.status('正在播报：' + text);
      };
      utterance.onend = () => {
        if (generation !== this.generation) return;
        this.busy = false;
        this.utterance = null;
        this.lastEnded = this.now();
        this.status('上次播报：' + text);
        const pending = this.pendingAlert;
        this.pendingAlert = null;
        this.activeAlertPriority = 0;
        if (pending) this.announceAlert(pending.alert);
        else this.drain();
      };
      utterance.onerror = event => {
        if (generation !== this.generation) return;
        this.busy = false;
        this.utterance = null;
        this.failed = true;
        this.status(event.error === 'not-allowed' ? '浏览器尚未允许播报，请点击“测试播报”启用声音' : '语音播报失败，请检查系统声音并点击“测试播报”重试');
      };
      try {
        this.synth.resume();
        this.synth.speak(utterance);
      } catch (error) {
        utterance.onerror({error: error.name});
      }
    }
  }

  const api = {create: options => new Announcer(options), objectName};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingCameraSpeech = api;
})(typeof window === 'undefined' ? globalThis : window);
