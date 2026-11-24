(function (root) {
  'use strict';

  const directions = {left: '左侧', front: '前方', right: '右侧'};
  const requestTypes = {
    environment_query: '环境查询', env_query: '环境查询',
    dialogue: '日常对话', chat: '日常对话', general_chat: '日常对话',
    ocr_read: '文字识别', ocr_again: '再次朗读', camera_capture: '拍照',
    stop: '停止播报', unknown: '未分类', simulation: '模拟查询'
  };
  function escapeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }
  function formatTimestamp(value, timeZone) {
    if (!value) return '时间未知';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '时间未知';
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).formatToParts(date);
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${fields.year}-${fields.month}-${fields.day} ${fields.hour}:${fields.minute}:${fields.second}`;
  }
  function validateSettings(confidence, cooldown) {
    const errors = {};
    if (String(confidence).trim() === '' || !Number.isFinite(Number(confidence)) || Number(confidence) < 0 || Number(confidence) > 1) {
      errors.confidence = '检测置信度请输入 0 到 1 之间的小数';
    }
    if (!/^\d+$/.test(String(cooldown).trim()) || !Number.isSafeInteger(Number(cooldown)) || Number(cooldown) <= 0) {
      errors.cooldown = '预警冷却时间请输入大于 0 的整数';
    }
    return errors;
  }
  function icon(kind) {
    const paths = {
      device: '<rect x="5" y="3" width="14" height="14" rx="2"/><path d="M8 21h8m-4-4v4M8 7h8m-8 4h5"/>',
      empty: '<rect x="5" y="5" width="30" height="23" rx="3"/><path d="M15 35h10m-5-7v7M11 12h18m-18 6h10"/><circle cx="35" cy="30" r="7"/><path d="M32 30h6"/>'
    };
    return `<svg viewBox="0 0 ${kind === 'empty' ? '44 40' : '24 24'}" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${paths[kind] || paths.device}</svg>`;
  }
  function emptyTable(message, columns) {
    return `<tr><td colspan="${columns}" class="table-empty"><span class="empty-table-icon">${icon('device')}</span><strong>${escapeText(message)}</strong></td></tr>`;
  }
  function alertRows(rows) {
    if (!rows.length) return emptyTable('暂无预警记录', 4);
    return rows.map(row => {
      const name = root.QimingCameraSpeech?.objectName(row.object_class) || row.object_class || '未知目标';
      const simulation = String(row.device_id || '').startsWith('simulator_');
      return `<tr><td><time title="${escapeText(row.detected_at)}">${formatTimestamp(row.detected_at)}</time></td><td><strong class="table-object">${escapeText(name)}</strong>${simulation ? ' <span class="status-tag warning">模拟</span>' : ''}</td><td><span class="direction-tag">${escapeText(directions[row.direction] || row.direction || '未知')}</span></td><td class="table-message">${escapeText(row.voice_text || '—')}</td></tr>`;
    }).join('');
  }
  function voiceRows(rows) {
    if (!rows.length) return emptyTable('暂无语音交互记录', 5);
    return rows.map(row => {
      const state = row.tts_status || row.asr_status || '';
      const simulation = [row.tts_status, row.asr_status].includes('simulation') || String(row.device_id || '').startsWith('simulator_');
      const failed = ['failed', 'error', 'unavailable'].includes(state);
      const ready = ['ok', 'success', 'completed', 'done', 'played'].includes(state);
      const label = simulation ? '模拟' : failed ? '播报失败' : ready ? '已完成' : ({pending: '待播报', speaking: '播报中', skipped: '已跳过', disabled: '已关闭', interrupted: '已中断'}[state] || '未提供');
      const tone = simulation ? 'warning' : failed ? 'error' : ready ? 'success' : 'idle';
      return `<tr><td><time title="${escapeText(row.occurred_at)}">${formatTimestamp(row.occurred_at)}</time></td><td><div class="chat-bubble chat-bubble-user">${escapeText(row.user_text || '—')}</div></td><td><div class="chat-bubble chat-bubble-reply">${escapeText(row.reply_text || '暂无回复')}</div></td><td><span class="type-tag">${escapeText(requestTypes[row.request_type] || row.request_type || '未分类')}</span></td><td><span class="status-tag ${tone}"><i aria-hidden="true"></i>${label}</span></td></tr>`;
    }).join('');
  }
  function deviceRows(rows) {
    if (!rows.length) return `<div class="empty-state"><span class="empty-state-art">${icon('empty')}</span><strong>等待设备接入</strong><p>连接帽端设备或启动模拟器后，<br>设备状态与心跳将在这里更新。</p></div>`;
    return rows.map(row => `<div class="device-row"><span class="device-row-icon">${icon('device')}</span><span><strong>${escapeText(row.name || row.device_id)}</strong><small>最后心跳 ${formatTimestamp(row.last_heartbeat)}</small></span><span class="status-tag ${row.online ? 'success' : 'idle'}"><i aria-hidden="true"></i>${row.online ? '在线' : '离线'}</span></div>`).join('');
  }

  function mount(options) {
    const document = options.document || root.document;
    const fetch = options.fetch || root.fetch.bind(root);
    const $ = id => document.getElementById(id);
    let refreshing = false;
    let dirty = false;
    let editVersion = 0;
    let saving = false;
    let debugging = false;
    const fields = ['confidence', 'cooldown', 'obstacle', 'voice'];
    for (const id of fields) {
      $(id).addEventListener('input', () => {
        dirty = true;
        editVersion++;
        $(id).setCustomValidity('');
        $(id).removeAttribute('aria-invalid');
        $('saveMsg').textContent = '有尚未保存的修改';
        $('saveMsg').className = 'muted';
      });
    }

    async function request(path, init = {}) {
      const token = root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token');
      const response = await fetch(path, {...init, headers: {...(token ? {Authorization: 'Bearer ' + token} : {}), ...init.headers}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
      return data;
    }
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        await Promise.allSettled([
          request('/api/obstacles').then(rows => {
            $('alertRows').innerHTML = alertRows(rows);
            $('alertsLoadState').textContent = '按最新发生时间排列';
          }).catch(() => { $('alertsLoadState').textContent = '更新失败，请检查后端连接'; }),
          request('/api/voices').then(rows => {
            $('voiceRows').innerHTML = voiceRows(rows);
            $('voicesLoadState').textContent = '最近的交互优先显示';
          }).catch(() => { $('voicesLoadState').textContent = '更新失败，请检查后端连接'; }),
          request('/api/config').then(config => {
            options.onConfig(config);
            if (dirty || saving) return;
            $('confidence').value = config.confidence;
            $('cooldown').value = config.alert_cooldown_ms;
            $('obstacle').checked = config.obstacle_enabled;
            $('voice').checked = config.voice_enabled;
          }),
          request('/api/tablet/devices').then(rows => {
            $('deviceList').innerHTML = deviceRows(rows);
            $('deviceLoadState').textContent = '实时';
          }).catch(() => { $('deviceLoadState').textContent = '更新失败'; })
        ]);
      } finally {
        refreshing = false;
      }
    }

    $('configForm').addEventListener('submit', async event => {
      event.preventDefault();
      if (saving) return;
      const errors = validateSettings($('confidence').value, $('cooldown').value);
      for (const id of ['confidence', 'cooldown']) {
        $(id).setCustomValidity(errors[id] || '');
        $(id).setAttribute('aria-invalid', String(Boolean(errors[id])));
      }
      const invalid = Object.keys(errors)[0];
      if (invalid) {
        $('saveMsg').textContent = errors[invalid];
        $('saveMsg').className = 'form-error';
        $(invalid).reportValidity();
        return;
      }
      saving = true;
      const savedVersion = editVersion;
      $('saveConfig').disabled = true;
      $('saveConfig').textContent = '保存中…';
      $('saveMsg').textContent = '';
      try {
        await request('/api/config', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          confidence: Number($('confidence').value), alert_cooldown_ms: Number($('cooldown').value),
          obstacle_enabled: $('obstacle').checked, voice_enabled: $('voice').checked
        })});
        dirty = editVersion !== savedVersion;
        $('saveMsg').textContent = dirty ? '设置已保存，仍有新的修改待保存' : '设置已保存';
        $('saveMsg').className = 'form-success';
      } catch (error) {
        $('saveMsg').textContent = error.message || '保存失败，请稍后重试';
        $('saveMsg').className = 'form-error';
      } finally {
        saving = false;
        $('saveConfig').disabled = false;
        $('saveConfig').textContent = '保存设置';
      }
    });

    $('debugForm').addEventListener('submit', async event => {
      event.preventDefault();
      if (debugging) return;
      const text = $('simVoice').value.trim();
      if (!text) { $('simVoice').focus(); return; }
      debugging = true;
      $('sendVoice').disabled = true;
      $('sendVoice').textContent = '查询中…';
      $('debugState').textContent = '正在处理';
      $('debugOut').classList.add('is-loading');
      $('debugOut').setAttribute('aria-busy', 'true');
      $('debugOut').textContent = '正在查询电脑后端，请稍候…';
      const controller = new AbortController();
      const timeout = root.setTimeout(() => controller.abort(), 130000);
      try {
        const context = options.getVoiceVisionContext ? options.getVoiceVisionContext() : null;
        const path = context ? '/api/voice-vision/tasks' : '/api/simulate/voice';
        const ttsTarget = context?.source_mode === 'hardware' ? 'device' : context?.source_mode === 'simulation' ? 'simulation' : 'browser';
        const payload = context ? {...context, text, tts_target: ttsTarget} : {device_id: 'simulator_001', text};
        const data = await request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal, body: JSON.stringify(payload)});
        if (context) root.dispatchEvent?.(new root.CustomEvent('qiming-voice-task-completed', {detail: data}));
        $('debugOut').textContent = JSON.stringify(data, null, 2);
        $('debugState').textContent = '查询完成';
      } catch (error) {
        $('debugOut').textContent = '查询失败\n' + (error.name === 'AbortError' ? '请求超时，请检查电脑模型服务后重试。' : error.message);
        $('debugState').textContent = '执行失败';
      } finally {
        root.clearTimeout(timeout);
        debugging = false;
        $('sendVoice').disabled = false;
        $('sendVoice').textContent = '测试语音查询';
        $('debugOut').classList.remove('is-loading');
        $('debugOut').setAttribute('aria-busy', 'false');
      }
    });

    return {refresh};
  }

  const api = {mount, formatTimestamp, validateSettings, alertRows, voiceRows, deviceRows};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.QimingConsoleUI = api;
})(typeof window === 'undefined' ? globalThis : window);
