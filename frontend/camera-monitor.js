(function (root) {
  'use strict';

  function drawDetections(canvas, media, data, selectedIndex = -1) {
    const box = media.getBoundingClientRect();
    const dpr = root.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(box.width * dpr));
    canvas.height = Math.max(1, Math.round(box.height * dpr));
    canvas.style.width = box.width + 'px';
    canvas.style.height = box.height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, box.width, box.height);
    const fw = data.frame_width || media.videoWidth || media.naturalWidth || 1;
    const fh = data.frame_height || media.videoHeight || media.naturalHeight || 1;
    const scale = Math.min(box.width / fw, box.height / fh);
    const ox = (box.width - fw * scale) / 2, oy = (box.height - fh * scale) / 2;
    ctx.lineWidth = 2;
    ctx.font = '13px "Microsoft YaHei",sans-serif';
    (data.objects || []).forEach((object, index) => {
      if (!Array.isArray(object.bbox) || object.bbox.length !== 4) return;
      const [x1, y1, x2, y2] = object.bbox;
      const x = ox + x1 * scale, y = oy + y1 * scale;
      ctx.strokeStyle = index === selectedIndex ? '#ffcf67' : object.direction === 'left' ? '#63f3d0' : object.direction === 'right' ? '#ffcf67' : '#58c7ff';
      ctx.lineWidth = index === selectedIndex ? 4 : 2;
      ctx.strokeRect(x, y, (x2 - x1) * scale, (y2 - y1) * scale);
      const label = `${root.QimingCameraSpeech?.objectName?.(object.class) || object.class} ${(object.confidence * 100).toFixed(0)}% · ${object.direction === 'front' ? '前方' : object.direction === 'left' ? '左侧' : '右侧'}`;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fillRect(x, Math.max(0, y - 22), ctx.measureText(label).width + 12, 22);
      ctx.fillStyle = '#061321';
      ctx.fillText(label, x + 6, Math.max(15, y - 6));
    });
  }

  function drawDepthHighlight(canvas, media, data, object) {
    const box = media.getBoundingClientRect();
    const dpr = root.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(box.width * dpr));
    canvas.height = Math.max(1, Math.round(box.height * dpr));
    canvas.style.width = box.width + 'px';
    canvas.style.height = box.height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, box.width, box.height);
    if (!object || !Array.isArray(object.bbox) || object.bbox.length !== 4) return;
    const fw = data.frame_width || media.naturalWidth || 1;
    const fh = data.frame_height || media.naturalHeight || 1;
    const scale = Math.min(box.width / fw, box.height / fh);
    const ox = (box.width - fw * scale) / 2, oy = (box.height - fh * scale) / 2;
    const [x1, y1, x2, y2] = object.bbox;
    const x = ox + x1 * scale, y = oy + y1 * scale;
    const width = (x2 - x1) * scale, height = (y2 - y1) * scale;
    ctx.fillStyle = 'rgba(255, 207, 103, .28)';
    ctx.fillRect(x, y, width, height);
    ctx.strokeStyle = '#ffcf67';
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, width, height);
  }

  class Monitor {
    constructor(options) {
      this.document = options.document || root.document;
      this.get = id => this.document.getElementById(id);
      this.fetch = options.fetch || root.fetch.bind(root);
      this.media = options.mediaDevices || root.navigator?.mediaDevices;
      this.storage = options.storage || root.localStorage;
      this.getToken = options.getToken || (() => root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token'));
      this.url = options.url || root.URL;
      this.interval = options.setInterval || root.setInterval.bind(root);
      this.clearInterval = options.clearInterval || root.clearInterval.bind(root);
      this.timeout = options.setTimeout || root.setTimeout.bind(root);
      this.clearTimeout = options.clearTimeout || root.clearTimeout.bind(root);
      this.speech = options.objectSpeech;
      this.sources = [];
      this.localDevices = [];
      this.defaultDeviceId = options.defaultDeviceId || 'esp32s3_cam_001';
      this.sourceId = 'local_camera';
      try { this.sourceId = this.storage.getItem('qh_camera_source') || this.sourceId; } catch (_) {}
      this.explicitSource = this.sourceId !== 'local_camera';
      try { this.explicitSource = Boolean(this.storage.getItem('qh_camera_source')); } catch (_) {}
      this.defaultChosen = this.explicitSource;
      this.running = this.hasFrame = false;
      this.generation = this.seq = 0;
      this.localStream = this.timer = this.request = this.upload = null;
      this.locationTimer = null;
      this.motorMonitorTimer = null;
      this.spatialAlerts = [];
      this.viewMode = 'original';
      this.spatialResult = null;
      this.selectedTargetKey = null;
      this.motorMonitorActive = false;
      this.motorMonitorDevice = null;
      this.frameBlob = this.frameUrl = this.frameSeq = this.lastDetection = null;
      this.localDeviceId = '';
      this.video = this.get('cameraVideo');
      this.image = this.get('cameraRemote');
      this.canvas = this.document.createElement('canvas');
      this.compareImage = this.get('compareOriginal');
      this.compareDepthImage = this.get('compareDepth');
      this.compareDetectionCanvas = this.get('compareDetectionOverlay');
      this.compareDepthCanvas = this.get('compareDepthOverlay');
      this.compareFrameReady = null;
      this.compareDetectionCanvas.onclick = event => this.selectCompareTargetAt(event);
      for (const [mode, id] of [['original', 'cameraModeOriginal'], ['detection', 'cameraModeDetection'], ['compare', 'cameraModeCompare']]) {
        const button = this.get(id);
        if (button) button.onclick = () => this.setViewMode(mode);
      }
      this.renderSources();
      this.setViewMode(this.viewMode);
      this.get('cameraStart').onclick = () => this.start();
      this.get('cameraStop').onclick = () => this.stop();
      this.get('cameraSelect').onchange = () => this.selectCamera(this.get('cameraSelect').value);
      const ocrStart = this.get('ocrCameraStart');
      const ocrStop = this.get('ocrCameraStop');
      if (ocrStart) ocrStart.onclick = () => this.start();
      if (ocrStop) ocrStop.onclick = () => this.stop();
      const ocrSource = this.get('ocrCameraSelect');
      if (ocrSource) ocrSource.onchange = () => this.selectCamera(ocrSource.value);
      this.fullscreenButton = this.get('cameraFullscreen');
      this.fullscreenChanged = () => {
        this.updateFullscreenButton();
        if (this.active()) this.renderDetection();
        if (this.spatialResult) this.renderCompare();
      };
      if (this.fullscreenButton) this.fullscreenButton.onclick = () => this.toggleFullscreen();
      this.document.addEventListener?.('fullscreenchange', this.fullscreenChanged);
      this.document.addEventListener?.('webkitfullscreenchange', this.fullscreenChanged);
      root.addEventListener?.('resize', this.fullscreenChanged);
      this.updateFullscreenButton();
    }

    isLocal() { return this.sourceId === 'local_camera'; }
    active() { return this.running && this.hasFrame; }
    targetKey(object, index) { return object?.track_id == null ? `index:${index}` : `track:${object.track_id}`; }
    objectLabel(object) { return root.QimingCameraSpeech?.objectName?.(object?.class) || object?.class || '目标'; }
    setViewMode(mode) {
      if (!['original', 'detection', 'compare'].includes(mode)) return;
      this.viewMode = mode;
      for (const [key, id] of [['original', 'cameraModeOriginal'], ['detection', 'cameraModeDetection'], ['compare', 'cameraModeCompare']]) {
        const button = this.get(id);
        if (!button) continue;
        button.classList.toggle('is-active', mode === key);
        button.setAttribute?.('aria-pressed', String(mode === key));
      }
      const stage = this.get('cameraStage');
      const compare = this.get('cameraCompare');
      const overlay = this.get('cameraOverlay');
      if (stage) stage.hidden = mode === 'compare';
      if (compare) compare.hidden = mode !== 'compare';
      if (overlay) overlay.hidden = mode !== 'detection';
      if (mode === 'detection') this.renderDetection();
      if (mode === 'compare') this.renderCompare();
    }
    fullscreenElement() { return this.document.fullscreenElement || this.document.webkitFullscreenElement; }
    updateFullscreenButton() {
      if (!this.fullscreenButton) return;
      const stage = this.get('cameraStage');
      const fullscreen = this.fullscreenElement() === stage;
      const supported = this.document.fullscreenEnabled !== false && Boolean(stage.requestFullscreen || stage.webkitRequestFullscreen);
      this.fullscreenButton.disabled = !supported || (!fullscreen && !this.active());
      this.fullscreenButton.textContent = fullscreen ? '退出全屏' : '全屏查看';
      this.fullscreenButton.title = !supported ? '当前浏览器不支持全屏查看' : fullscreen ? '退出全屏查看，可按 Esc 返回' : this.active() ? '全屏查看当前摄像头画面' : '启动摄像头并等待画面后可全屏查看';
      this.fullscreenButton.setAttribute?.('aria-pressed', String(fullscreen));
      this.fullscreenButton.setAttribute?.('aria-label', fullscreen ? '退出摄像头全屏' : '全屏查看摄像头画面');
    }
    async toggleFullscreen() {
      const stage = this.get('cameraStage');
      const fullscreen = this.fullscreenElement() === stage;
      const action = fullscreen
        ? this.document.exitFullscreen || this.document.webkitExitFullscreen
        : stage.requestFullscreen || stage.webkitRequestFullscreen;
      if (!action || (!fullscreen && !this.active())) {
        this.status(action ? '请先启动摄像头并等待画面，再使用全屏查看' : '当前浏览器不支持全屏查看');
        return;
      }
      if (this.fullscreenButton) this.fullscreenButton.disabled = true;
      try {
        await action.call(fullscreen ? this.document : stage);
      } catch (_) {
        this.status('全屏切换失败，请直接点击全屏按钮重试');
      } finally {
        this.updateFullscreenButton();
      }
    }
    label() {
      if (this.isLocal()) return '电脑本机摄像头';
      if (this.sourceId === this.defaultDeviceId) return 'ESP32 帽端摄像头';
      return this.sources.find(item => item.source_id === this.sourceId)?.label || '帽端摄像头 ' + this.sourceId;
    }
    headers() { const token = this.getToken(); return token ? {Authorization: 'Bearer ' + token} : {}; }
    async armMotorMonitor(deviceId, generation) {
      if (this.motorMonitorActive && this.motorMonitorDevice === deviceId) return;
      this.motorMonitorActive = true;
      this.motorMonitorDevice = deviceId;
      const update = active => this.fetch('/api/tablet/motor-monitor', {
        method: 'POST', headers: {...this.headers(), 'Content-Type': 'application/json'},
        body: JSON.stringify({active, device_id: deviceId})
      });
      try {
        const response = await update(true);
        if (!response.ok) throw new Error(response.status === 401 ? '登录已失效' : '后端未接受马达告警设置');
        if (generation !== this.generation || !this.running || this.sourceId !== deviceId) {
          this.disarmMotorMonitor();
          return;
        }
        this.motorMonitorTimer = this.interval(() => {
          if (!this.running || this.sourceId !== deviceId) return;
          update(true).then(response => {
            if (!response.ok) throw new Error('后端拒绝续租');
          }).catch(() => {
            if (!this.motorMonitorActive || this.motorMonitorDevice !== deviceId) return;
            this.disarmMotorMonitor();
            this.status('马达告警连接已失效，请重新启动帽端监测');
          });
        }, 5000);
        this.status('正在查看：' + this.label() + ' · 马达告警已启用');
      } catch (error) {
        this.disarmMotorMonitor();
        this.status('帽端画面可查看，但马达告警启用失败：' + error.message);
      }
    }
    disarmMotorMonitor() {
      if (this.motorMonitorTimer !== null) this.clearInterval(this.motorMonitorTimer);
      this.motorMonitorTimer = null;
      if (!this.motorMonitorActive) return;
      const deviceId = this.motorMonitorDevice;
      this.motorMonitorActive = false;
      this.motorMonitorDevice = null;
      this.fetch('/api/tablet/motor-monitor', {
        method: 'POST', headers: {...this.headers(), 'Content-Type': 'application/json'},
        body: JSON.stringify({active: false, device_id: deviceId}), keepalive: true
      }).catch(() => {});
    }
    event(name) { if (root.dispatchEvent) root.dispatchEvent(new root.Event(name)); }
    status(text) {
      const statuses = ['cameraStatus', 'ocrCameraStatus'].map(id => this.get(id)).filter(Boolean);
      for (const status of statuses) {
        status.textContent = text;
        status.classList.toggle('page-status-ready', /正在查看|已连接|已启用|画面可查看/.test(text));
        status.classList.toggle('page-status-error', /失败|不可用|权限被拒绝|连接已失效|黑帧/.test(text));
        status.classList.toggle('page-status-warm', /正在读取|等待|超时/.test(text));
      }
    }
    clearOverlay() { const canvas = this.get('cameraOverlay'); canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height); }

    option(value, label) {
      const option = this.document.createElement('option');
      option.value = value;
      option.textContent = label;
      return option;
    }

    renderSources() {
      const remote = this.sources.filter(item => item.source_id !== 'local_camera');
      // Keep the configured hat selectable even before it connects or the list loads.
      if (!remote.some(item => item.source_id === this.defaultDeviceId)) {
        remote.unshift({source_id: this.defaultDeviceId, online: false});
      }
      const selects = ['cameraSelect', 'ocrCameraSelect'].map(id => this.get(id)).filter(Boolean);
      for (const select of selects) {
        const choices = [this.option('local:', '默认电脑摄像头'),
          ...remote.map(item => this.option('remote:' + item.source_id,
            (item.source_id === this.defaultDeviceId ? 'ESP32 帽端摄像头' : item.label) +
            (item.online ? '（有画面）' : '（等待画面）'))),
          ...this.localDevices.map((device, index) => this.option('local:' + device.deviceId, device.label || `电脑摄像头 ${index + 1}`))];
        if (!this.isLocal() && !remote.some(item => item.source_id === this.sourceId)) {
          choices.push(this.option('remote:' + this.sourceId, this.label() + '（等待连接）'));
        }
        if (this.localDeviceId && !this.localDevices.some(device => device.deviceId === this.localDeviceId)) {
          choices.push(this.option('local:' + this.localDeviceId, '原选电脑摄像头（暂不可用）'));
        }
        select.replaceChildren(...choices);
        select.value = this.isLocal() ? 'local:' + this.localDeviceId : 'remote:' + this.sourceId;
      }
      const startLabel = this.isLocal() ? '启动电脑摄像头' : '启动帽端监测';
      for (const id of ['cameraStart', 'ocrCameraStart']) {
        const button = this.get(id);
        if (button) { button.textContent = startLabel; button.disabled = this.running; }
      }
      for (const id of ['cameraStop', 'ocrCameraStop']) {
        const button = this.get(id);
        if (button) button.disabled = !this.running;
      }
      this.get('cameraPlaceholderTitle').textContent = this.label();
      this.get('cameraPlaceholderHint').textContent = this.isLocal()
        ? '点击“启动电脑摄像头”后显示画面' : '启动监测后查看帽端画面并启用位置震动提醒';
    }

    async refreshSources() {
      if (this.sourceRequest) return;
      const controller = new AbortController();
      this.sourceRequest = controller;
      const timeout = this.timeout(() => controller.abort(), 5000);
      try {
        const response = await this.fetch('/api/tablet/cameras', {headers: this.headers(), cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error(response.status === 401 ? '登录状态已失效，请重新登录以读取帽端来源' : '来源列表读取失败，请检查后端');
        const sources = await response.json();
        if (controller.signal.aborted) return;
        this.sources = sources;
        if (!this.defaultChosen && !this.running) {
          const preferred = sources.find(item => item.kind === 'device' && item.online);
          if (preferred) this.sourceId = preferred.source_id;
          this.defaultChosen = true;
        }
        this.renderSources();
      } catch (error) {
        // A transient list error must never reset the user's selected source.
        if (!this.running) this.status(controller.signal.aborted ? '来源列表读取超时，保留当前选择' : error.message);
      } finally {
        this.clearTimeout(timeout);
        if (this.sourceRequest === controller) this.sourceRequest = null;
      }
    }

    async listLocalCameras() {
      if (!this.media?.enumerateDevices) return;
      try {
        this.localDevices = (await this.media.enumerateDevices()).filter(device => device.kind === 'videoinput');
        this.renderSources();
      } catch (_) {}
    }

    async selectCamera(value) {
      if (value.startsWith('remote:')) return this.selectSource(value.slice(7));
      if (!value.startsWith('local:')) return;
      const deviceId = value.slice(6);
      const changed = this.localDeviceId !== deviceId;
      this.localDeviceId = deviceId;
      if (!this.isLocal()) return this.selectSource('local_camera');
      this.renderSources();
      if (changed && this.running) return this.start();
    }

    async selectSource(sourceId) {
      if (sourceId === this.sourceId) return;
      const restart = this.running;
      this.stop(false);
      this.sourceId = sourceId;
      this.defaultChosen = this.explicitSource = true;
      try { this.storage.setItem('qh_camera_source', sourceId); } catch (_) {}
      this.event('qiming-camera-source-changed');
      this.renderSources();
      this.status('已选择：' + this.label());
      if (restart) await this.start();
    }

    async start() {
      this.stop(false);
      const generation = this.generation;
      this.defaultChosen = this.explicitSource = true;
      try { this.storage.setItem('qh_camera_source', this.sourceId); } catch (_) {}
      this.running = true;
      this.renderSources();
      this.get('cameraStage').classList.toggle('is-waiting', true);
      this.speech.setSource(this.sourceId);
      this.speech.start();
      this.renderSources();
      if (!this.isLocal()) {
        this.status('正在读取：' + this.label());
        this.pollLocation();
        this.locationTimer = this.interval(() => this.pollLocation(), 15000);
        await this.pollRemote();
        if (generation === this.generation && this.running) this.timer = this.interval(() => this.pollRemote(), 150);
        return;
      }
      try {
        if (!this.media?.getUserMedia) throw new Error('当前浏览器无法访问电脑摄像头，请使用本机地址和受支持的浏览器');
        const id = this.localDeviceId;
        const stream = await this.media.getUserMedia({video: {...(id ? {deviceId: {exact: id}} : {}), width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
        if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
        this.localStream = stream;
        this.video.onloadedmetadata = () => {
          if (generation !== this.generation) return;
          this.video.play().catch(() => {});
          this.hasFrame = true;
          this.stage(true, false);
          this.status(`电脑摄像头已连接 · ${this.video.videoWidth} × ${this.video.videoHeight}`);
        };
        this.video.srcObject = stream;
        this.get('cameraLive').textContent = '● 电脑摄像头已连接';
        await this.listLocalCameras();
        if (generation !== this.generation) return;
        this.minimumSeq = this.seq + 1;
        this.timer = this.interval(() => this.sendLocalFrame(), 700);
        this.event('qiming-camera-started');
      } catch (error) {
        if (generation !== this.generation) return;
        this.stop(false);
        this.status(error.name === 'NotAllowedError' ? '电脑摄像头权限被拒绝，请在地址栏允许访问' : '电脑摄像头启动失败：' + error.message);
      }
    }

    stage(active, remote) {
      const stage = this.get('cameraStage');
      stage.classList.toggle('active', active);
      stage.classList.toggle('remote', remote);
      stage.classList.toggle('is-waiting', this.running && !active);
      this.image.hidden = !active || !remote;
      this.updateFullscreenButton();
    }

    clearFrame() {
      this.hasFrame = false;
      this.frameBlob = this.frameSeq = null;
      this.lastDetection = null;
      this.spatialResult = null;
      this.selectedTargetKey = null;
      this.get('objects').replaceChildren();
      this.get('detection').textContent = '等待所选来源的检测结果';
      this.image.onload = this.image.onerror = null;
      this.image.removeAttribute('src');
      if (this.frameUrl) this.url.revokeObjectURL(this.frameUrl);
      this.frameUrl = null;
      for (const id of ['compareOriginal', 'compareDepth']) {
        const image = this.get(id);
        image.onload = image.onerror = null;
        image.removeAttribute('src');
        image.hidden = true;
      }
      this.compareFrameReady = null;
      this.renderCompareTargets();
      this.renderCompare();
      this.stage(false, !this.isLocal());
      this.clearOverlay();
    }

    stop(update = true) {
      this.generation += 1;
      this.running = false;
      this.disarmMotorMonitor();
      if (this.timer !== null) this.clearInterval(this.timer);
      this.timer = null;
      if (this.locationTimer !== null) this.clearInterval(this.locationTimer);
      this.locationTimer = null;
      if (this.request) this.request.abort();
      if (this.upload) this.upload.abort();
      this.request = this.upload = null;
      if (this.localStream) this.localStream.getTracks().forEach(track => track.stop());
      this.localStream = null;
      this.video.onloadedmetadata = null;
      this.video.srcObject = null;
      this.clearFrame();
      this.lastDetection = null;
      for (const id of ['objects', 'monitorObjects']) this.get(id).replaceChildren();
      this.get('monitorText').textContent = '';
      this.get('detection').textContent = '等待所选来源的检测结果';
      this.renderSources();
      this.get('cameraLive').textContent = '● 未连接';
      this.get('cameraFps').textContent = '等待画面';
      this.speech.stop();
      this.event('qiming-camera-stopped');
      if (update) this.status('已停止查看：' + this.label());
    }

    async pollRemote() {
      if (!this.running || this.isLocal() || this.request) return;
      const generation = this.generation, sourceId = this.sourceId;
      const controller = new AbortController();
      this.request = controller;
      const timeout = this.timeout(() => controller.abort(), 4000);
      try {
        const response = await this.fetch(`/api/tablet/cameras/${encodeURIComponent(sourceId)}/frame`, {headers: this.headers(), cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error(response.status === 410 ? '等待帽端上传新画面；不会切换到电脑摄像头' : response.status === 401 ? '登录已失效，请重新登录' : '帽端画面请求失败，请检查后端');
        const blob = await response.blob();
        if (generation !== this.generation || controller.signal.aborted) return;
        const frameSeq = response.headers.get('X-Frame-Seq');
        if (this.hasFrame && String(this.frameSeq) === frameSeq) return;
        const address = this.url.createObjectURL(blob);
        const previousUrl = this.frameUrl;
        this.frameUrl = address;
        if (previousUrl) this.url.revokeObjectURL(previousUrl);
        this.image.onload = () => {
          if (generation !== this.generation || this.frameUrl !== address) return;
          this.frameBlob = blob;
          this.frameSeq = frameSeq;
          this.hasFrame = true;
          this.stage(true, true);
          this.clearOverlay();
          this.status('正在查看：' + this.label());
          this.get('cameraLive').textContent = '● 帽端上传画面';
          this.get('cameraFps').textContent = `${this.image.naturalWidth} × ${this.image.naturalHeight} · 图像序号 ${frameSeq}`;
          this.renderDetection();
          this.armMotorMonitor(sourceId, generation);
        };
        this.image.onerror = () => {
          if (generation !== this.generation) return;
          this.disarmMotorMonitor();
          this.clearFrame();
          this.status('帽端画面解码失败，等待下一帧');
        };
        this.image.src = address;
      } catch (error) {
        if (generation !== this.generation) return;
        this.disarmMotorMonitor();
        this.clearFrame();
        this.speech.reset();
        this.get('cameraLive').textContent = '● 等待帽端画面';
        this.get('cameraFps').textContent = '尚无新鲜画面';
        this.status(controller.signal.aborted ? '帽端画面请求超时，保留当前来源并等待重试' : error.message);
      } finally {
        this.clearTimeout(timeout);
        if (this.request === controller) this.request = null;
      }
    }

    async pollLocation() {
      const status = this.get('wifiLocationStatus');
      const detail = this.get('wifiLocationDetail');
      const generation = this.generation, sourceId = this.sourceId;
      if (this.isLocal()) {
        status.textContent = '当前选择的是电脑摄像头';
        detail.textContent = 'Wi-Fi 定位由 ESP32 帽端扫描附近热点提供';
        return;
      }
      try {
        const response = await this.fetch(`/api/tablet/location?device_id=${encodeURIComponent(this.sourceId)}`, {
          headers: this.headers(), cache: 'no-store'
        });
        if (!response.ok) throw new Error(response.status === 401 ? '登录已失效，请重新登录' : '位置状态读取失败');
        const location = await response.json();
        if (generation !== this.generation || sourceId !== this.sourceId || !this.running) return;
        if (location.available) {
          const updated = location.updated_at ? new Date(location.updated_at).toLocaleString() : '时间未知';
          status.textContent = `${Number(location.latitude).toFixed(5)}, ${Number(location.longitude).toFixed(5)} · 估算误差约 ±${Math.round(location.accuracy_m)} 米`;
          detail.textContent = `使用 ${location.access_points_used} 个 Wi-Fi 热点估算 · 更新于 ${updated} · ${location.provider || 'Wi-Fi 定位'}`;
        } else {
          const messages = {
            awaiting_scan: '等待 ESP32 上传首次扫描（设备联网后约 30 秒）',
            locating: '已收到热点信息，正在估算位置…',
            insufficient_access_points: '可用热点不足 2 个，暂时无法估算位置',
            no_match: '定位数据库未匹配到附近热点',
            provider_timeout: '定位服务请求超时，稍后自动重试',
            provider_unavailable: '定位服务暂不可用，稍后自动重试',
            invalid_response: '定位服务返回无效数据'
          };
          status.textContent = messages[location.status] || location.detail || '位置暂不可用';
          detail.textContent = location.updated_at ? `最近扫描：${new Date(location.updated_at).toLocaleString()}` : '请确认 ESP32 已连接 Wi-Fi 并保持运行';
        }
      } catch (error) {
        if (generation !== this.generation || sourceId !== this.sourceId || !this.running) return;
        status.textContent = error.message || '位置状态读取失败';
        detail.textContent = '连接恢复后会自动重试';
      }
    }

    async sendLocalFrame() {
      if (!this.running || !this.isLocal() || !this.localStream || this.upload || this.video.readyState < 2) return;
      const generation = this.generation, controller = new AbortController();
      this.upload = controller;
      try {
        const w = this.video.videoWidth, h = this.video.videoHeight;
        if (!w || !h) return;
        this.canvas.width = Math.min(w, 960);
        this.canvas.height = Math.round(h * this.canvas.width / w);
        const context = this.canvas.getContext('2d', {alpha: false});
        context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
        const sample = context.getImageData(0, 0, 1, 1).data;
        if (sample[0] + sample[1] + sample[2] < 8) this.status('电脑摄像头已连接，但当前画面为黑帧；请检查镜头遮挡、系统隐私权限或其他占用程序。');
        const blob = await new Promise(resolve => this.canvas.toBlob(resolve, 'image/jpeg', 0.72));
        if (!blob || generation !== this.generation) return;
        const response = await this.fetch(`/api/local-camera/frame?seq=${++this.seq}`, {method: 'POST', headers: {'Content-Type': 'image/jpeg'}, body: blob, signal: controller.signal});
        if (!response.ok) throw new Error('后端帧上传失败');
        if (generation === this.generation) this.get('cameraFps').textContent = `${w} × ${h} · 上传序号 ${this.seq}`;
      } catch (error) {
        if (generation === this.generation && !controller.signal.aborted) this.status('电脑画面上传失败：' + error.message);
      } finally {
        if (this.upload === controller) this.upload = null;
      }
    }

    handleDetection(result) {
      if (!this.running || result.device_id !== this.sourceId || (this.isLocal() && result.image_seq < this.minimumSeq)) return;
      this.lastDetection = result;
      this.renderDetection();
      this.renderPerformance(result.performance);
      if (this.hasFrame) this.speech.announce(result);
    }

    renderPerformance(performance) {
      if (!performance) return;
      const status = this.get('performanceStatus');
      const meta = this.get('performanceMeta');
      if (status) {
        const yolo = Number(performance.yolo_fps || 0).toFixed(1);
        const depth = Number(performance.depth_fps || 0).toFixed(1);
        status.textContent = `YOLO ${yolo} FPS · 深度 ${depth} FPS`;
      }
      if (meta) {
        const gpu = performance.gpu_name || 'CPU / GPU 未启用';
        const memory = performance.gpu_memory_used_mb == null ? '显存不可用' : `${performance.gpu_memory_used_mb}/${performance.gpu_memory_total_mb || '—'} MB`;
        const latency = performance.frame_latency_ms == null ? '帧延迟 —' : `帧延迟 ${performance.frame_latency_ms.toFixed(0)} ms`;
        meta.textContent = `${gpu} · ${memory} · 风险 ${performance.risk_ms} ms · ${latency}`;
      }
    }

    handleSpatial(result) {
      if (!this.running || result.device_id !== this.sourceId) return;
      if (this.isLocal() && result.image_seq < this.minimumSeq) return;
      if (this.spatialResult && Number(result.image_seq) < Number(this.spatialResult.image_seq)) return;
      const sameFrame = result.depth_status === 'ready' && result.image_seq != null && result.depth_frame_id != null &&
        String(result.image_seq) === String(result.depth_frame_id);
      const previousSelection = this.selectedTargetKey;
      this.spatialResult = sameFrame && result.raw_frame_preview && result.depth_preview ? result : null;
      const compareImage = this.get('compareOriginal');
      const compareDepth = this.get('compareDepth');
      if (this.spatialResult) {
        const sequence = String(result.image_seq);
        this.compareFrameReady = {sequence, original: false, depth: false};
        compareImage.onload = () => this.compareImageLoaded(sequence, 'original');
        compareDepth.onload = () => this.compareImageLoaded(sequence, 'depth');
        compareImage.onerror = compareDepth.onerror = () => {
          if (this.compareFrameReady?.sequence !== sequence) return;
          this.compareFrameReady = null;
          compareImage.hidden = compareDepth.hidden = true;
          this.renderCompare();
        };
        compareImage.src = `data:image/jpeg;base64,${result.raw_frame_preview}`;
        compareDepth.src = `data:image/jpeg;base64,${result.depth_preview}`;
        compareImage.hidden = compareDepth.hidden = true;
        this.selectedTargetKey = previousSelection?.startsWith('track:') && (result.objects || []).some((object, index) =>
          this.targetKey(object, index) === previousSelection) ? previousSelection : null;
      } else {
        for (const image of [compareImage, compareDepth]) {
          image.onload = image.onerror = null;
          image.removeAttribute('src');
          image.hidden = true;
        }
        this.compareFrameReady = null;
        this.selectedTargetKey = null;
      }
      const depth = this.get('depthStatus');
      const meta = this.get('depthMeta');
      const preview = this.get('depthPreview');
      if (depth) depth.textContent = result.depth_status === 'ready' ? '已获取同帧深度' : '深度结果不可用';
      if (meta) meta.textContent = result.depth_status === 'ready'
        ? `Depth Anything V2 Small · 相对深度 · 帧 ${result.depth_frame_id}`
        : 'Depth Anything V2 Small · 相对深度';
      if (preview) {
        if (result.depth_preview) {
          preview.src = `data:image/jpeg;base64,${result.depth_preview}`;
          preview.hidden = false;
        } else preview.hidden = true;
      }
      this.renderPerformance(result.performance);
      const objects = result.objects || [];
      const highest = objects.map(item => item.risk).filter(Boolean).sort((a, b) => (b.score || 0) - (a.score || 0))[0];
      const risk = this.get('riskStatus');
      const reasons = this.get('riskReasons');
      if (risk) risk.textContent = highest?.level ? `${highest.level} · ${highest.score == null ? '等待质量足够数据' : highest.score.toFixed(2)}` : 'L0 · 无高风险目标';
      if (reasons) reasons.textContent = highest?.reasons?.join('、') || '风险评分仅用于系统内部决策';
      const list = this.get('spatialObjects');
      if (list) {
        list.replaceChildren(...(objects.length ? objects.map(item => {
          const node = this.document.createElement('span');
          node.className = 'spatial-object';
          const depthLevel = item.depth?.level || '未测';
          const approach = item.approach?.approaching ? '接近' : '稳定';
          node.textContent = `#${item.track_id ?? '—'} ${item.class} · ${item.direction || 'front'} · ${depthLevel} · ${approach}`;
          return node;
        }) : [Object.assign(this.document.createElement('span'), {className: 'muted', textContent: '当前帧没有可用空间对象'})]));
      }
      this.renderCompareTargets();
      this.renderCompare();
    }

    selectSpatialTarget(index) {
      const objects = this.spatialResult?.objects || [];
      if (!Number.isInteger(index) || index < 0 || index >= objects.length) return;
      this.selectedTargetKey = this.targetKey(objects[index], index);
      this.renderCompareTargets();
      this.renderCompare();
    }

    compareImageLoaded(sequence, pane) {
      if (!this.compareFrameReady || this.compareFrameReady.sequence !== sequence) return;
      this.compareFrameReady[pane] = true;
      if (this.compareFrameReady.original && this.compareFrameReady.depth) {
        this.compareImage.hidden = this.compareDepthImage.hidden = false;
        this.renderCompare();
      }
    }

    selectCompareTargetAt(event) {
      const data = this.spatialResult;
      if (!data || !this.compareFrameReady?.original || !this.compareFrameReady?.depth) return;
      const objects = data.objects || [];
      const rect = this.compareDetectionCanvas.getBoundingClientRect();
      const width = Number(data.frame_width) || this.compareImage.naturalWidth || 1;
      const height = Number(data.frame_height) || this.compareImage.naturalHeight || 1;
      const scale = Math.min(rect.width / width, rect.height / height);
      const x = (event.clientX - rect.left - (rect.width - width * scale) / 2) / scale;
      const y = (event.clientY - rect.top - (rect.height - height * scale) / 2) / scale;
      const hits = objects.map((object, index) => ({object, index}))
        .filter(({object}) => Array.isArray(object.bbox) && object.bbox.length === 4 &&
          x >= object.bbox[0] && x <= object.bbox[2] && y >= object.bbox[1] && y <= object.bbox[3])
        .sort((a, b) => {
          const area = item => (item.object.bbox[2] - item.object.bbox[0]) * (item.object.bbox[3] - item.object.bbox[1]);
          return area(a) - area(b);
        });
      if (hits.length) this.selectSpatialTarget(hits[0].index);
    }

    renderCompareTargets() {
      const container = this.get('compareTargets');
      if (!container) return;
      const objects = this.spatialResult?.objects || [];
      if (!objects.length) {
        container.replaceChildren(Object.assign(this.document.createElement('span'), {
          className: 'muted', textContent: '同帧检测结果中没有可选目标'
        }));
        return;
      }
      container.replaceChildren(...objects.map((object, index) => {
        const button = this.document.createElement('button');
        button.type = 'button';
        button.className = 'compare-target-option';
        button.textContent = `${this.objectLabel(object)}${object.track_id == null ? ` ${index + 1}` : ` #${object.track_id}`}`;
        button.setAttribute('aria-pressed', String(this.targetKey(object, index) === this.selectedTargetKey));
        button.onclick = () => this.selectSpatialTarget(index);
        return button;
      }));
    }

    renderCompare() {
      const data = this.spatialResult;
      const hasFrame = Boolean(data && String(data.image_seq) === String(data.depth_frame_id) &&
        data.raw_frame_preview && data.depth_preview && this.compareFrameReady?.sequence === String(data.image_seq) &&
        this.compareFrameReady.original && this.compareFrameReady.depth);
      const originalPlaceholder = this.get('compareOriginalPlaceholder');
      const depthPlaceholder = this.get('compareDepthPlaceholder');
      const frameMeta = this.get('compareFrameMeta');
      if (originalPlaceholder) originalPlaceholder.hidden = hasFrame;
      if (depthPlaceholder) depthPlaceholder.hidden = hasFrame;
      if (frameMeta) frameMeta.textContent = hasFrame
        ? `检测帧 ${data.image_seq} · 深度帧 ${data.depth_frame_id} · ${data.frame_width || '—'} × ${data.frame_height || '—'}`
        : '等待相同帧的检测与深度结果';
      const selectedIndex = hasFrame ? (data.objects || []).findIndex((object, index) =>
        this.targetKey(object, index) === this.selectedTargetKey) : -1;
      if (hasFrame && !this.compareImage.hidden && this.compareImage.naturalWidth) {
        drawDetections(this.compareDetectionCanvas, this.compareImage, data, selectedIndex);
      } else if (this.compareDetectionCanvas) {
        const ctx = this.compareDetectionCanvas.getContext('2d');
        ctx.clearRect(0, 0, this.compareDetectionCanvas.width, this.compareDetectionCanvas.height);
      }
      const selected = selectedIndex >= 0 ? data.objects[selectedIndex] : null;
      if (hasFrame && selected && !this.compareDepthImage.hidden && this.compareDepthImage.naturalWidth) {
        drawDepthHighlight(this.compareDepthCanvas, this.compareDepthImage, data, selected);
      } else if (this.compareDepthCanvas) {
        const ctx = this.compareDepthCanvas.getContext('2d');
        ctx.clearRect(0, 0, this.compareDepthCanvas.width, this.compareDepthCanvas.height);
      }
      this.renderSelectedTarget(selected);
    }

    renderSelectedTarget(object) {
      const name = this.get('compareTargetName');
      const direction = this.get('compareTargetDirection');
      const depth = this.get('compareTargetDepth');
      const risk = this.get('compareTargetRisk');
      if (!object) {
        name.textContent = '尚未选择目标';
        direction.textContent = (this.spatialResult?.objects || []).length ? '点击左侧检测框，查看方位' : '当前帧没有可选择的检测目标';
        depth.textContent = '相对深度将在目标深度分析完成后显示';
        risk.textContent = '当前风险等级等待评估';
        return;
      }
      const directionNames = {left: '左侧', front: '正前方', right: '右侧'};
      const depthNames = {near: '较近', mid: '中等', far: '较远'};
      const region = object.depth || {};
      const percentile = region.depth_percentile != null && Number.isFinite(Number(region.depth_percentile))
        ? ` · 相对深度 ${Math.round(Number(region.depth_percentile) * 100)}%` : '';
      const riskResult = object.risk || {};
      name.textContent = `${this.objectLabel(object)}${object.track_id == null ? '' : ` · 跟踪 #${object.track_id}`}`;
      direction.textContent = `方位：${directionNames[object.direction] || '正前方'}`;
      depth.textContent = `相对深度：${depthNames[region.level] || '暂不可用'}${percentile}`;
      risk.textContent = `风险等级：${/^L[0-3]$/.test(riskResult.level || '') ? riskResult.level : '尚未评估'}`;
    }

    handleAlert(result) {
      if (result.device_id !== this.sourceId) return;
      // Risk events are the depth-aware speech channel.  They are delivered
      // separately from frame updates so L3 can interrupt a generic phrase.
      this.speech?.announceAlert?.(result);
      this.spatialAlerts.unshift(result);
      this.spatialAlerts = this.spatialAlerts.slice(0, 5);
      const container = this.get('spatialAlertEvents');
      if (!container) return;
      container.replaceChildren(...this.spatialAlerts.map(event => {
        const row = this.document.createElement('div');
        row.className = 'spatial-alert-row spatial-alert-' + String(event.alert_state || 'L0').toLowerCase();
        const label = this.document.createElement('strong');
        const priority = event.speech_priority ? ` · 语音优先级 ${event.speech_priority}` : '';
        label.textContent = `${event.alert_state || '预警'} · ${event.object || '障碍物'}${priority}`;
        const detail = this.document.createElement('span');
        const depth = event.relative_depth?.level ? ` · 相对深度 ${event.relative_depth.level}` : '';
        const frames = event.frame_id != null && event.depth_frame_id != null
          ? ` · 检测帧 ${event.frame_id} / 深度帧 ${event.depth_frame_id}` : '';
        detail.textContent = `${event.approach_reason || '风险状态变化'}${depth}${frames} · ${event.delivery_status || '发送状态未知'}${event.voice_status ? ' · 语音 ' + event.voice_status : ''}`;
        row.append(label, detail);
        return row;
      }));
    }

    renderDetection() {
      const result = this.lastDetection;
      if (!this.running || !this.hasFrame || !result) return;
      this.get('detection').textContent = `${this.label()} · 模型：${result.model_status} · 检测序号：${result.image_seq}`;
      const pills = (result.objects || []).map(object => {
        const node = this.document.createElement('span');
        node.className = 'pill';
        node.textContent = `${object.class} / ${object.direction} / ${(object.confidence * 100).toFixed(1)}%`;
        return node;
      });
      this.get('objects').replaceChildren(...pills);
      if (result.model_status === 'error' || result.model_status === 'unavailable') this.status('检测服务不可用：' + (result.error || '请检查 YOLO 权重和后端状态'));
      if (this.isLocal() || String(result.image_seq) === String(this.frameSeq)) drawDetections(this.get('cameraOverlay'), this.isLocal() ? this.video : this.image, result);
      else this.clearOverlay();
    }

    async captureFrame() {
      if (!this.active()) throw new Error('请先启动所选来源，并等待画面出现');
      if (!this.isLocal()) return this.frameBlob;
      const canvas = this.document.createElement('canvas');
      canvas.width = Math.min(this.video.videoWidth, 1600);
      canvas.height = Math.round(this.video.videoHeight * canvas.width / this.video.videoWidth);
      canvas.getContext('2d').drawImage(this.video, 0, 0, canvas.width, canvas.height);
      return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92));
    }

    drawPreview(canvas) {
      if (!this.active()) return false;
      const media = this.isLocal() ? this.video : this.image;
      const sourceWidth = this.isLocal() ? media.videoWidth : media.naturalWidth;
      const sourceHeight = this.isLocal() ? media.videoHeight : media.naturalHeight;
      if (!sourceWidth || !sourceHeight) return false;
      const scale = Math.min(1, 1280 / sourceWidth);
      canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      canvas.getContext('2d').drawImage(media, 0, 0, canvas.width, canvas.height);
      return true;
    }

    dispose() {
      this.stop(false);
      if (this.sourceRequest) this.sourceRequest.abort();
      this.clearInterval(this.sourceTimer);
      this.document.removeEventListener?.('fullscreenchange', this.fullscreenChanged);
      this.document.removeEventListener?.('webkitfullscreenchange', this.fullscreenChanged);
      root.removeEventListener?.('resize', this.fullscreenChanged);
    }
  }

  function mount(options) {
    const monitor = new Monitor(options);
    monitor.refreshSources();
    monitor.listLocalCameras();
    monitor.sourceTimer = monitor.interval(() => monitor.refreshSources(), 3000);
    root.addEventListener('pagehide', () => monitor.dispose());
    return monitor;
  }

  const api = {Monitor, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingCameraMonitor = api;
})(typeof window === 'undefined' ? globalThis : window);
