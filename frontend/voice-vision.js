(function (root) {
  'use strict';

  const labels = {
    OCR_READ: '识字', OCR_AGAIN: '重新朗读', CAMERA_CAPTURE: '拍照', STOP: '停止',
    ENV_QUERY: '环境查询', SCENE_DESCRIBE: '画面描述', UNKNOWN: '未识别'
  };
  const stateLabel = value => ({
    ready: '就绪', configured: '已配置', unavailable: '不可用', disabled: '已关闭',
    connected: '已连接', client_capability: '浏览器能力', ok: '完成', failed: '失败',
    partial: '部分完成', not_needed: '未调用', no_frame: '无画面', empty: '无结果',
    not_requested: '未请求', processing: '处理中', not_used: '未使用', simulation: '模拟',
    queued: '已进入帽端队列', device_offline: '帽端离线', asset_missing: '缺少音频资源',
    library_error: '语音库不可用', library_missing: '语音库未就绪', queue_full: '播报队列已满', interrupted: '已中断',
    synthesizing: '正在合成语音', synthesis_unavailable: '缺少 Edge TTS 或 FFmpeg',
    synthesis_queue_full: '合成队列已满', text_too_long: '语音文本过长',
    output_unconfigured: '帽端 I2S 输出未配置', sample_rate_mismatch: '音频采样率不匹配'
  }[value] || value || '未知');

  function mount({ cameraMonitor }) {
    const $ = id => root.document.getElementById(id);
    const form = $('voiceVisionForm');
    if (!form) return null;
    let busy = false;
    let currentReply = '';
    let currentTask = null;
    let lastTaskId = null;
    let uploadImage = null;
    let generation = 0;
    let requestController = null;
    let speechTaskId = null;
    let ttsUpdateChain = Promise.resolve();

    function cancelTask(message = '已停止等待和本机朗读') {
      generation += 1;
      requestController?.abort();
      requestController = null;
      root.voiceInput?.stopReply?.();
      if (speechTaskId) updateTts('interrupted', 'browser', speechTaskId);
      speechTaskId = null;
      if (currentTask?.source_mode === 'hardware' && currentTask?.device_id) {
        jsonRequest('/api/tts/stop', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({device_id: currentTask.device_id})
        }).catch(() => {});
      }
      busy = false;
      $('voiceVisionSubmit').disabled = false;
      $('voiceVisionDescribe').disabled = false;
      $('voiceVisionSubmit').textContent = '提交问题';
      $('voiceVisionTaskStatus').textContent = message;
    }

    function tokenHeaders(extra = {}) {
      const token = root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token');
      return {...(token ? {Authorization: 'Bearer ' + token} : {}), ...extra};
    }

    async function jsonRequest(path, init = {}) {
      const response = await root.fetch(path, {...init, headers: tokenHeaders(init.headers || {})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
      return data;
    }

    function sourceInfo() {
      const sourceId = cameraMonitor?.sourceId || 'local_camera';
      const sourceMode = sourceId.startsWith('simulator_') ? 'simulation'
        : sourceId === 'local_camera' ? 'software' : 'hardware';
      return {source_id: uploadImage ? 'upload' : sourceId, source_mode: uploadImage ? 'software' : sourceMode};
    }

    function ttsTarget(info) {
      return info.source_mode === 'hardware' ? 'device'
        : info.source_mode === 'simulation' ? 'simulation' : 'browser';
    }

    function updateModeLabel() {
      const info = sourceInfo();
      const label = info.source_mode === 'hardware' ? '实机 · 帽端摄像头'
        : info.source_mode === 'simulation' ? '模拟 · 软件联调' : uploadImage ? '软件联调 · 上传图片' : '软件联调 · 电脑摄像头';
      $('voiceVisionMode').textContent = label;
      $('voiceVisionSource').textContent = info.source_mode === 'hardware' ? '实机' : info.source_mode === 'simulation' ? '模拟联调' : '软件联调';
    }

    function renderStages(task) {
      const stages = [
        ['ASR', task.asr_status], ['意图', task.intent_status], ['YOLO', task.yolo_status],
        ['VLM', task.vlm_status], ['回复', task.reply_status], ['朗读', task.tts_status]
      ];
      $('voiceVisionStages').replaceChildren(...stages.map(([label, state]) => {
        const item = root.document.createElement('div');
        item.className = 'voice-stage';
        const title = root.document.createElement('strong'); title.textContent = label;
        const value = root.document.createElement('span'); value.textContent = stateLabel(state);
        item.append(title, value); return item;
      }));
    }

    function renderTask(task) {
      currentTask = task;
      currentReply = task.final_reply || task.reply_text || task.reply || task.text || '';
      lastTaskId = task.task_id || task.id || null;
      if (currentTask) currentTask.task_id = lastTaskId;
      $('voiceVisionReply').textContent = currentReply || '任务没有生成回复';
      $('voiceVisionIntent').textContent = `${labels[task.intent] || task.intent || '未知意图'} · ${Math.round((task.intent_confidence || 0) * 100)}%`;
      $('voiceVisionSource').textContent = task.source_mode === 'hardware' ? '实机' : task.source_mode === 'simulation' ? '模拟联调' : '软件联调';
      $('voiceVisionModel').textContent = task.vlm_model ? `VLM · ${task.vlm_model}` : task.ocr_model ? `OCR · ${task.ocr_model}` : '视觉模型未参与';
      $('voiceVisionDetection').textContent = (task.detection_snapshot || []).length
        ? task.detection_snapshot.map(item => `${item.direction || '画面中'} · ${item.class} · ${Math.round((item.confidence || 0) * 100)}%`).join('\n')
        : task.yolo_status === 'ready' ? '当前帧没有检测到目标' : `检测状态：${stateLabel(task.yolo_status)}`;
      $('voiceVisionScene').textContent = task.vlm_scene_description || (task.vlm_status === 'unavailable' ? 'VLM 未配置；仍可查看 YOLO 结果。' : '本次任务未调用场景描述模型。');
      $('voiceVisionLatency').textContent = Object.entries(task.stage_latency_ms || {}).map(([key, value]) => `${key} ${value}ms`).join(' · ') || '耗时未提供';
      renderStages(task);
      $('voiceVisionTaskStatus').textContent = task.error_code ? `已完成 · ${task.error_code}`
        : task.tts_target === 'device' && task.tts_status === 'synthesizing' ? '回复已生成，正在合成帽端语音…'
        : task.tts_target === 'device' && task.tts_status === 'queued' ? '回复已生成，语音已发送，等待帽端播放…'
        : '任务完成';
      $('voiceVisionReplyPanel').classList.add('has-result');
      $('voiceVisionReplay').disabled = !currentReply;
      refreshHistory();
    }

    async function submitTask({intent, text} = {}) {
      if (busy) return;
      const question = String(text ?? $('voiceVisionQuestion').value ?? '').trim();
      const info = sourceInfo();
      if (!question && !intent) return $('voiceVisionQuestion').focus();
      cancelTask();
      const taskGeneration = generation;
      const controller = new AbortController();
      requestController = controller;
      const timeout = root.setTimeout(() => controller.abort(), 310000);
      busy = true;
      currentReply = '';
      $('voiceVisionReplay').disabled = true;
      $('voiceVisionSubmit').disabled = true;
      $('voiceVisionDescribe').disabled = true;
      $('voiceVisionTaskStatus').textContent = '正在识别意图并处理当前画面…';
      $('voiceVisionSubmit').textContent = '处理中…';
      try {
        let response;
        if (uploadImage) {
          const query = new URLSearchParams({
            text: question || (intent === 'SCENE_DESCRIBE' ? '描述一下画面' : ''),
            source_id: 'upload', source_mode: 'software', tts_target: 'browser'
          });
          if (intent) query.set('intent', intent);
          response = await root.fetch('/api/voice-vision/tasks?' + query, {
            method: 'POST', body: uploadImage, signal: controller.signal,
            headers: tokenHeaders({'Content-Type': uploadImage.type || 'image/jpeg'})
          });
        } else if (cameraMonitor?.active()) {
          const frame = await cameraMonitor.captureFrame();
          if (generation !== taskGeneration) return;
          const query = new URLSearchParams({...info, text: question || (intent === 'SCENE_DESCRIBE' ? '描述一下画面' : ''), tts_target: ttsTarget(info)});
          if (intent) query.set('intent', intent);
          if (cameraMonitor.sourceId !== 'local_camera' && cameraMonitor.frameSeq != null) query.set('frame_seq', cameraMonitor.frameSeq);
          response = await root.fetch('/api/voice-vision/tasks?' + query, {
            method: 'POST', body: frame, signal: controller.signal,
            headers: tokenHeaders({'Content-Type': frame.type || 'image/jpeg'})
          });
        } else {
          response = await root.fetch('/api/voice-vision/tasks', {
            method: 'POST', body: JSON.stringify({...info, text: question, intent, tts_target: ttsTarget(info)}), signal: controller.signal,
            headers: tokenHeaders({'Content-Type': 'application/json'})
          });
        }
        const task = await response.json();
        if (generation !== taskGeneration) return;
        if (!response.ok) throw new Error(task.detail || `请求失败（${response.status}）`);
        uploadImage = null;
        $('voiceVisionUpload').value = '';
        $('voiceVisionUploadName').textContent = '也可以上传图片进行软件联调';
        updateModeLabel();
        renderTask(task);
        $('voiceVisionTaskStatus').textContent = task.reply_status === 'partial'
          ? '已使用检测结果回复；场景描述不可用' : task.reply_status === 'failed' ? '任务部分失败，请查看各阶段状态' : '回复已生成';
      } catch (error) {
        if (generation !== taskGeneration) return;
        $('voiceVisionTaskStatus').textContent = error.name === 'AbortError' ? '任务等待已取消或超时，可重新提交' : '任务失败：' + error.message;
        $('voiceVisionReply').textContent = '未生成回复';
      } finally {
        root.clearTimeout(timeout);
        if (generation !== taskGeneration) return;
        requestController = null;
        busy = false;
        $('voiceVisionSubmit').disabled = false;
        $('voiceVisionDescribe').disabled = false;
        $('voiceVisionSubmit').textContent = '提交问题';
      }
    }

    function updateTts(status, target = 'browser', taskId = lastTaskId) {
      if (!taskId) return Promise.resolve();
      ttsUpdateChain = ttsUpdateChain.then(async () => {
        try {
          await jsonRequest(`/api/voice-vision/tasks/${taskId}/tts`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({status, target})
          });
          if (taskId !== lastTaskId) return;
          if (currentTask) currentTask.tts_status = status;
          renderStages(currentTask || {});
          $('voiceVisionTaskStatus').textContent = status === 'completed' ? '本机浏览器朗读完成' : status === 'speaking' ? '本机浏览器正在朗读' : `朗读状态：${stateLabel(status)}`;
          refreshHistory();
        } catch (_) {}
      });
      return ttsUpdateChain;
    }

    async function speakReply() {
      if (!currentReply) return;
      const info = currentTask?.source_mode
        ? {source_mode: currentTask.source_mode, source_id: currentTask.source_id}
        : sourceInfo();
      if (info.source_mode === 'hardware') {
        if (!lastTaskId) return;
        $('voiceVisionTaskStatus').textContent = '正在加入帽端语音队列…';
        jsonRequest(`/api/voice-vision/tasks/${lastTaskId}/speak`, {method: 'POST'})
          .then(result => {
            const status = result.status || 'queued';
            if (currentTask) currentTask.tts_status = status;
            renderStages(currentTask || {});
            $('voiceVisionTaskStatus').textContent = status === 'synthesizing'
              ? '正在合成语音，完成后发送到帽端…' : '已加入帽端语音队列，等待设备播放回执';
            refreshHistory();
          })
          .catch(error => {
            const state = error.message.includes('音频资源') ? 'asset_missing' : error.message.includes('采样率') ? 'sample_rate_mismatch' : error.message.includes('队列') ? 'queue_full' : error.message.includes('Edge TTS') ? 'synthesis_unavailable' : 'device_offline';
            if (currentTask) currentTask.tts_status = state;
            renderStages(currentTask || {});
            $('voiceVisionTaskStatus').textContent = error.message;
          });
        return;
      }
      const taskId = lastTaskId;
      root.voiceInput?.stopReply?.();
      speechTaskId = taskId;
      $('voiceVisionTaskStatus').textContent = 'Edge TTS 正在合成电脑朗读…';
      await updateTts('pending', 'browser', taskId);
      try {
        if (!root.QimingEdgeTts?.supported) throw new Error('Edge TTS 音频播放器不可用');
        await root.QimingEdgeTts.speak(currentReply, {
          onstart: () => { updateTts('speaking', 'browser', taskId); $('voiceVisionTaskStatus').textContent = 'Edge TTS 正在朗读回复'; },
        });
        if (speechTaskId === taskId) {
          speechTaskId = null;
          await updateTts('completed', 'browser', taskId);
        }
      } catch (error) {
        if (error.name === 'AbortError' || speechTaskId !== taskId) return;
        if (speechTaskId === taskId) speechTaskId = null;
        $('voiceVisionTaskStatus').textContent = `电脑朗读失败：${error.message}`;
        await updateTts('failed', 'browser', taskId);
      }
    }

    async function refreshCapabilities() {
      try {
        const data = await jsonRequest('/api/voice-vision/capabilities');
        $('voiceVisionCapabilities').replaceChildren(...data.capabilities.map(capability => {
          const item = root.document.createElement('div'); item.className = 'voice-capability';
          const name = root.document.createElement('strong'); name.textContent = ({intent: '意图识别', yolo: 'YOLO', ocr: 'OCR', vlm: 'VLM', asr: 'ASR', browser_tts: '本机朗读', device_tts: '帽端朗读'}[capability.name] || capability.name);
          const state = root.document.createElement('span');
          const detail = capability.name === 'device_tts'
            ? ` · 音频库${stateLabel(capability.library)} · 实时合成${stateLabel(capability.synthesis)} · I2S 输出${stateLabel(capability.output)}` : '';
          state.textContent = stateLabel(capability.status) + detail; state.dataset.state = capability.status;
          item.append(name, state); return item;
        }));
      } catch (_) {
        $('voiceVisionCapabilities').textContent = '能力状态暂不可用，请检查后端连接。';
      }
    }

    async function refreshHistory() {
      try {
        const mode = $('voiceVisionFilter').value;
        const query = mode === 'all' ? '' : `?source_mode=${encodeURIComponent(mode)}`;
        const tasks = await jsonRequest('/api/voice-vision/tasks' + query);
        if (!tasks.length) { $('voiceVisionHistory').textContent = '暂无任务记录'; return; }
        $('voiceVisionHistory').replaceChildren(...tasks.map(task => {
          const row = root.document.createElement('button'); row.type = 'button'; row.className = 'voice-history-row';
          const main = root.document.createElement('span'); main.className = 'voice-history-main';
          const title = root.document.createElement('strong'); title.textContent = `${labels[task.intent] || task.intent} · ${task.user_text || '快捷任务'}`;
          const reply = root.document.createElement('span'); reply.textContent = task.final_reply || '无回复';
          const detail = root.document.createElement('small'); detail.textContent = `${task.source_mode === 'hardware' ? '实机' : task.source_mode === 'simulation' ? '模拟' : '软件联调'} · ${new Date(task.created_at).toLocaleString('zh-CN')}`;
          main.append(title, reply, detail); const state = root.document.createElement('em'); state.textContent = `${stateLabel(task.reply_status)} / ${stateLabel(task.tts_status)}`;
          row.append(main, state); row.onclick = () => renderTask(task); return row;
        }));
      } catch (_) { $('voiceVisionHistory').textContent = '任务记录暂不可用，请检查后端连接。'; }
    }

    function handleTtsEvent(event) {
      if (!event || event.task_id !== lastTaskId) return;
      if (currentTask) currentTask.tts_status = event.status;
      renderStages(currentTask || {});
      $('voiceVisionTaskStatus').textContent = ({
        queued: '语音已生成，等待帽端播放', synthesizing: '正在合成语音…',
        speaking: '帽端正在播放', completed: '帽端播放完成并已回执',
        failed: `帽端播放失败${event.error_code ? `（${event.error_code}）` : ''}`,
        interrupted: '帽端已中断播放'
      }[event.status] || `帽端状态：${stateLabel(event.status)}`);
      refreshHistory();
    }

    $('voiceVisionDescribe').onclick = () => submitTask({intent: 'SCENE_DESCRIBE', text: '描述一下画面'});
    $('voiceVisionReplay').onclick = speakReply;
    if ($('voiceVisionStop')) $('voiceVisionStop').onclick = () => cancelTask();
    $('voiceVisionForm').addEventListener('submit', event => { event.preventDefault(); submitTask(); });
    $('voiceVisionFilter').onchange = refreshHistory;
    $('voiceVisionUpload').onchange = () => {
      uploadImage = $('voiceVisionUpload').files?.[0] || null;
      $('voiceVisionUploadName').textContent = uploadImage ? uploadImage.name : '也可以上传图片进行软件联调';
      updateModeLabel();
    };
    root.addEventListener('qiming-camera-source-changed', () => {
      cancelTask('来源已切换，旧任务回复不会覆盖当前画面');
      uploadImage = null;
      $('voiceVisionUpload').value = '';
      $('voiceVisionUploadName').textContent = '画面来源已切换；下一次任务会使用新来源';
      updateModeLabel();
    });
    root.addEventListener('qiming-device-status', updateModeLabel);
    root.addEventListener('qiming-camera-stopped', () => { cancelTask('摄像头已停止'); updateModeLabel(); });
    root.addEventListener('pagehide', () => cancelTask());
    root.addEventListener('qiming-voice-task-completed', event => {
      if (!event.detail) return;
      renderTask(event.detail);
      if (event.detail.tts_target === 'browser') root.setTimeout(() => speakReply(), 0);
    });
    root.addEventListener('qiming-live-voice-task-completed', event => {
      if (!event.detail) return;
      renderTask({...event.detail, user_text: event.detail.text || event.detail.user_text});
      if (event.detail.tts_target === 'browser') root.setTimeout(() => speakReply(), 0);
    });
    updateModeLabel();
    refreshCapabilities(); refreshHistory();
    return {submitTask, refreshCapabilities, refreshHistory, speakReply, handleTtsEvent};
  }

  const api = {mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingVoiceVision = api;
})(typeof window === 'undefined' ? globalThis : window);
