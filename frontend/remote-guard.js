(function (root) {
  'use strict';

  function mount() {
    var doc = root.document;
    var $ = function (id) { return doc.getElementById(id); };
    if (!$('guardPage') && !$('assistantPage')) return { refresh: function () {} };

    var state = {
      deviceId: root.localStorage.getItem('qh_guard_device') || '',
      socket: null, mapConfig: null, mapSdkPromise: null, maps: {}, mapObjects: {},
      location: null, navigation: null, lastReverseCell: '', knownEvents: new Set(), events: [],
      audioCapture: null, videoStreaming: false, videoConnecting: false,
      videoAvailable: false, videoLastFrameAt: 0, videoConnectTimer: null, mapSdkError: '',
      watchId: null, watchDeviceId: '', locationWatchGeneration: 0, locationStarting: false,
      locationProvider: '', locationStatusMessage: '', locationStartFailed: false,
      autoLocationAttemptedDevice: '', locationReadyPromise: null, locationReadyDevice: '', ownerDevice: false,
      hasDevice: false, refreshing: false, loadedDeviceId: '', devicesLoading: false
    };

    function token() { return root.sessionStorage.getItem('qh_token') || root.localStorage.getItem('qh_token') || ''; }
    function headers() { return { Authorization: 'Bearer ' + token(), 'Content-Type': 'application/json' }; }
    async function request(path, init) {
      var options = init || {};
      var response = await root.fetch(path, Object.assign({}, options, { headers: Object.assign({}, headers(), options.headers || {}) }));
      var data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || ('请求失败（' + response.status + '）'));
      return data;
    }
    function text(id, value) { var node = $(id); if (node) node.textContent = value == null ? '' : String(value); }
    function tone(id, value, kind) { var node = $(id); if (!node) return; node.textContent = value; node.className = kind || ''; }
    function activeSurface() { return Boolean(($('guardPage') && $('guardPage').classList.contains('active')) || ($('assistantPage') && $('assistantPage').classList.contains('active'))); }
    function guardPageActive() { return Boolean($('guardPage') && $('guardPage').classList.contains('active')); }
    function cleanName(name) { var value = String(name || '').replace(/演示|模拟/g, '').trim(); return value || '启明辅行设备'; }

    function setLocationStatus(message) {
      state.locationStatusMessage = message;
      text('guardDiagnosticStatus', message);
      text('guardLocationSourceStatus', message);
    }

    function renderLocalLocationControl() {
      var button = $('guardUseLocation');
      if (button) {
        button.hidden = !state.ownerDevice;
        button.disabled = !state.hasDevice || !state.ownerDevice || state.locationStarting;
        button.textContent = state.locationStarting ? '正在获取本机位置…'
          : state.watchId !== null ? '停止本机定位'
          : state.locationStartFailed ? '重试本机定位'
          : state.locationProvider === 'browser_geolocation' ? '重新连接本机定位' : '启用本机定位';
        button.setAttribute('aria-pressed', String(state.watchId !== null));
      }
      if (!state.ownerDevice || !state.hasDevice) return;
      if (state.watchId !== null) setLocationStatus('本机定位正在同步到当前设备；家属可见性沿用设备共享设置。');
      else if (state.locationProvider === 'browser_geolocation') setLocationStatus('设备位置来源是本机定位；此页面尚未连接，点击按钮重新连接。');
      else if (state.locationProvider) setLocationStatus(state.locationStatusMessage || '设备正在使用其他定位来源；启用本机定位会切换到这台电脑。');
      else setLocationStatus(state.locationStatusMessage || '点击启用并允许浏览器定位，位置才会同步到当前设备。');
    }

    function maybeAutoStartLocalLocation() {
      if (!activeSurface() || !state.ownerDevice || !state.hasDevice || !state.deviceId || state.watchId !== null || state.locationStarting) return;
      if (state.locationProvider && state.locationProvider !== 'browser_geolocation') return;
      if (state.autoLocationAttemptedDevice === state.deviceId) return;
      state.autoLocationAttemptedDevice = state.deviceId;
      var button = $('guardUseLocation');
      if (button && typeof button.onclick === 'function') {
        state.locationReadyDevice = state.deviceId;
        state.locationReadyPromise = Promise.resolve(button.onclick()).finally(function () {
          state.locationReadyPromise = null;
        });
      }
    }

    function locationErrorMessage(error) {
      if (!error || error.code === 2) return '电脑未能确定位置，请检查系统定位服务后重试。';
      if (error.code === 1) return '浏览器未获定位权限；请在地址栏站点权限中允许位置后重试。';
      if (error.code === 3) return '获取电脑位置超时，请稍后重试。';
      return error.message || '无法读取电脑位置。';
    }

    async function stopLocalGeolocation(deviceId, reportStatus) {
      var targetDevice = deviceId || state.watchDeviceId || state.deviceId;
      if (state.watchId !== null && root.navigator.geolocation) root.navigator.geolocation.clearWatch(state.watchId);
      state.watchId = null;
      state.watchDeviceId = '';
      state.locationWatchGeneration += 1;
      state.locationStarting = false;
      if (targetDevice && state.locationProvider === 'browser_geolocation') {
        await request('/api/v1/location/provider', { method: 'POST', body: JSON.stringify({ device_id: targetDevice, source: 'browser_geolocation', enabled: false }) });
      }
      if (state.deviceId === targetDevice) state.locationProvider = '';
      renderLocalLocationControl();
      if (reportStatus) setLocationStatus(reportStatus);
    }

    function setDeviceAvailability(available) {
      state.hasDevice = Boolean(available && state.deviceId);
      var ids = ['guardRefreshLocation', 'guardHistoryToggle', 'guardStartVideo', 'guardShareToggle', 'guardAssistantInput', 'guardMic', 'guardSimulateStart', 'guardSimulateStop'];
      ids.forEach(function (id) { var node = $(id); if (node) node.disabled = !state.hasDevice; });
      var form = $('guardAssistantForm'); if (form) form.querySelector('button[type="submit"]').disabled = !state.hasDevice;
      doc.querySelectorAll('[data-guard-query]').forEach(function (button) { button.disabled = !state.hasDevice; });
      if (!state.hasDevice) {
        state.ownerDevice = false;
        state.locationProvider = '';
        renderLocalLocationControl();
        renderLocation({ available: false }); renderNavigation({ status: 'IDLE' }); renderPoiResults([]);
        state.knownEvents.clear();
        var timeline = $('guardTimeline'); if (timeline) timeline.innerHTML = '<li class="guard-event guard-event-empty"><time>—</time><div><strong>暂无动态</strong><p>绑定设备后显示重要动态。</p></div></li>';
        text('assistantDeviceName', '暂无已绑定设备');
        text('guardDeviceState', '暂无设备');
        tone('guardLocationState', '等待设备', 'is-warn');
        text('guardLocationNote', '绑定设备后可查看位置');
        text('guardAddress', '地址暂不可用');
        text('guardUpdated', '等待设备');
        text('guardMapEmpty', '绑定设备后显示实时位置');
        text('assistantMapState', '绑定设备后查看地图');
        text('guardShareState', '尚无可用设备');
        text('guardEnvironmentState', '等待画面');
        text('guardEnvironmentDetail', '绑定设备并收到识别结果后显示');
        text('guardVoiceStatus', '绑定设备后可使用地图助手。');
        renderVideoStatus(false);
      }
    }

    function renderEvents(events) {
      var list = $('guardTimeline'); if (!list || !Array.isArray(events)) return;
      state.events = events;
      var filter = $('guardEventFilter') ? $('guardEventFilter').value : 'all';
      var visible = events.filter(function (event) {
        if (!event || !event.id) return false;
        if (filter === 'warning') return event.type === 'warning_event';
        if (filter === 'navigation') return /^navigation_/.test(event.type || '');
        return true;
      }).sort(function (a, b) { return (Date.parse(b.occurred_at) || 0) - (Date.parse(a.occurred_at) || 0); }).slice(0, 8);
      list.replaceChildren();
      if (!visible.length) {
        list.innerHTML = '<li class="guard-event guard-event-empty"><time>—</time><div><strong>暂无动态</strong><p>有重要变化时会显示在这里。</p></div></li>';
        return;
      }
      visible.forEach(function (event) {
        var item = doc.createElement('li'); item.className = 'guard-event';
        var time = doc.createElement('time'); var date = new Date(event.occurred_at || '');
        time.textContent = Number.isFinite(date.getTime()) ? date.toLocaleTimeString('zh-CN', { hour12: false }) : '—';
        var body = doc.createElement('div'); var heading = doc.createElement('strong');
        heading.textContent = event.type === 'location_update' ? '位置已同步' : (event.title || '设备状态更新');
        var details = doc.createElement('details'); var summary = doc.createElement('summary'); summary.textContent = '查看详情';
        var copy = doc.createElement('p'); copy.textContent = event.detail || '暂无更多详情';
        details.append(summary, copy); body.append(heading, details); item.append(time, body); list.appendChild(item);
      });
    }

    function sdkProvider() { return state.mapConfig && state.mapConfig.js_provider || 'amap'; }
    function sdkReady() {
      return sdkProvider() === 'baidu' ? Boolean(root.BMapGL && root.BMapGL.Map && root.BMapGL.Convertor) : Boolean(root.AMap && root.AMap.Map);
    }

    function loadMapSdk() {
      if (state.mapSdkPromise) return state.mapSdkPromise;
      state.mapSdkPromise = (async function () {
        if (!state.mapConfig) {
          try { state.mapConfig = await request('/api/v1/map/config'); }
          catch (_) { state.mapSdkError = '地图配置读取失败，请刷新页面重试'; return null; }
        }
        if (!state.mapConfig.js_key) {
          state.mapSdkError = (sdkProvider() === 'baidu' ? '百度地图' : '高德地图') + '网页 Key 尚未配置';
          return state.mapConfig;
        }
        if (sdkReady()) { state.mapSdkError = ''; return state.mapConfig; }
        try {
          var baidu = sdkProvider() === 'baidu';
          if (!baidu && state.mapConfig.security_code) root._AMapSecurityConfig = { securityJsCode: state.mapConfig.security_code };
          var resources = [];
          resources.push(new Promise(function (resolve, reject) {
            var script = doc.createElement('script');
            script.async = true;
            script.src = baidu
              ? 'https://api.map.baidu.com/getscript?type=webgl&v=1.0&ak=' + encodeURIComponent(state.mapConfig.js_key) + '&services=&t=' + Date.now()
              : 'https://webapi.amap.com/maps?v=2.0&key=' + encodeURIComponent(state.mapConfig.js_key);
            script.onload = resolve;
            script.onerror = function () { reject(new Error((baidu ? '百度地图' : '高德地图') + ' SDK 加载失败，请检查网络、浏览器 Key 和来源白名单')); };
            doc.head.appendChild(script);
          }));
          if (baidu) resources.push(new Promise(function (resolve, reject) {
            var style = doc.createElement('link');
            style.rel = 'stylesheet';
            style.href = 'https://api.map.baidu.com/res/webgl/10/bmap.css';
            style.onload = resolve;
            style.onerror = function () { reject(new Error('百度地图样式加载失败，请检查网络')); };
            doc.head.appendChild(style);
          }));
          await Promise.all(resources);
          if (!sdkReady()) throw new Error((baidu ? '百度地图' : '高德地图') + ' SDK 未初始化，请确认浏览器 Key、来源白名单和地图服务已开通');
          state.mapSdkError = '';
        } catch (error) { state.mapSdkError = error.message || '地图服务暂不可用'; }
        return state.mapConfig;
      })();
      var pending = state.mapSdkPromise;
      return pending.finally(function () { if (state.mapSdkPromise === pending) state.mapSdkPromise = null; });
    }

    function ensureMap(id, center) {
      if (!sdkReady() || !state.mapConfig || !state.mapConfig.js_key || !$(id)) return null;
      if (!state.maps[id]) {
        if (sdkProvider() === 'baidu') {
          state.maps[id] = new root.BMapGL.Map(id);
          state.maps[id].centerAndZoom(center, 16);
          state.maps[id].enableScrollWheelZoom(true);
        } else {
          state.maps[id] = new root.AMap.Map(id, { zoom: 16, center: center, viewMode: '2D' });
        }
        $(id).classList.add('is-map-ready');
        var empty = id === 'guardMap' ? $('guardMapEmpty') : $('assistantMapState');
        if (empty) empty.hidden = true;
      }
      return state.maps[id];
    }

    function addMapOverlay(map, overlay) {
      if (sdkProvider() === 'baidu') map.addOverlay(overlay);
      else map.add(overlay);
    }

    function removeMapOverlay(id, overlay) {
      var map = state.maps[id]; if (!map || !overlay) return;
      if (sdkProvider() === 'baidu') map.removeOverlay(overlay);
      else map.remove(overlay);
    }

    function convertMapPoints(points) {
      if (!sdkReady() || !Array.isArray(points)) return Promise.reject(new Error('地图服务暂不可用'));
      var output = [];
      var batches = [];
      for (var start = 0; start < points.length; start += 10) batches.push(points.slice(start, start + 10));
      return batches.reduce(function (chain, batch) {
        return chain.then(function () {
          return new Promise(function (resolve, reject) {
            if (sdkProvider() === 'baidu') {
              var input = batch.map(function (item) { return new root.BMapGL.Point(Number(item[0]), Number(item[1])); });
              // Baidu official JSAPI GL coordinate type constants: WGS84=1, BD09=5.
              new root.BMapGL.Convertor().translate(input, 1, 5, function (result) {
                if (!result || result.status !== 0 || !Array.isArray(result.points)) { reject(new Error('地图坐标转换失败')); return; }
                output = output.concat(result.points); resolve();
              });
            } else {
              root.AMap.convertFrom(batch, 'gps', function (status, result) {
                if (status !== 'complete' || !result.locations) { reject(new Error('地图坐标转换失败')); return; }
                output = output.concat(result.locations); resolve();
              });
            }
          });
        });
      }, Promise.resolve()).then(function () { return output; });
    }

    function updateMap(id, item) {
      if (!item) return;
      convertMapPoints([[item.longitude, item.latitude]]).then(function (locations) {
        if (state.location !== item || !locations[0]) return;
        var point = locations[0]; var map = ensureMap(id, point); if (!map) return;
        var objects = state.mapObjects[id] || (state.mapObjects[id] = {});
        if (!objects.marker) {
          objects.marker = sdkProvider() === 'baidu'
            ? new root.BMapGL.Marker(point)
            : new root.AMap.Marker({ position: point, title: '当前位置' });
          addMapOverlay(map, objects.marker);
        } else { objects.marker.setPosition(point); objects.marker.show(); }
        var radius = Math.max(5, Math.min(300, Number(item.accuracy) || 20));
        if (!objects.accuracy) {
          objects.accuracy = sdkProvider() === 'baidu'
            ? new root.BMapGL.Circle(point, radius, { strokeColor: '#176c6b', strokeOpacity: 0.45, strokeWeight: 1, fillColor: '#176c6b', fillOpacity: 0.1 })
            : new root.AMap.Circle({ center: point, radius: radius, strokeColor: '#176c6b', strokeOpacity: 0.45, strokeWeight: 1, fillColor: '#176c6b', fillOpacity: 0.1 });
          addMapOverlay(map, objects.accuracy);
        } else { objects.accuracy.setCenter(point); objects.accuracy.setRadius(radius); objects.accuracy.show(); }
        if (!objects.centered) { if (sdkProvider() === 'baidu') map.centerAndZoom(point, 16); else map.setCenter(point); objects.centered = true; }
      }).catch(function () { if (state.deviceId) text('guardLocationNote', '地图暂时无法显示当前位置'); });
    }

    function updateRouteOnMap(id, points) {
      var map = state.maps[id]; if (!map || !sdkReady() || !points || points.length < 2) return;
      var objects = state.mapObjects[id] || (state.mapObjects[id] = {});
      if (objects.route) removeMapOverlay(id, objects.route);
      convertMapPoints(points).then(function (converted) {
        objects.route = sdkProvider() === 'baidu'
          ? new root.BMapGL.Polyline(converted, { strokeColor: '#176c6b', strokeWeight: 5, strokeOpacity: 0.85 })
          : new root.AMap.Polyline({ path: converted, strokeColor: '#176c6b', strokeWeight: 5, strokeOpacity: 0.85, lineJoin: 'round' });
        addMapOverlay(map, objects.route);
      }).catch(function () { text(id === 'guardMap' ? 'guardLocationNote' : 'assistantRouteSummary', '地图路线暂不可用'); });
    }

    function renderLocation(data) {
      var item = data && (data.location || data);
      var available = Boolean(item && data && data.available !== false && !data.expired);
      var usable = available && item.mode !== 'demo';
      state.location = usable ? item : null;
      tone('guardLocationState', usable ? '正常' : '暂不可用', usable ? 'is-ready' : 'is-warn');
      text('guardLocationNote', usable ? ('精度约 ' + Math.round(Number(item.accuracy) || 0) + ' 米') : '等待有效的位置数据');
      text('guardMode', usable ? '已定位' : '等待定位');
      if ($('guardMode')) $('guardMode').className = 'guard-mode-badge' + (usable ? ' is-live' : '');
      if ($('assistantMapState')) {
        var mapReady = Boolean(usable && sdkReady() && state.mapConfig && state.mapConfig.js_key);
        $('assistantMapState').hidden = mapReady;
        if (!usable) $('assistantMapState').textContent = '等待有效位置';
        else if (!mapReady) $('assistantMapState').textContent = state.mapSdkError || '地图服务暂不可用';
      }
      if ($('guardMapEmpty')) {
        var guardMapReady = Boolean(usable && sdkReady() && state.mapConfig && state.mapConfig.js_key);
        $('guardMapEmpty').hidden = guardMapReady;
        if (!usable) $('guardMapEmpty').textContent = '等待可用的位置和地图服务';
        else if (!guardMapReady) $('guardMapEmpty').textContent = state.mapSdkError || '地图服务暂不可用';
      }
      if (!usable) {
        state.lastReverseCell = '';
        text('guardAddress', '地址暂不可用');
        if (available && item.mode === 'demo') setLocationStatus('模拟位置仅供开发验证，未用于守护页面。');
        ['guardMap', 'assistantMap'].forEach(function (id) {
          var mapObjects = state.mapObjects[id];
          if (mapObjects && mapObjects.marker) mapObjects.marker.hide();
          if (mapObjects && mapObjects.accuracy) mapObjects.accuracy.hide();
          if (mapObjects && mapObjects.route && state.maps[id]) { removeMapOverlay(id, mapObjects.route); mapObjects.route = null; }
        });
        if ($('guardMapEmpty')) { $('guardMapEmpty').hidden = false; $('guardMapEmpty').textContent = '等待可用的位置和地图服务'; }
        if ($('assistantMapState')) { $('assistantMapState').hidden = false; $('assistantMapState').textContent = '等待当前位置'; }
        return;
      }
      if (item.source && item.source !== state.locationProvider) { state.locationProvider = item.source; state.locationStatusMessage = ''; }
      if (state.ownerDevice) renderLocalLocationControl();
      else setLocationStatus('当前已收到有效位置。');
      updateMap('guardMap', item); updateMap('assistantMap', item);
      var cell = Number(item.latitude).toFixed(3) + ',' + Number(item.longitude).toFixed(3);
      if (cell !== state.lastReverseCell) {
        state.lastReverseCell = cell;
        var requestedDeviceId = state.deviceId;
        request('/api/v1/map/reverse-geocode', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId, latitude: item.latitude, longitude: item.longitude }) })
          .then(function (result) { if (state.deviceId === requestedDeviceId) text('guardAddress', result.mode === 'amap' ? (result.address || '地址暂不可用') : '地址暂不可用'); })
          .catch(function () { if (state.deviceId === requestedDeviceId) text('guardAddress', '地址暂不可用'); });
      }
    }

    function renderNavigation(nav) {
      state.navigation = nav || { status: 'IDLE' };
      var status = state.navigation.status || 'IDLE';
      var labels = { IDLE: '未导航', ROUTE_READY: '路线已准备', NAVIGATING: '导航中', ARRIVED: '已到达', OFF_ROUTE: '偏离路线', PAUSED: '已暂停', CANCELLED: '已取消' };
      tone('guardNavState', labels[status] || status, status === 'NAVIGATING' ? 'is-ready' : '');
      var route = state.navigation.route;
      tone('assistantNavState', labels[status] || status, status === 'NAVIGATING' ? 'is-ready' : '');
      if (!route) {
        ['guardMap', 'assistantMap'].forEach(function (id) { var objects = state.mapObjects[id]; if (objects && objects.route && state.maps[id]) { removeMapOverlay(id, objects.route); objects.route = null; } });
        text('guardNavNote', status === 'CANCELLED' ? '路线已取消' : '暂无进行中的路线'); text('assistantRouteSummary', status === 'CANCELLED' ? '路线已取消' : '尚未规划路线'); return;
      }
      if (route.mode !== 'amap') {
        tone('guardNavState', '路线暂不可用', 'is-warn'); text('guardNavNote', '暂时无法获取有效路线'); text('assistantRouteSummary', '暂时无法获取有效步行路线');
        ['guardMap', 'assistantMap'].forEach(function (id) { var objects = state.mapObjects[id]; if (objects && objects.route && state.maps[id]) { removeMapOverlay(id, objects.route); objects.route = null; } });
        return;
      }
      var remain = state.navigation.remaining_m == null ? '' : '，剩余约 ' + Math.round(state.navigation.remaining_m) + ' 米';
      text('guardNavNote', Math.round(route.distance) + ' 米，约 ' + Math.ceil(route.duration / 60) + ' 分钟' + remain);
      text('assistantRouteSummary', Math.round(route.distance) + ' 米，约 ' + Math.ceil(route.duration / 60) + ' 分钟');
      updateRouteOnMap('guardMap', route.polyline || []); updateRouteOnMap('assistantMap', route.polyline || []);
    }

    function textValue(id) { var node = $(id); return node ? node.textContent : ''; }
    function renderVideoStatus(available) {
      state.videoAvailable = Boolean(available);
      if (!available) { state.videoStreaming = false; state.videoConnecting = false; if (state.videoConnectTimer) { root.clearTimeout(state.videoConnectTimer); state.videoConnectTimer = null; } }
      tone('guardVideoState', available ? '画面有更新' : '未连接', available ? 'is-ready' : 'is-warn');
      if (!available && $('guardVideo') && !$('guardVideo').hidden) {
        $('guardVideo').src = ''; $('guardVideo').hidden = true; state.videoStreaming = false;
        if ($('guardVideoPlaceholder')) $('guardVideoPlaceholder').hidden = false;
        if ($('guardStopVideo')) $('guardStopVideo').hidden = true;
      }
      tone('guardVideoBadge', state.videoStreaming ? '实时画面' : (available ? '可查看' : '未连接'), state.videoStreaming || available ? 'guard-mode-badge is-live' : 'guard-mode-badge');
      if ($('guardVideoOverlay')) $('guardVideoOverlay').hidden = !state.videoStreaming;
      if ($('guardStartVideo')) { $('guardStartVideo').disabled = !state.hasDevice || state.videoStreaming || state.videoConnecting; $('guardStartVideo').textContent = available ? '查看画面' : '重新连接'; }
    }

    function renderEnvironment(event) {
      if (!event) return;
      if (event.simulation || event.model_status === 'simulation') return;
      if (event.model_status && event.model_status !== 'ready') { text('guardEnvironmentState', '识别暂不可用'); text('guardEnvironmentDetail', '等待设备提供有效识别结果'); return; }
      var objects = Array.isArray(event.objects) ? event.objects : [];
      var counts = {};
      objects.forEach(function (item) { var raw = item.class || item.object || '目标'; var name = root.QimingCameraSpeech ? root.QimingCameraSpeech.objectName(raw) : raw; counts[name] = (counts[name] || 0) + 1; });
      var summary = Object.keys(counts).map(function (name) { return name + ' ' + counts[name]; }).join(' · ');
      if (event.type === 'obstacle_alert') {
        var warning = event.voice_text || ((event.object || '前方目标') + ' · ' + (event.direction || '前方'));
        text('guardWarningState', warning); text('guardWarningDetail', '刚刚');
        text('guardEnvironmentState', '需要留意'); text('guardEnvironmentDetail', warning);
      } else if (event.type === 'detection_update' || event.type === 'spatial_update') {
        text('guardEnvironmentState', summary || '暂未发现目标'); text('guardEnvironmentDetail', summary ? '识别结果已更新' : '最近一次识别未发现目标');
      }
    }

    function renderDevice(device) {
      if (!device) return;
      text('guardDeviceState', device.online ? '在线' : '离线');
      renderVideoStatus(Boolean(device.camera_online));
      text('guardVideoMeta', device.camera_frame_age_ms == null ? '等待设备上传新画面' : ('最近帧 ' + (device.camera_frame_age_ms < 1000 ? Math.round(device.camera_frame_age_ms) + ' 毫秒前' : (device.camera_frame_age_ms / 1000).toFixed(1) + ' 秒前')));
      var visual = device.visual_summary;
      if (visual && Array.isArray(visual.objects)) renderEnvironment({ type: 'detection_update', objects: visual.objects, model_status: visual.model_status });
      else { text('guardEnvironmentState', '暂无实时识别结果'); text('guardEnvironmentDetail', '设备没有可用的最新识别结果'); }
    }

    async function refresh() {
      if (!state.deviceId) { setDeviceAvailability(false); return; }
      if (state.refreshing) return;
      state.refreshing = true;
      try {
        var results = await Promise.all([
          request('/api/v1/location/latest?device_id=' + encodeURIComponent(state.deviceId)),
          request('/api/v1/navigation/status?device_id=' + encodeURIComponent(state.deviceId)),
          request('/api/v1/devices/status?device_id=' + encodeURIComponent(state.deviceId)),
          request('/api/v1/events?device_id=' + encodeURIComponent(state.deviceId) + '&limit=40')
        ]);
        renderLocation(results[0]); renderNavigation(results[1]); renderDevice(results[2]); renderEvents(results[3]);
        var captured = state.location && Date.parse(state.location.captured_at);
        text('guardUpdated', captured ? '位置更新 ' + new Date(captured).toLocaleTimeString('zh-CN', { hour12: false }) : '等待有效位置');
      } catch (error) {
        tone('guardDeviceState', '连接失败', 'is-warn');
        tone('guardLocationState', '暂不可用', 'is-warn');
        text('guardLocationNote', '暂时无法读取设备位置');
        text('guardAddress', '地址暂不可用');
        renderVideoStatus(false);
        text('guardEnvironmentState', '暂不可用'); text('guardEnvironmentDetail', '暂时无法读取最新识别结果');
        text('guardVoiceStatus', error.message);
      }
      finally { state.refreshing = false; }
    }

    async function loadDevices() {
      if (state.devicesLoading) return;
      state.devicesLoading = true;
      try {
        var devices = await request('/api/v1/guard/devices');
        var selects = [$('guardDeviceSelect'), $('guardDiagnosticDevice')].filter(Boolean);
        selects.forEach(function (select) {
          select.replaceChildren();
          devices.forEach(function (item) { var option = doc.createElement('option'); option.value = item.device_id; option.textContent = cleanName(item.name); select.appendChild(option); });
          if (!devices.length) { var empty = doc.createElement('option'); empty.value = ''; empty.textContent = '暂无已绑定设备'; select.appendChild(empty); select.disabled = true; }
          else { select.disabled = false; }
        });
        if (!devices.length) {
          if (state.watchId !== null && root.navigator.geolocation) root.navigator.geolocation.clearWatch(state.watchId);
          state.watchId = null; state.watchDeviceId = ''; state.locationWatchGeneration += 1; state.locationProvider = '';
          state.deviceId = ''; state.loadedDeviceId = ''; setDeviceAvailability(false); if (state.socket) { state.socket.close(); state.socket = null; } return;
        }
        if (!devices.some(function (item) { return item.device_id === state.deviceId; })) state.deviceId = devices[0].device_id;
        if (state.loadedDeviceId && state.loadedDeviceId !== state.deviceId) {
          if (state.socket) { state.socket.close(); state.socket = null; }
          state.lastReverseCell = ''; state.knownEvents.clear();
          var timeline = $('guardTimeline'); if (timeline) timeline.innerHTML = '<li class="guard-event guard-event-empty"><time>—</time><div><strong>暂无动态</strong><p>设备上线或出现重要变化后会显示在这里。</p></div></li>';
          renderLocation({ available: false }); renderNavigation({ status: 'IDLE' }); renderPoiResults([]); renderVideoStatus(false);
          text('guardEnvironmentState', '等待画面'); text('guardEnvironmentDetail', '收到识别结果后显示'); text('guardWarningState', '暂无预警'); text('guardWarningDetail', '有需要留意的情况时会显示在这里');
        }
        state.loadedDeviceId = state.deviceId;
        selects.forEach(function (select) { select.value = state.deviceId; });
        root.localStorage.setItem('qh_guard_device', state.deviceId);
        var current = devices.find(function (item) { return item.device_id === state.deviceId; });
        state.ownerDevice = Boolean(current && current.owner);
        var nextLocationProvider = current && current.location_source || '';
        if (nextLocationProvider !== state.locationProvider) state.locationStatusMessage = '';
        state.locationProvider = nextLocationProvider;
        setDeviceAvailability(true);
        renderLocalLocationControl();
        maybeAutoStartLocalLocation();
        text('assistantDeviceName', cleanName(current && current.name));
        var sharing = Boolean(current && current.sharing);
        tone('guardShareState', sharing ? '已共享' : '已暂停', sharing ? 'is-ready' : 'is-warn');
        if ($('guardShareToggle')) { $('guardShareToggle').hidden = !current || !current.owner; $('guardShareToggle').textContent = sharing ? '暂停共享' : '开启共享'; $('guardShareToggle').dataset.enabled = String(sharing); }
        await loadMapSdk();
        await refresh(); connect();
      } catch (error) {
        setDeviceAvailability(false);
        tone('guardDeviceState', '暂不可用', 'is-warn');
        text('guardLocationNote', '暂时无法读取设备，请稍后重试。');
        text('guardVoiceStatus', error.message);
      } finally {
        state.devicesLoading = false;
        maybeAutoStartLocalLocation();
      }
    }

    function connect() {
      if (!state.deviceId || !activeSurface()) return;
      if (state.socket) state.socket.close();
      var socket = new root.WebSocket((root.location.protocol === 'https:' ? 'wss://' : 'ws://') + root.location.host + '/ws/v1/guard/' + encodeURIComponent(state.deviceId));
      state.socket = socket;
      socket.onopen = function () { if (state.socket === socket) socket.send(JSON.stringify({ type: 'authenticate', token: token() })); };
      socket.onmessage = function (event) {
        if (state.socket !== socket) return;
        try {
          var data = JSON.parse(event.data);
          if (data.type === 'location_update') renderLocation({ available: true, location: data.location });
          else if (data.type === 'navigation_update') renderNavigation(data.navigation);
          else if (data.type === 'sharing_status') tone('guardShareState', data.enabled ? '已共享' : '已暂停', data.enabled ? 'is-ready' : 'is-warn');
          else if (data.type === 'location_provider') {
            if ((data.enabled ? data.source || '' : '') !== state.locationProvider) state.locationStatusMessage = '';
            state.locationProvider = data.enabled ? data.source || '' : '';
            if (data.source !== 'browser_geolocation' && state.watchId !== null && root.navigator.geolocation) { root.navigator.geolocation.clearWatch(state.watchId); state.watchId = null; state.watchDeviceId = ''; state.locationWatchGeneration += 1; }
            renderLocalLocationControl();
            if (!data.enabled) setLocationStatus('位置来源已停止。');
          }
          else if (data.type === 'device_status' || data.type === 'detection_update' || data.type === 'spatial_update' || data.type === 'obstacle_alert') renderEnvironment(data);
        } catch (_) {}
      };
      socket.onclose = function (event) {
        if (state.socket !== socket) return;
        if (event.code === 4403) { tone('guardShareState', '已暂停', 'is-warn'); return; }
        if (activeSurface()) root.setTimeout(connect, 3000);
      };
    }

    function renderPoiResults(results) {
      var list = $('guardPoiResults');
      if (!list) return;
      list.replaceChildren();
      var mapObjects = state.mapObjects.assistantMap || (state.mapObjects.assistantMap = {});
      (mapObjects.searchMarkers || []).forEach(function (marker) { removeMapOverlay('assistantMap', marker); });
      mapObjects.searchMarkers = [];
      if (!Array.isArray(results) || !results.length) return;
      results.filter(function (poi) { return poi.mode !== 'mock'; }).slice(0, 8).forEach(function (poi, index) {
        var row = doc.createElement('article'); row.className = 'guard-poi-card';
        var copy = doc.createElement('div'); var name = doc.createElement('strong'); name.textContent = poi.name || '附近地点';
        var address = doc.createElement('p'); address.textContent = poi.address || '地址暂不可用';
        var distance = doc.createElement('small'); distance.textContent = poi.distance != null && poi.distance !== '' && Number.isFinite(Number(poi.distance)) ? (Number(poi.distance) >= 1000 ? (Number(poi.distance) / 1000).toFixed(1) + ' 公里' : Math.round(Number(poi.distance)) + ' 米') : '';
        copy.append(name, address, distance);
        var route = doc.createElement('button'); route.type = 'button'; route.className = 'button button-quiet'; route.textContent = '导航到这里';
        route.addEventListener('click', async function () {
          route.disabled = true; route.textContent = '规划中…';
          try {
            var planned = await request('/api/v1/map/walking-route', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId, destination: { latitude: Number(poi.latitude), longitude: Number(poi.longitude), name: poi.name } }) });
            renderNavigation(planned);
            addChat('导航到' + (poi.name || '所选地点'), planned.route && planned.route.mode === 'amap' ? '路线已准备，步行约 ' + Math.ceil(planned.route.duration / 60) + ' 分钟。' : '暂时无法获取真实步行路线。');
          } catch (error) { text('guardVoiceStatus', error.message); }
          finally { route.disabled = false; route.textContent = '导航到这里'; }
        });
        row.append(copy, route); list.appendChild(row);
        updateMap('assistantMap', state.location);
        if (sdkReady() && state.maps.assistantMap && Number.isFinite(Number(poi.longitude)) && Number.isFinite(Number(poi.latitude))) {
          convertMapPoints([[Number(poi.longitude), Number(poi.latitude)]]).then(function (locations) {
            if (!locations[0]) return;
            var poiObjects = state.mapObjects.assistantMap || (state.mapObjects.assistantMap = {});
            poiObjects.searchMarkers = poiObjects.searchMarkers || [];
            var marker = sdkProvider() === 'baidu'
              ? new root.BMapGL.Marker(locations[0])
              : new root.AMap.Marker({ position: locations[0], title: poi.name || '搜索结果', label: { content: String(index + 1), direction: 'top' } });
            poiObjects.searchMarkers.push(marker); addMapOverlay(state.maps.assistantMap, marker);
          }).catch(function () {});
        }
      });
    }

    function addChat(question, answer) {
      var chat = $('guardChat'); if (!chat) return;
      var q = doc.createElement('p'); var a = doc.createElement('p');
      q.textContent = '你：' + question; a.textContent = '助手：' + answer; chat.append(q, a); chat.scrollTop = chat.scrollHeight;
    }
    async function ask(question) {
      if (!question) return;
      if (!state.deviceId) { addChat(question, '请先绑定一台设备，再使用地图助手。'); return; }
      try {
        if (state.ownerDevice && state.watchId === null && state.locationReadyPromise && state.locationReadyDevice === state.deviceId) {
          text('guardVoiceStatus', '正在等待读取本机位置，然后查询附近地点…');
          await state.locationReadyPromise;
        }
        var data = await request('/api/v1/map/assistant', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId, text: question }) });
        var answer = data.reply || '暂时没有可用结果。';
        var calls = Array.isArray(data.calls) ? data.calls : [];
        var searchResult = data.tool === 'search_nearby' || data.tool === 'search_places' ? data.result : null;
        var routeResult = data.result && data.result.route ? data.result : null;
        calls.slice().reverse().some(function (call) {
          if (!call || !call.result) return false;
          var clientResult = call.client_result || call.result;
          if (!searchResult && (call.tool === 'search_nearby' || call.tool === 'search_places')) searchResult = clientResult;
          if (!routeResult && call.tool === 'plan_walking_route' && clientResult.route) routeResult = clientResult;
          return Boolean(searchResult && routeResult);
        });
        if (searchResult) {
          var places = searchResult.mode === 'amap' ? searchResult.results : [];
          renderPoiResults(places);
          if (!places.length && searchResult.mode !== 'amap') answer = '附近地点服务暂不可用，请稍后重试。';
        }
        else if (data.tool === 'get_current_location') renderPoiResults([]);
        if (data.tool === 'cancel_navigation') renderNavigation(data.result);
        calls.forEach(function (call) { if (call && call.tool === 'cancel_navigation' && (call.client_result || call.result)) renderNavigation(call.client_result || call.result); });
        if (data.result && data.result.mode === 'mock') answer = data.tool === 'get_current_location' ? '地址服务暂不可用，无法确认当前街区。' : '地图服务暂不可用，暂时无法提供真实地点或路线。';
        if (routeResult) renderNavigation(routeResult);
        if (routeResult && routeResult.route && routeResult.route.mode !== 'amap') answer = '地图服务暂不可用，无法规划真实步行路线。';
        if (!routeResult && /带我去|导航到|去最近/.test(question) && searchResult && searchResult.results && searchResult.results[0] && searchResult.mode !== 'mock') {
          var poi = searchResult.results[0];
          var route = await request('/api/v1/map/walking-route', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId, destination: { latitude: Number(poi.latitude), longitude: Number(poi.longitude), name: poi.name } }) });
          renderNavigation(route); answer += route.route && route.route.mode === 'amap' ? ' 已生成步行路线。' : ' 暂时无法获取真实步行路线。';
        }
        addChat(question, answer);
        try {
          await request('/api/v1/map/voice/speak', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId, text: answer }) });
          text('guardVoiceStatus', '已发送到佩戴设备播报。');
        } catch (error) {
          text('guardVoiceStatus', '帽端播报失败：' + (error.message || '请检查设备连接'));
        }
      } catch (error) { addChat(question, '暂时无法完成：' + error.message); }
    }

    function finishRecording() {
      var capture = state.audioCapture; if (!capture) return Promise.resolve();
      state.audioCapture = null; root.clearTimeout(capture.timer); capture.processor.onaudioprocess = null;
      try { capture.source.disconnect(); capture.processor.disconnect(); } catch (_) {}
      capture.stream.getTracks().forEach(function (track) { track.stop(); });
      var button = $('guardMic'); if (button) { button.disabled = true; button.textContent = '识别中…'; }
      var length = capture.chunks.reduce(function (sum, part) { return sum + part.length; }, 0);
      var samples = new Float32Array(length); var offset = 0;
      capture.chunks.forEach(function (part) { samples.set(part, offset); offset += part.length; });
      var outputLength = Math.floor(samples.length * 16000 / capture.context.sampleRate);
      if (outputLength < 3200) { if (button) { button.disabled = false; button.textContent = '语音输入'; } return Promise.reject(new Error('录音太短，请再说一次。')); }
      var buffer = new ArrayBuffer(44 + outputLength * 2); var view = new DataView(buffer);
      function write(position, value) { for (var i = 0; i < value.length; i += 1) view.setUint8(position + i, value.charCodeAt(i)); }
      write(0, 'RIFF'); view.setUint32(4, 36 + outputLength * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
      view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, outputLength * 2, true);
      for (var i = 0; i < outputLength; i += 1) {
        var source = i * capture.context.sampleRate / 16000; var left = Math.floor(source); var mix = source - left;
        var sample = (samples[left] || 0) * (1 - mix) + (samples[Math.min(left + 1, samples.length - 1)] || 0) * mix;
        view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, sample)) * 32767, true);
      }
      return root.fetch('/api/v1/map/voice/transcribe?device_id=' + encodeURIComponent(state.deviceId), { method: 'POST', headers: { Authorization: 'Bearer ' + token(), 'Content-Type': 'audio/wav' }, body: new Blob([buffer], { type: 'audio/wav' }) })
        .then(function (response) { return response.json().then(function (result) { if (!response.ok) throw new Error(result.detail || '语音识别失败'); $('guardAssistantInput').value = result.text || ''; text('guardVoiceStatus', '识别完成，请确认文字后再查询。'); }); })
        .finally(function () { return capture.context.close().catch(function () {}); })
        .finally(function () { if (button) { button.disabled = false; button.textContent = '语音输入'; } });
    }

    function bind() {
      [$('guardDeviceSelect'), $('guardDiagnosticDevice')].filter(Boolean).forEach(function (deviceSelect) {
        deviceSelect.onchange = async function () {
          var previousDevice = state.deviceId;
          state.locationWatchGeneration += 1;
          if (state.watchId !== null || state.locationProvider === 'browser_geolocation') {
            try { await stopLocalGeolocation(previousDevice, '已停止旧设备的本机定位。'); }
            catch (error) { setLocationStatus(error.message); return; }
          }
          state.deviceId = deviceSelect.value; root.localStorage.setItem('qh_guard_device', state.deviceId); loadDevices();
        };
      });
      if ($('guardCreateDevice')) $('guardCreateDevice').onclick = async function () { try { var created = await request('/api/v1/guard/devices', { method: 'POST', body: JSON.stringify({ name: '启明辅行设备' }) }); state.deviceId = created.device_id; await loadDevices(); } catch (error) { text('guardLocationNote', error.message); } };
      if ($('guardRefreshLocation')) $('guardRefreshLocation').onclick = refresh;
      if ($('guardEventFilter')) $('guardEventFilter').onchange = function () { renderEvents(state.events); };
      if ($('guardVideoFullscreen')) $('guardVideoFullscreen').onclick = async function () {
        var stage = doc.querySelector('.guard-video-stage');
        try { if (doc.fullscreenElement) await doc.exitFullscreen(); else if (stage && stage.requestFullscreen) await stage.requestFullscreen(); else throw new Error('当前浏览器暂不支持全屏。'); }
        catch (_) { text('guardVideoMeta', '暂时无法进入全屏，请使用浏览器全屏功能。'); }
      };
      if ($('guardUseLocation')) $('guardUseLocation').onclick = async function () {
        var button = $('guardUseLocation'); button.disabled = true; state.locationStarting = true; setLocationStatus('正在请求本机浏览器定位…');
        try {
          if (state.watchId !== null) {
            state.locationStartFailed = false;
            await stopLocalGeolocation(state.watchDeviceId || state.deviceId, '本机定位已停止。'); return;
          }
          if (!['localhost', '127.0.0.1', '::1'].includes(root.location.hostname)) throw new Error('请在运行服务的电脑上打开 localhost 并授权位置。');
          if (!root.navigator.geolocation) throw new Error('当前浏览器不支持系统定位。');
          var deviceId = state.deviceId;
          var startGeneration = state.locationWatchGeneration;
          var position = await new Promise(function (resolve, reject) {
            root.navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 });
          });
          if (deviceId !== state.deviceId) throw new Error('当前设备已切换，请重新启用本机定位。');
          await request('/api/v1/location/provider', { method: 'POST', body: JSON.stringify({ device_id: deviceId, source: 'browser_geolocation', enabled: true }) });
          state.locationProvider = 'browser_geolocation';
          state.locationStartFailed = false;
          state.watchDeviceId = deviceId;
          await request('/api/v1/location/report', { method: 'POST', body: JSON.stringify({ device_id: deviceId, latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy: position.coords.accuracy, speed: position.coords.speed, heading: position.coords.heading, captured_at: new Date(position.timestamp).toISOString(), source: 'browser_geolocation', mode: 'real' }) });
          if (deviceId !== state.deviceId || startGeneration !== state.locationWatchGeneration) {
            await request('/api/v1/location/provider', { method: 'POST', body: JSON.stringify({ device_id: deviceId, source: 'browser_geolocation', enabled: false }) });
            state.locationProvider = '';
            state.watchDeviceId = '';
            throw new Error('当前设备已切换，请重新启用本机定位。');
          }
          if (state.deviceId === deviceId) await refresh();
          var watchGeneration = ++state.locationWatchGeneration;
          state.watchId = root.navigator.geolocation.watchPosition(async function (nextPosition) {
            if (state.locationWatchGeneration !== watchGeneration || state.watchDeviceId !== deviceId || state.deviceId !== deviceId) return;
            try {
              await request('/api/v1/location/report', { method: 'POST', body: JSON.stringify({ device_id: deviceId, latitude: nextPosition.coords.latitude, longitude: nextPosition.coords.longitude, accuracy: nextPosition.coords.accuracy, speed: nextPosition.coords.speed, heading: nextPosition.coords.heading, captured_at: new Date(nextPosition.timestamp).toISOString(), source: 'browser_geolocation', mode: 'real' }) });
              if (state.deviceId === deviceId) await refresh();
            } catch (error) {
              setLocationStatus(error.message);
              try { await stopLocalGeolocation(deviceId); } catch (stopError) { setLocationStatus(stopError.message); }
            }
          }, function (error) {
            setLocationStatus('本机定位暂不可用：' + locationErrorMessage(error));
            if (error && error.code === 1) stopLocalGeolocation(deviceId, '浏览器定位权限已撤销，已停止本机定位。').catch(function (stopError) { setLocationStatus(stopError.message); });
          }, { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 });
          setLocationStatus('本机定位已启动，位置正在同步到当前设备。');
        } catch (error) {
          state.locationStartFailed = true;
          if (state.locationProvider === 'browser_geolocation' && state.watchDeviceId === state.deviceId) {
            try { await stopLocalGeolocation(state.deviceId); } catch (_) { state.locationProvider = ''; }
          }
          setLocationStatus(locationErrorMessage(error));
        } finally {
          state.locationStarting = false;
          renderLocalLocationControl();
        }
      };
      if ($('guardSimulateStart')) $('guardSimulateStart').onclick = async function () { var button = $('guardSimulateStart'); button.disabled = true; try { await request('/api/v1/location/simulate/start', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId }) }); text('guardDiagnosticStatus', '模拟路线已启动。'); } catch (error) { text('guardDiagnosticStatus', error.message); } finally { button.disabled = false; } };
      if ($('guardSimulateStop')) $('guardSimulateStop').onclick = async function () { var button = $('guardSimulateStop'); button.disabled = true; try { await request('/api/v1/location/simulate/stop', { method: 'POST', body: JSON.stringify({ device_id: state.deviceId }) }); text('guardDiagnosticStatus', '模拟定位已停止。'); } catch (error) { text('guardDiagnosticStatus', error.message); } finally { button.disabled = false; } };
      if ($('guardShareToggle')) $('guardShareToggle').onclick = async function () {
        var enabled = $('guardShareToggle').dataset.enabled !== 'true';
        try { await request('/api/v1/sharing/' + (enabled ? 'start' : 'stop'), { method: 'POST', body: JSON.stringify({ device_id: state.deviceId }) }); $('guardShareToggle').dataset.enabled = String(enabled); tone('guardShareState', enabled ? '已共享' : '已暂停', enabled ? 'is-ready' : 'is-warn'); $('guardShareToggle').textContent = enabled ? '暂停共享' : '开启共享'; }
        catch (error) { text('guardLocationNote', error.message); }
      };
      if ($('guardHistoryToggle')) $('guardHistoryToggle').onclick = async function () {
        if (!sdkReady() || !state.maps.guardMap) { text('guardLocationNote', '地图服务暂不可用'); return; }
        try {
          var rows = await request('/api/v1/location/history?device_id=' + encodeURIComponent(state.deviceId) + '&limit=500');
          rows = rows.filter(function (point) { return point.mode !== 'demo'; });
          if (rows.length < 2) { text('guardLocationNote', '暂时没有可查看的有效轨迹。'); return; }
          updateRouteOnMap('guardMap', rows.reverse().map(function (point) { return [Number(point.longitude), Number(point.latitude)]; }));
          text('guardLocationNote', '已显示 ' + rows.length + ' 个有效位置点。');
        } catch (error) { text('guardUpdated', error.message); }
      };
      if ($('guardStartVideo')) $('guardStartVideo').onclick = async function () {
        if (!state.deviceId) { text('guardVideoMeta', '请先绑定设备。'); return; }
        if (!state.videoAvailable) { text('guardVideoMeta', '正在检查设备画面…'); await refresh(); if (!state.videoAvailable) { text('guardVideoMeta', '当前没有新鲜画面，请稍后重试。'); return; } }
        var image = $('guardVideo'); state.videoConnecting = true; image.hidden = false; $('guardStartVideo').disabled = true; text('guardVideoBadge', '连接中…'); text('guardVideoMeta', '正在等待设备上传新画面'); if ($('guardVideoOverlay')) $('guardVideoOverlay').hidden = true; image.src = '/api/v1/video/mjpeg?device_id=' + encodeURIComponent(state.deviceId);
        if (state.videoConnectTimer) root.clearTimeout(state.videoConnectTimer);
        state.videoConnectTimer = root.setTimeout(function () { if (!state.videoStreaming) { image.src = ''; image.hidden = true; renderVideoStatus(false); if ($('guardVideoPlaceholder')) $('guardVideoPlaceholder').hidden = false; text('guardVideoMeta', '设备暂未上传新画面，请稍后重试。'); } }, 7000);
      };
      if ($('guardStopVideo')) $('guardStopVideo').onclick = function () { var image = $('guardVideo'); image.src = ''; image.hidden = true; renderVideoStatus(false); if ($('guardVideoPlaceholder')) $('guardVideoPlaceholder').hidden = false; if ($('guardStartVideo')) $('guardStartVideo').disabled = false; };
      if ($('guardVideo')) { $('guardVideo').onload = function () { if (state.videoConnectTimer) { root.clearTimeout(state.videoConnectTimer); state.videoConnectTimer = null; } state.videoConnecting = false; state.videoStreaming = true; state.videoLastFrameAt = Date.now(); renderVideoStatus(true); if ($('guardVideoPlaceholder')) $('guardVideoPlaceholder').hidden = true; if ($('guardStartVideo')) $('guardStartVideo').disabled = false; if ($('guardStopVideo')) $('guardStopVideo').hidden = false; }; $('guardVideo').onerror = function () { renderVideoStatus(false); if ($('guardVideoPlaceholder')) $('guardVideoPlaceholder').hidden = false; if ($('guardStartVideo')) $('guardStartVideo').disabled = false; if ($('guardStopVideo')) $('guardStopVideo').hidden = true; text('guardVideoMeta', '设备画面连接失败，请稍后重试。'); }; }
      if ($('guardAssistantForm')) $('guardAssistantForm').onsubmit = function (event) { event.preventDefault(); var input = $('guardAssistantInput'); var question = input.value.trim(); input.value = ''; ask(question).catch(function (error) { addChat(question, error.message); }); };
      doc.querySelectorAll('[data-guard-query]').forEach(function (button) { button.onclick = function () { var input = $('guardAssistantInput'); input.value = button.getAttribute('data-guard-query') || ''; if ($('guardAssistantForm')) $('guardAssistantForm').requestSubmit(); }; });
      if ($('guardMic')) $('guardMic').onclick = async function () {
        if (state.audioCapture) { try { await finishRecording(); } catch (error) { text('guardVoiceStatus', error.message); } return; }
        if (!root.navigator.mediaDevices || !root.navigator.mediaDevices.getUserMedia || !root.AudioContext) { text('guardVoiceStatus', '当前浏览器无法录音，请输入文字。'); return; }
        try {
          var stream = await root.navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
          var context = new root.AudioContext(); var source = context.createMediaStreamSource(stream); var processor = context.createScriptProcessor(2048, 1, 1); var chunks = [];
          processor.onaudioprocess = function (event) { chunks.push(new Float32Array(event.inputBuffer.getChannelData(0))); };
          source.connect(processor); processor.connect(context.destination); state.audioCapture = { stream: stream, context: context, source: source, processor: processor, chunks: chunks };
          state.audioCapture.timer = root.setTimeout(function () { finishRecording().catch(function (error) { text('guardVoiceStatus', error.message); }); }, 15000);
          $('guardMic').textContent = '结束录音'; text('guardVoiceStatus', '正在听，请说出目的地或问题。');
        } catch (error) { text('guardVoiceStatus', '无法打开麦克风：' + error.message); }
      };
      root.addEventListener('qiming-guard-activate', function () {
        if (activeSurface()) state.autoLocationAttemptedDevice = '';
        loadDevices();
      });
      root.setInterval(function () { if (activeSurface() && state.deviceId) refresh(); }, 2500);
      root.addEventListener('pagehide', function () {
        if (state.watchId !== null && root.navigator.geolocation) root.navigator.geolocation.clearWatch(state.watchId);
        if (state.watchDeviceId && state.locationProvider === 'browser_geolocation') {
          root.fetch('/api/v1/location/provider', { method: 'POST', headers: headers(), body: JSON.stringify({ device_id: state.watchDeviceId, source: 'browser_geolocation', enabled: false }), keepalive: true }).catch(function () {});
        }
        if (state.socket) state.socket.close();
        if (state.audioCapture) { state.audioCapture.stream.getTracks().forEach(function (track) { track.stop(); }); state.audioCapture.context.close().catch(function () {}); }
      });
      loadDevices();
    }

    bind();
    return { refresh: refresh };
  }

  root.QimingRemoteGuard = { mount: mount };
})(window);
