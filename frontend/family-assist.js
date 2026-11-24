(function (root) {
  'use strict';

  function deliveryLabel(delivery) {
    const labels = {
      queued: '已加入帽端播报队列，尚未确认实际出声',
      synthesizing: '正在合成语音，完成后自动发送到帽端',
      synthesis_unavailable: '语音合成服务不可用，消息已保存但未播报',
      text_too_long: '消息过长，无法作为一条语音播报',
      output_unconfigured: '帽端 I2S 输出尚未配置',
      offline: '消息已保存，帽端离线，本次未播报',
      unsupported: '消息已保存，当前帽端尚不支持合成播报',
      speech_disabled: '消息已保存，请先开启帽端语音播报',
      queue_full: '消息已保存，帽端播报繁忙，请稍后重发'
    };
    return delivery?.detail || labels[delivery?.status] || '消息已保存，未收到播报回执';
  }

  function uniqueDevices(bindings) {
    return [...new Map(bindings.map(binding => [binding.device_id, binding])).values()];
  }

  function isOwnerDevice(binding) {
    return binding.is_owner_device === true || (
      typeof binding.owner_username === 'string' &&
      typeof binding.family_username === 'string' &&
      binding.owner_username.toLowerCase() === binding.family_username.toLowerCase()
    );
  }

  function create(options = {}) {
    const fetch = options.fetch || root.fetch.bind(root);
    const schedule = options.setTimeout || root.setTimeout.bind(root);
    const unschedule = options.clearTimeout || root.clearTimeout.bind(root);
    const urls = options.urls || root.URL;
    const token = options.token || (() => root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token'));
    const changed = options.onChange || (() => {});
    const SpeechRecognition = options.SpeechRecognition || root.SpeechRecognition || root.webkitSpeechRecognition;
    const state = {
      bindings: [], selectedDevice: '', overview: null, messages: [], frameUrl: '',
      frameStatus: '请选择已授权的帽端设备', error: '', delivery: null,
      loading: false, sending: false, paused: false, listening: false,
      transcript: '', voiceDraft: false,
      speechSupported: Boolean(SpeechRecognition) && root.isSecureContext !== false,
      speechUnavailableReason: root.isSecureContext === false
        ? '当前地址未启用安全连接，语音录入请使用 Android 客户端，或输入文字发送。'
        : '当前浏览器未提供语音识别，请使用 Android 客户端，或输入文字发送。'
    };
    let active = false;
    let generation = 0;
    let metaTimer;
    let frameTimer;
    let recognitionTimer;
    let metaRequest;
    let frameRequest;
    let recognition;
    let mutation = false;

    const emit = () => changed(state);
    const current = epoch => active && epoch === generation;

    async function request(path, init = {}) {
      const session = token();
      const controller = init.signal ? null : new AbortController();
      const timeout = controller ? schedule(() => controller.abort(), 15000) : null;
      let response;
      try {
        response = await fetch(path, {
          ...init,
          cache: 'no-store',
          signal: init.signal || controller.signal,
          headers: {...(session ? {Authorization: 'Bearer ' + session} : {}), ...init.headers}
        });
      } catch (error) {
        if (!controller) throw error;
        if (error.name === 'AbortError') throw new Error('请求超时，结果暂未确认，请刷新消息记录后再决定是否重发');
        throw new Error('无法连接电脑后端，请检查网络后重试');
      } finally {
        if (timeout !== null) unschedule(timeout);
      }
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const error = new Error(body.detail || `请求失败（${response.status}）`);
        error.status = response.status;
        throw error;
      }
      return response;
    }

    function clearFrame(message) {
      if (state.frameUrl) urls.revokeObjectURL(state.frameUrl);
      state.frameUrl = '';
      if (message) state.frameStatus = message;
    }

    function stopRecognition() {
      unschedule(recognitionTimer);
      const previous = recognition;
      recognition = null;
      state.listening = false;
      if (previous) {
        previous.onresult = previous.onerror = previous.onend = null;
        try {previous.abort();} catch (_) { /* Some engines have already ended. */ }
      }
    }

    function stopWork() {
      generation++;
      unschedule(metaTimer);
      unschedule(frameTimer);
      metaRequest?.abort();
      frameRequest?.abort();
      metaRequest = frameRequest = null;
      stopRecognition();
      clearFrame();
      state.loading = false;
    }

    async function refresh() {
      if (!active || metaRequest) return;
      unschedule(metaTimer);
      const epoch = generation;
      const controller = new AbortController();
      metaRequest = controller;
      const timeout = schedule(() => controller.abort(), 12000);
      state.loading = true;
      emit();
      try {
        const response = await request('/api/family/bindings', {signal: controller.signal});
        const bindings = await response.json();
        if (!Array.isArray(bindings)) throw new Error('家属绑定数据格式不正确');
        if (!current(epoch)) return;
        state.bindings = bindings;
        const exists = bindings.some(item => item.device_id === state.selectedDevice);
        const selected = exists ? state.selectedDevice : bindings[0]?.device_id || '';
        if (selected !== state.selectedDevice) {
          unschedule(frameTimer);
          frameRequest?.abort();
          frameRequest = null;
          stopRecognition();
          state.selectedDevice = selected;
          state.overview = null;
          state.messages = [];
          state.delivery = null;
          state.transcript = '';
          state.voiceDraft = false;
          clearFrame('等待帽端画面');
        }
        if (!selected) {
          state.overview = null;
          state.messages = [];
          clearFrame('授权家属账号后，可在这里查看帽端画面');
          state.error = '';
          return;
        }
        const query = '?device_id=' + encodeURIComponent(selected);
        const [overviewResponse, messagesResponse] = await Promise.all([
          request('/api/family/overview' + query, {signal: controller.signal}),
          request('/api/family/messages' + query, {signal: controller.signal})
        ]);
        const [overview, messages] = await Promise.all([overviewResponse.json(), messagesResponse.json()]);
        if (!current(epoch) || state.selectedDevice !== selected) return;
        state.overview = overview;
        state.messages = Array.isArray(messages) ? messages : [];
        state.error = '';
        if (overview.permissions?.camera === false) clearFrame('当前家属账号没有画面查看权限');
        if (!frameRequest && !state.paused) {
          unschedule(frameTimer);
          frameTimer = schedule(pollFrame, 0);
        }
      } catch (error) {
        if (!current(epoch)) return;
        state.error = error.status === 401 ? '登录已过期，请重新登录' : error.name === 'AbortError' ? '状态读取超时，请检查连接后刷新' : error.message;
        state.overview = null;
        if (error.status === 401 || error.status === 403) state.messages = [];
        clearFrame('无法读取画面，请检查登录、授权和连接');
      } finally {
        unschedule(timeout);
        if (metaRequest === controller) metaRequest = null;
        if (current(epoch)) {
          state.loading = false;
          emit();
          metaTimer = schedule(refresh, 8000);
        }
      }
    }

    async function pollFrame() {
      if (!active || state.paused || frameRequest || !state.selectedDevice || state.overview?.permissions?.camera !== true) return;
      const epoch = generation;
      const device = state.selectedDevice;
      const controller = new AbortController();
      frameRequest = controller;
      const timeout = schedule(() => controller.abort(), 5000);
      try {
        const response = await request('/api/family/cameras/' + encodeURIComponent(device) + '/frame', {signal: controller.signal});
        const blob = await response.blob();
        if (!current(epoch) || state.selectedDevice !== device || state.paused) return;
        const nextUrl = urls.createObjectURL(blob);
        clearFrame();
        state.frameUrl = nextUrl;
        state.frameStatus = '画面持续更新中';
      } catch (error) {
        if (!current(epoch) || state.selectedDevice !== device || state.paused) return;
        const messages = {401: '登录已过期，请重新登录', 403: '画面授权已撤销', 410: '帽端暂无新画面，请开启帽端摄像头'};
        clearFrame(messages[error.status] || '画面连接中断，正在重试');
        if (error.status === 401 || error.status === 403) {
          state.overview = null;
          state.messages = [];
        }
      } finally {
        unschedule(timeout);
        if (frameRequest === controller) frameRequest = null;
        if (current(epoch) && state.selectedDevice === device) {
          emit();
          if (!state.paused && state.overview?.permissions?.camera === true) frameTimer = schedule(pollFrame, 700);
        }
      }
    }

    function setActive(value) {
      if (active === Boolean(value)) return;
      active = Boolean(value);
      stopWork();
      state.frameStatus = active ? '等待帽端画面' : '已暂停查看';
      emit();
      if (active) refresh();
    }

    function selectDevice(device) {
      if (device === state.selectedDevice) return;
      stopWork();
      state.selectedDevice = device;
      state.overview = null;
      state.messages = [];
      state.delivery = null;
      state.transcript = '';
      state.voiceDraft = false;
      emit();
      refresh();
    }

    function setPaused(value) {
      state.paused = Boolean(value);
      unschedule(frameTimer);
      frameRequest?.abort();
      clearFrame(state.paused ? '画面已暂停' : '正在恢复画面');
      emit();
      if (!state.paused && !frameRequest) pollFrame();
    }

    function setDraft(value) {
      state.transcript = value.slice(0, 500);
      state.delivery = null;
      emit();
    }

    function startRecognition() {
      if (!active || !state.speechSupported || state.listening || state.sending || state.overview?.permissions?.messages !== true) return;
      const epoch = generation;
      const device = state.selectedDevice;
      const instance = new SpeechRecognition();
      recognition = instance;
      instance.lang = 'zh-CN';
      instance.continuous = false;
      instance.interimResults = false;
      state.listening = true;
      state.error = '';
      state.delivery = null;
      instance.onresult = event => {
        if (!current(epoch) || recognition !== instance || device !== state.selectedDevice) return;
        const text = Array.from(event.results).filter(result => result.isFinal !== false).map(result => result[0]?.transcript || '').join('').trim();
        if (text) {
          state.transcript = text.slice(0, 500);
          state.voiceDraft = true;
        }
        emit();
      };
      instance.onerror = event => {
        if (recognition !== instance) return;
        const errors = {'not-allowed': '麦克风权限被拒绝，请在浏览器中允许麦克风访问', 'audio-capture': '未找到可用麦克风，请检查设备', 'network': '语音识别服务连接失败，请检查网络或输入文字', 'no-speech': '没有听到声音，请重试'};
        state.error = errors[event.error] || '语音识别未完成，请重试或输入文字';
        stopRecognition();
        emit();
      };
      instance.onend = () => {
        if (recognition !== instance) return;
        recognition = null;
        state.listening = false;
        unschedule(recognitionTimer);
        emit();
      };
      try {
        instance.start();
        recognitionTimer = schedule(() => {
          if (recognition !== instance) return;
          stopRecognition();
          state.error = '语音识别超时，请重新录入或输入文字';
          emit();
        }, 30000);
      } catch (_) {
        stopRecognition();
        state.error = '当前浏览器无法启动语音识别，请输入文字';
      }
      emit();
    }

    async function sendMessage() {
      const body = state.transcript.trim();
      if (!active || state.sending || state.listening || !body || state.overview?.permissions?.messages !== true) return;
      const device = state.selectedDevice;
      const epoch = generation;
      const type = state.voiceDraft ? 'voice_transcript' : 'text';
      state.sending = true;
      state.error = '';
      emit();
      try {
        const response = await request('/api/family/messages', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device_id: device, body, message_type: type})});
        const message = await response.json();
        if (!current(epoch) || device !== state.selectedDevice) return;
        state.delivery = message.delivery || {};
        state.messages.push(message);
        if (state.transcript.trim() === body) {
          state.transcript = '';
          state.voiceDraft = false;
        }
      } catch (error) {
        if (current(epoch) && device === state.selectedDevice) state.error = error.message;
      } finally {
        state.sending = false;
        emit();
      }
    }

    async function addBinding(device, username) {
      if (mutation) return;
      mutation = true;
      try {
        await request('/api/family/bindings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device_id: device.trim(), family_username: username.trim()})});
        stopWork();
        await refresh();
      } finally {
        mutation = false;
      }
    }

    async function addDevice(device) {
      if (mutation) return;
      mutation = true;
      try {
        await request('/api/family/devices', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device_id: device.trim()})});
        stopWork();
        await refresh();
      } finally {
        mutation = false;
      }
    }

    async function revokeBinding(id) {
      if (mutation) return;
      mutation = true;
      try {
        await request('/api/family/bindings/' + encodeURIComponent(id), {method: 'DELETE'});
        stopWork();
        state.overview = null;
        state.messages = [];
        state.delivery = null;
        emit();
        await refresh();
      } finally {
        mutation = false;
      }
    }

    return {state, setActive, refresh, selectDevice, setPaused, setDraft, startRecognition, stopRecognition() {stopRecognition(); emit();}, sendMessage, addDevice, addBinding, revokeBinding, dispose() {active = false; stopWork();}};
  }

  function mount(options = {}) {
    const document = options.document || root.document;
    const $ = id => document.getElementById(id);
    let requestedActive = false;
    let controller;
    let deviceOptionsKey = '';
    let messageListKey = '';
    let bindingListKey = '';
    function render(state) {
      const selection = $('familyDevice');
      const devices = uniqueDevices(state.bindings);
      const optionsKey = JSON.stringify(devices.map(binding => binding.device_id));
      if (deviceOptionsKey !== optionsKey) {
        deviceOptionsKey = optionsKey;
        selection.replaceChildren();
        if (!devices.length) selection.append(new Option('暂无已授权设备', ''));
        for (const binding of devices) selection.append(new Option(binding.device_id, binding.device_id));
      }
      if (selection.value !== state.selectedDevice) selection.value = state.selectedDevice;
      selection.disabled = !devices.length;
      $('familyRefresh').disabled = state.loading;
      $('familyRefresh').textContent = state.loading ? '更新中…' : '刷新状态';
      $('familyError').textContent = state.error;
      $('familyError').hidden = !state.error;
      $('familyEmpty').hidden = Boolean(state.selectedDevice);
      $('familyContent').hidden = !state.selectedDevice;
      const image = $('familyFrame');
      if (state.frameUrl) {
        if (image.getAttribute('src') !== state.frameUrl) image.src = state.frameUrl;
      } else image.removeAttribute('src');
      image.hidden = !state.frameUrl;
      $('familyFrameEmpty').hidden = Boolean(state.frameUrl);
      $('familyFrameEmpty').textContent = state.frameStatus;
      $('familyFrameStatus').textContent = state.frameStatus;
      $('familyPause').textContent = state.paused ? '继续查看' : '暂停画面';
      $('familyPause').setAttribute('aria-pressed', String(state.paused));
      const overview = state.overview;
      const online = overview?.device?.online === true;
      $('familyDeviceStatus').textContent = !overview ? '等待状态' : online ? '帽端在线' : '帽端离线';
      $('familyDeviceStatus').className = 'status-tag ' + (online ? 'success' : 'idle');
      $('familySpeechStatus').textContent = overview?.speech?.detail || '等待帽端播报状态';
      const allowed = overview?.permissions?.messages === true;
      $('familyVoice').disabled = !allowed || !state.speechSupported || state.sending || state.listening;
      $('familyVoice').textContent = state.listening ? '正在听，请说话…' : '语音录入';
      $('familyVoiceStop').hidden = !state.listening;
      $('familyVoiceHint').textContent = !state.speechSupported ? state.speechUnavailableReason : state.listening ? '请说出留言，识别完成后确认文字再发送。' : '语音识别后由帽端合成播报。';
      $('familyDraft').disabled = !allowed || state.sending || state.listening;
      if ($('familyDraft').value !== state.transcript) $('familyDraft').value = state.transcript;
      $('familyDraftCount').textContent = state.transcript.length + ' / 500';
      $('familyDraftType').textContent = state.voiceDraft ? '语音识别文字，请确认' : '文字留言';
      $('familySend').disabled = !allowed || state.sending || state.listening || !state.transcript.trim();
      $('familySend').textContent = state.sending ? '正在发送…' : '确认并发送';
      $('familyDelivery').hidden = !state.delivery;
      $('familyDelivery').textContent = state.delivery ? deliveryLabel(state.delivery) : '';
      $('familyDelivery').className = 'family-feedback ' + (state.delivery?.status === 'queued' ? 'is-queued' : 'is-warning');
      const messages = $('familyMessages');
      const messagesKey = JSON.stringify(state.messages);
      if (messageListKey !== messagesKey) {
        messageListKey = messagesKey;
        messages.replaceChildren();
        if (!state.messages.length) {
          const empty = document.createElement('li');
          empty.className = 'muted';
          empty.textContent = '暂无协助消息';
          messages.append(empty);
        }
        for (const item of state.messages.slice(-20).reverse()) {
          const entry = document.createElement('li');
          const meta = document.createElement('div');
          const who = document.createElement('strong');
          who.textContent = item.sender_username || '家属';
          const time = document.createElement('time');
          const date = new Date(item.created_at);
          time.textContent = Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {hour12: false});
          meta.append(who, time);
          const body = document.createElement('p');
          body.textContent = item.body;
          const type = document.createElement('small');
          type.textContent = item.message_type === 'voice_transcript' ? '语音识别留言' : '文字留言';
          entry.append(meta, body, type);
          messages.append(entry);
        }
      }
      const bindings = $('familyBindings');
      const ownedBindings = overview?.role === 'owner' ? state.bindings.filter(binding => binding.device_id === state.selectedDevice && !isOwnerDevice(binding)) : [];
      const bindingsKey = JSON.stringify(ownedBindings);
      if (bindingListKey !== bindingsKey) {
        bindingListKey = bindingsKey;
        bindings.replaceChildren();
        for (const item of ownedBindings) {
          const row = document.createElement('li');
          const label = document.createElement('span');
          label.textContent = item.family_username;
          const revoke = document.createElement('button');
          revoke.className = 'text-button';
          revoke.type = 'button';
          revoke.textContent = '撤销授权';
          revoke.onclick = async () => {
            if (!root.confirm('撤销后，该家属将无法继续查看此设备或发送消息。继续撤销？')) return;
            revoke.disabled = true;
            try {await controller.revokeBinding(item.id);} catch (error) {state.error = error.message; render(state);} finally {revoke.disabled = false;}
          };
          row.append(label, revoke);
          bindings.append(row);
        }
      }
      $('familyPermissions').hidden = overview?.role !== 'owner' || !ownedBindings.length;
    }
    controller = create({...options, onChange: render});
    $('familyDevice').addEventListener('change', event => controller.selectDevice(event.target.value));
    $('familyRefresh').addEventListener('click', () => controller.refresh());
    $('familyPause').addEventListener('click', () => controller.setPaused(!controller.state.paused));
    $('familyVoice').addEventListener('click', () => controller.startRecognition());
    $('familyVoiceStop').addEventListener('click', () => controller.stopRecognition());
    $('familyDraft').addEventListener('input', event => controller.setDraft(event.target.value));
    $('familyMessageForm').addEventListener('submit', event => {event.preventDefault(); controller.sendMessage();});
    $('familyAddDevice').addEventListener('click', async () => {
      const device = $('familyBindDevice');
      if (!device.reportValidity()) return;
      const button = $('familyAddDevice');
      if (button.disabled || $('familyBind').disabled) return;
      button.disabled = true;
      $('familyBind').disabled = true;
      button.textContent = '添加中…';
      $('familyBindingStatus').textContent = '';
      try {
        await controller.addDevice(device.value);
        $('familyBindingStatus').textContent = '帽端已添加，可使用当前账号查看画面和发送留言';
      } catch (error) {
        $('familyBindingStatus').textContent = error.message;
      } finally {
        button.disabled = false;
        $('familyBind').disabled = false;
        button.textContent = '添加我的帽端';
      }
    });
    $('familyBindingForm').addEventListener('submit', async event => {
      event.preventDefault();
      const button = $('familyBind');
      if (button.disabled) return;
      button.disabled = true;
      $('familyAddDevice').disabled = true;
      button.textContent = '授权中…';
      $('familyBindingStatus').textContent = '';
      try {
        await controller.addBinding($('familyBindDevice').value, $('familyBindUsername').value);
        $('familyBindUsername').value = '';
        $('familyBindingStatus').textContent = '家属授权已添加';
      } catch (error) {
        $('familyBindingStatus').textContent = error.message;
      } finally {
        button.disabled = false;
        $('familyAddDevice').disabled = false;
        button.textContent = '授权家属';
      }
    });
    document.addEventListener('visibilitychange', () => controller.setActive(requestedActive && !document.hidden));
    root.addEventListener('pagehide', () => controller.dispose());
    render(controller.state);
    return {setActive(value) {requestedActive = Boolean(value); controller.setActive(requestedActive && !document.hidden);}, dispose: controller.dispose};
  }

  const api = {create, mount, deliveryLabel, uniqueDevices, isOwnerDevice};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingFamilyAssist = api;
})(typeof window === 'undefined' ? globalThis : window);
