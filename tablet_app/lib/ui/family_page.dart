import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:material_ui/material_ui.dart';

import '../core/app_controller.dart';
import '../core/api.dart';
import '../core/family_voice.dart';
import 'common.dart';

class FamilyAssistPage extends StatefulWidget {
  const FamilyAssistPage({super.key, required this.app, this.voice});
  final AppController app;
  final FamilyVoice? voice;
  @override
  State<FamilyAssistPage> createState() => _FamilyAssistPageState();
}

class _FamilyAssistPageState extends State<FamilyAssistPage>
    with WidgetsBindingObserver {
  List<Map<String, dynamic>> _bindings = const [];
  Map<String, dynamic>? _overview;
  List<Map<String, dynamic>> _messages = const [];
  Uint8List? _frame;
  String? _selectedDevice;
  String? _error;
  bool _busy = false;
  bool _sending = false;
  bool _voiceBusy = false;
  bool _active = true;
  bool _cameraPaused = false;
  bool _frameDenied = false;
  String? _frameError;
  String? _deliveryDetail;
  bool _deliveryQueued = false;
  DateTime? _frameUpdated;
  int _generation = 0;
  int _frameGeneration = 0;
  Timer? _timer;
  Timer? _frameTimer;
  Timer? _frameExpiry;
  CancelToken? _metadataCancel;
  CancelToken? _frameCancel;
  late final FamilyVoice _voice;

  @override
  void initState() {
    super.initState();
    _voice = widget.voice ?? FamilyVoice(transcribe: _transcribeRecording);
    WidgetsBinding.instance.addObserver(this);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 8), (_) {
      if (_active) _refresh(silent: true);
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _active = state == AppLifecycleState.resumed;
    if (_active) {
      _refresh();
      _scheduleFrame(immediate: true);
    } else {
      if (state == AppLifecycleState.paused ||
          state == AppLifecycleState.hidden ||
          state == AppLifecycleState.detached) {
        _cancelNetwork();
        unawaited(_voice.cancel());
      } else {
        // Android permission prompts temporarily make the activity inactive.
        // Keep that voice session valid while pausing camera/network work.
        _metadataCancel?.cancel('应用暂时失去焦点');
        _metadataCancel = null;
        _busy = false;
        _stopFrames();
      }
      if (mounted) setState(() {});
    }
  }

  Future<void> _refresh({bool silent = false}) async {
    if (!mounted ||
        _metadataCancel != null ||
        !_active ||
        !widget.app.loggedIn) {
      return;
    }
    final generation = _generation;
    final token = CancelToken();
    _metadataCancel = token;
    if (!silent && mounted) setState(() => _busy = true);
    try {
      final api = widget.app.api!;
      final bindings = await api.request(
        '/api/family/bindings',
        cancelToken: token,
      );
      if (token.isCancelled || !_isCurrent(generation)) return;
      if (bindings is! List) throw const FormatException('家属绑定接口格式不正确。');
      final parsed = bindings
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      if (parsed.isEmpty) {
        _stopFrames();
        if (mounted) {
          setState(() {
            _bindings = const [];
            _overview = null;
            _messages = const [];
            _selectedDevice = null;
            _deliveryDetail = null;
            _error = null;
          });
        }
        return;
      }
      final selected =
          parsed.any((item) => item['device_id'] == _selectedDevice)
          ? _selectedDevice
          : parsed.first['device_id']?.toString();
      final overview = await api.map(
        '/api/family/overview?device_id=${Uri.encodeQueryComponent(selected!)}',
        cancelToken: token,
      );
      final messages = await api.request(
        '/api/family/messages?device_id=${Uri.encodeQueryComponent(selected)}',
        cancelToken: token,
      );
      if (token.isCancelled || !_isCurrent(generation)) return;
      final deviceChanged = _selectedDevice != selected;
      if (deviceChanged || overview['permissions']?['camera'] != true) {
        _stopFrames();
      }
      if (mounted) {
        setState(() {
          _bindings = parsed;
          _selectedDevice = selected;
          _overview = overview;
          _messages = messages is List
              ? messages
                    .map((item) => Map<String, dynamic>.from(item as Map))
                    .toList()
              : const [];
          if (deviceChanged) {
            _frameError = null;
            _deliveryDetail = null;
          }
          _frameDenied = false;
          _error = null;
        });
        _scheduleFrame(immediate: true);
      }
    } catch (error) {
      if (token.isCancelled || !mounted || generation != _generation) return;
      _stopFrames();
      if (mounted) {
        setState(() {
          _error = error.toString();
          if (error is ApiFailure && [401, 403].contains(error.status)) {
            _overview = null;
            _messages = const [];
          }
        });
      }
    } finally {
      if (identical(_metadataCancel, token)) {
        _metadataCancel = null;
        if (mounted && !silent) setState(() => _busy = false);
      }
    }
  }

  bool _isCurrent(int generation) =>
      mounted && _active && widget.app.loggedIn && generation == _generation;

  bool get _canReadFrame =>
      mounted &&
      _active &&
      widget.app.loggedIn &&
      !_cameraPaused &&
      !_frameDenied &&
      _selectedDevice != null &&
      _overview?['permissions']?['camera'] == true;

  void _stopFrames() {
    _frameGeneration++;
    _frameTimer?.cancel();
    _frameTimer = null;
    _frameExpiry?.cancel();
    _frameExpiry = null;
    _frameCancel?.cancel('画面订阅已结束');
    _frameCancel = null;
    _frame = null;
    _frameUpdated = null;
  }

  void _cancelNetwork() {
    _generation++;
    _metadataCancel?.cancel('页面状态已改变');
    _metadataCancel = null;
    _busy = false;
    _stopFrames();
  }

  void _scheduleFrame({bool immediate = false}) {
    if (!_canReadFrame || _frameCancel != null || _frameTimer != null) return;
    _frameTimer = Timer(
      immediate ? Duration.zero : const Duration(milliseconds: 700),
      () {
        _frameTimer = null;
        unawaited(_readFrame());
      },
    );
  }

  Future<void> _readFrame() async {
    if (!_canReadFrame || _frameCancel != null) return;
    final device = _selectedDevice!;
    final generation = _frameGeneration;
    final token = CancelToken();
    _frameCancel = token;
    bool sameRequest() =>
        mounted &&
        _active &&
        _selectedDevice == device &&
        _frameGeneration == generation &&
        !token.isCancelled;
    bool current() => sameRequest() && _canReadFrame;
    try {
      final bytes = await widget.app.api!.request(
        '/api/family/cameras/${Uri.encodeComponent(device)}/frame',
        responseType: ResponseType.bytes,
        timeout: const Duration(seconds: 3),
        cancelToken: token,
      );
      if (!current()) return;
      if (bytes is! List<int> || bytes.isEmpty) {
        throw const FormatException('画面数据无效，请稍后重试。');
      }
      _frameExpiry?.cancel();
      setState(() {
        _frame = Uint8List.fromList(bytes);
        _frameUpdated = DateTime.now();
        _frameError = null;
      });
      _frameExpiry = Timer(const Duration(seconds: 3), () {
        if (!mounted || generation != _frameGeneration) return;
        setState(() {
          _frame = null;
          _frameUpdated = null;
          _frameError = '画面已中断，正在等待新的摄像头画面';
        });
      });
    } catch (error) {
      if (!sameRequest()) return;
      _frameExpiry?.cancel();
      setState(() {
        _frame = null;
        _frameUpdated = null;
        _frameDenied = error is ApiFailure && [401, 403].contains(error.status);
        _frameError = switch (error) {
          ApiFailure(status: 410) => '摄像头未开启或画面已过期，正在等待新的画面',
          ApiFailure(status: 403) => '画面查看权限已撤销，请刷新授权状态。',
          ApiFailure(status: 401) => '登录已过期，请重新登录后查看画面。',
          _ => error.toString(),
        };
      });
    } finally {
      if (identical(_frameCancel, token)) {
        _frameCancel = null;
        _scheduleFrame();
      }
    }
  }

  void _toggleCamera() {
    setState(() {
      _cameraPaused = !_cameraPaused;
      _stopFrames();
      _frameError = null;
    });
    if (!_cameraPaused) _scheduleFrame(immediate: true);
  }

  Future<void> _voiceMessage() async {
    if (_voiceBusy ||
        _sending ||
        _selectedDevice == null ||
        _overview?['permissions']?['messages'] != true) {
      return;
    }
    final generation = _generation;
    final device = _selectedDevice;
    setState(() => _voiceBusy = true);
    try {
      final text = await showDialog<String>(
        context: context,
        barrierDismissible: false,
        builder: (context) => _VoiceCaptureDialog(voice: _voice),
      );
      if (!_isCurrent(generation) ||
          device != _selectedDevice ||
          text == null) {
        return;
      }
      await _sendMessage(transcript: text);
    } finally {
      if (mounted) setState(() => _voiceBusy = false);
    }
  }

  Future<String> _transcribeRecording(
    Uint8List wav,
    CancelToken cancelToken,
  ) async {
    final device = _selectedDevice;
    if (device == null || !mounted || !_active || !widget.app.loggedIn) {
      throw const FamilyVoiceFailure('cancelled', '本次语音输入已取消。');
    }
    final result = await widget.app.api!.map(
      '/api/family/voice/transcribe?device_id=${Uri.encodeQueryComponent(device)}',
      method: 'POST',
      data: wav,
      contentType: 'audio/wav',
      cancelToken: cancelToken,
      timeout: const Duration(seconds: 90),
    );
    final text = result['text'];
    if (text is! String || text.trim().isEmpty) {
      throw const ApiFailure('未识别到有效语音，请靠近麦克风重新录制。');
    }
    return text;
  }

  Future<void> _sendMessage({String? transcript}) async {
    final device = _selectedDevice;
    final generation = _generation;
    if (device == null ||
        !widget.app.loggedIn ||
        _overview?['permissions']?['messages'] != true) {
      return;
    }
    final voiceMessage = transcript != null;
    final body = await showDialog<String>(
      context: context,
      builder: (context) => _FamilyMessageDialog(
        initialText: transcript ?? '',
        voiceMessage: voiceMessage,
      ),
    );
    if (body == null ||
        body.trim().isEmpty ||
        !_isCurrent(generation) ||
        device != _selectedDevice ||
        _overview?['permissions']?['messages'] != true) {
      return;
    }
    setState(() {
      _sending = true;
      _deliveryDetail = null;
      _error = null;
    });
    try {
      final response = await widget.app.api!.map(
        '/api/family/messages',
        method: 'POST',
        data: {
          'device_id': device,
          'body': body.trim(),
          'message_type': voiceMessage ? 'voice_transcript' : 'text',
        },
      );
      if (!_isCurrent(generation) || device != _selectedDevice) return;
      final delivery = response['delivery'] as Map?;
      setState(() {
        _deliveryQueued = delivery?['status'] == 'queued';
        _deliveryDetail = delivery?['detail']?.toString() ?? '消息已保存，请查看帽端连接状态。';
      });
      await _refresh(silent: true);
    } catch (error) {
      if (_isCurrent(generation)) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _addBinding() async {
    final deviceController = TextEditingController(
      text: _bindings.isEmpty ? 'esp32s3_cam_001' : _selectedDevice,
    );
    final familyController = TextEditingController();
    final values = await showDialog<List<String>>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('授权家属账号'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: deviceController,
              decoration: const InputDecoration(labelText: '帽端设备编号'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: familyController,
              decoration: const InputDecoration(labelText: '家属账号'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, [
              deviceController.text,
              familyController.text,
            ]),
            child: const Text('授权'),
          ),
        ],
      ),
    );
    deviceController.dispose();
    familyController.dispose();
    if (values == null ||
        values.length != 2 ||
        values.any((value) => value.trim().isEmpty)) {
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.app.api!.map(
        '/api/family/bindings',
        method: 'POST',
        data: {
          'device_id': values[0].trim(),
          'family_username': values[1].trim(),
        },
      );
      if (mounted) setState(() => _busy = false);
      await _refresh();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _registerDevice() async {
    final device = await showDialog<String>(
      context: context,
      builder: (context) => const _RegisterDeviceDialog(),
    );
    if (device == null || !mounted || !_active || !widget.app.loggedIn) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.app.api!.map(
        '/api/family/devices',
        method: 'POST',
        data: {'device_id': device},
      );
      if (!mounted) return;
      _cancelNetwork();
      setState(() {
        _selectedDevice = device;
        _overview = null;
        _messages = const [];
        _deliveryDetail = null;
        _frameError = null;
      });
      await _refresh();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  bool _isOwnerDevice(Map<String, dynamic> binding) {
    if (binding['is_owner_device'] == true) return true;
    final owner = binding['owner_username']?.toString().trim().toLowerCase();
    final family = binding['family_username']?.toString().trim().toLowerCase();
    return owner != null && owner.isNotEmpty && owner == family;
  }

  List<Map<String, dynamic>> get _familyGrants => _bindings
      .where(
        (binding) =>
            binding['device_id'] == _selectedDevice && !_isOwnerDevice(binding),
      )
      .toList();

  Future<void> _revokeBinding(Map<String, dynamic> binding) async {
    final id = binding['id'];
    if (id == null ||
        _isOwnerDevice(binding) ||
        binding['device_id'] != _selectedDevice ||
        _overview?['role'] != 'owner') {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('撤销家属访问？'),
        content: Text('撤销后，${binding['family_username']} 将无法继续查看此设备。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('撤销授权'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _busy = true);
    try {
      await widget.app.api!.request(
        '/api/family/bindings/$id',
        method: 'DELETE',
      );
      if (mounted) setState(() => _busy = false);
      await _refresh();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _cancelNetwork();
    if (widget.voice == null) {
      _voice.dispose();
    } else {
      unawaited(_voice.cancel());
    }
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final overview = _overview;
    final device = overview?['device'] as Map?;
    final location = overview?['location'] as Map?;
    final camera = overview?['camera'] as Map?;
    final permissions = overview?['permissions'] as Map?;
    final binding = _selectedBinding;
    final wearer = _wearerLabel(binding);
    final deviceBindings = <String, Map<String, dynamic>>{};
    for (final binding in _bindings) {
      final id = binding['device_id']?.toString();
      if (id != null) deviceBindings.putIfAbsent(id, () => binding);
    }
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
        children: [
          _topBar(wearer, device),
          if (_error != null) Notice(_error!),
          if (_bindings.isEmpty)
            _emptyState()
          else ...[
            Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                onPressed: _busy ? null : _registerDevice,
                icon: const Icon(Icons.add_rounded),
                label: const Text('添加我的帽端'),
              ),
            ),
            const SizedBox(height: 12),
            if (deviceBindings.length > 1) ...[
              _devicePicker(deviceBindings),
              const SizedBox(height: 12),
            ],
            _cameraPanel(camera, permissions),
            const SizedBox(height: 12),
            _quickStatus(
              device,
              location,
              overview?['current_alert'] as Map?,
              permissions,
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(4, 8, 4, 0),
              child: Text(
                '设备状态仅反映连接与上报情况，不代表已确认佩戴者的人身安全。',
                style: TextStyle(color: muted, fontSize: 12, height: 1.4),
              ),
            ),
            const SizedBox(height: 12),
            _messagePanel(permissions),
            if (_overview?['role'] == 'owner') ...[
              const SizedBox(height: 12),
              _permissionPanel(),
            ],
          ],
        ],
      ),
    );
  }

  Map<String, dynamic>? get _selectedBinding {
    for (final item in _bindings) {
      if (item['device_id']?.toString() == _selectedDevice) return item;
    }
    return _bindings.isEmpty ? null : _bindings.first;
  }

  String _wearerLabel(Map<String, dynamic>? binding) {
    final name = binding?['owner_username'];
    return name?.toString().trim().isNotEmpty == true ? name.toString() : '佩戴者';
  }

  Widget _topBar(String wearer, Map? device) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
            color: accent.withValues(alpha: .14),
            borderRadius: BorderRadius.circular(14),
          ),
          child: const Icon(Icons.family_restroom_outlined, color: accent),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '家属远程协助',
                style: TextStyle(fontSize: 23, fontWeight: FontWeight.w700),
              ),
              Text(
                '$wearer · 帽端 ${device?['online'] == true ? '在线' : '离线'}',
                style: const TextStyle(color: muted, fontSize: 13),
              ),
            ],
          ),
        ),
        IconButton(
          tooltip: '刷新状态',
          onPressed: _busy ? null : _refresh,
          icon: const Icon(Icons.refresh_rounded),
        ),
      ],
    ),
  );

  Widget _devicePicker(Map<String, Map<String, dynamic>> devices) =>
      DropdownButtonFormField<String>(
        initialValue: _selectedDevice,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: '查看设备',
          prefixIcon: Icon(Icons.devices_other_outlined),
        ),
        items: devices.entries.map((entry) {
          final binding = entry.value;
          return DropdownMenuItem(
            value: entry.key,
            child: Text(
              '${binding['owner_username'] ?? '佩戴者'} · ${entry.key}',
              overflow: TextOverflow.ellipsis,
            ),
          );
        }).toList(),
        onChanged: (value) {
          if (value != null && value != _selectedDevice) {
            _cancelNetwork();
            unawaited(_voice.cancel());
            setState(() {
              _selectedDevice = value;
              _overview = null;
              _messages = const [];
              _deliveryDetail = null;
              _frameError = null;
            });
            _refresh();
          }
        },
      );

  Widget _emptyState() => Panel(
    child: Column(
      children: [
        const Icon(Icons.family_restroom_outlined, color: accent, size: 52),
        const SizedBox(height: 16),
        const Text(
          '还没有可访问的帽端',
          style: TextStyle(fontSize: 21, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 12),
        const Text(
          '添加自己的帽端后，即可查看画面与发送留言。需要家属协助时，再授权其他已注册账号。',
          textAlign: TextAlign.center,
          style: TextStyle(color: muted, height: 1.7),
        ),
        const SizedBox(height: 18),
        Wrap(
          alignment: WrapAlignment.center,
          spacing: 12,
          runSpacing: 12,
          children: [
            FilledButton.icon(
              onPressed: _busy ? null : _registerDevice,
              icon: const Icon(Icons.add_rounded),
              label: const Text('添加我的帽端'),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : _addBinding,
              icon: const Icon(Icons.person_add_alt_1),
              label: const Text('我是佩戴者，授权家属'),
            ),
          ],
        ),
      ],
    ),
  );

  Widget _quickStatus(
    Map? device,
    Map? location,
    Map? alert,
    Map? permissions,
  ) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Expanded(
        child: _statusTile(
          icon: Icons.location_on_outlined,
          title: '当前位置',
          value: permissions?['location'] == false
              ? '未授权'
              : location?['available'] == true
              ? '${location?['latitude']}, ${location?['longitude']}'
              : '暂无位置',
          detail: permissions?['location'] == false
              ? '等待佩戴者授权'
              : location?['available'] == true
              ? 'Wi-Fi 粗略估算 · 更新于 ${timeLabel(location?['updated_at'])}'
              : location?['detail']?.toString() ?? 'Wi-Fi 定位暂不可用',
          good:
              permissions?['location'] != false &&
              location?['available'] == true,
        ),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: _statusTile(
          icon: alert == null ? Icons.shield_outlined : Icons.warning_amber,
          title: '当前预警',
          value: alert == null ? '一切正常' : '需要留意',
          detail: alert == null
              ? '帽端最近心跳 ${timeLabel(device?['last_heartbeat'])}'
              : '${objectLabel(alert['object_class'])} · ${directionLabel(alert['direction'])}',
          good: alert == null,
        ),
      ),
    ],
  );

  Widget _statusTile({
    required IconData icon,
    required String title,
    required String value,
    required String detail,
    required bool good,
  }) => Container(
    padding: const EdgeInsets.all(15),
    decoration: BoxDecoration(
      color: surface,
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: lineColor),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: good ? successInk : warningInk, size: 22),
        const SizedBox(height: 10),
        Text(title, style: const TextStyle(color: muted, fontSize: 12)),
        const SizedBox(height: 4),
        Text(
          value,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 5),
        Text(
          detail,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(color: muted, fontSize: 11, height: 1.35),
        ),
      ],
    ),
  );

  Widget _cameraPanel(Map? camera, Map? permissions) => Panel(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Expanded(
              child: Text(
                '实时视觉协助',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
              ),
            ),
            StatusPill(
              permissions?['camera'] == false
                  ? '未授权'
                  : _cameraPaused
                  ? '已暂停'
                  : _frame != null
                  ? '画面在线'
                  : '等待画面',
              good: permissions?['camera'] != false && _frame != null,
            ),
          ],
        ),
        const SizedBox(height: 16),
        AspectRatio(
          aspectRatio: 16 / 10,
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: _frame == null ? blueSoft : Colors.black,
              borderRadius: BorderRadius.circular(8),
            ),
            child: _frame == null
                ? Center(
                    child: Text(
                      permissions?['camera'] == false
                          ? '佩戴者尚未授权远程查看画面'
                          : _cameraPaused
                          ? '画面已暂停\n点击继续观看后获取新画面'
                          : _frameError ?? '暂无实时画面\n请确认帽端在线并已授权查看',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted, height: 1.7),
                    ),
                  )
                : ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: Image.memory(
                      _frame!,
                      fit: BoxFit.contain,
                      gaplessPlayback: true,
                      semanticLabel: '帽端实时摄像头画面',
                    ),
                  ),
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: Text(
                _cameraPaused
                    ? '观看已暂停'
                    : _frameUpdated == null
                    ? '正在等待画面 · 连续获取最新帧'
                    : '更新于 ${timeLabel(_frameUpdated?.toIso8601String())}',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
            ),
            IconButton.filledTonal(
              onPressed: permissions?['camera'] == true ? _toggleCamera : null,
              icon: Icon(_cameraPaused ? Icons.play_arrow : Icons.pause),
              tooltip: _cameraPaused ? '继续观看' : '暂停观看',
            ),
          ],
        ),
        Padding(
          padding: const EdgeInsets.only(left: 2, top: 2),
          child: Text(
            '画面约每 0.7 秒获取一次，实际速度取决于帽端上传与网络。',
            style: const TextStyle(color: muted, fontSize: 12),
          ),
        ),
      ],
    ),
  );

  Widget _messagePanel(Map? permissions) => Panel(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Expanded(
              child: Text(
                '远程沟通',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
              ),
            ),
            const StatusPill('合成语音'),
          ],
        ),
        const SizedBox(height: 12),
        if (permissions?['messages'] != true)
          const Text(
            '当前授权不包含远程留言，请联系佩戴者开启权限。',
            style: TextStyle(color: muted, height: 1.7),
          )
        else ...[
          const Text(
            '语音识别后由帽端合成播报。请先确认文字，再发送给佩戴者。',
            style: TextStyle(color: bodyText, height: 1.6),
          ),
          const SizedBox(height: 8),
          Text(
            _overview?['speech']?['detail']?.toString() ??
                (_overview?['device']?['online'] == true
                    ? '帽端播放能力等待后端确认。'
                    : '帽端离线，留言可保存，暂无法播报。'),
            style: const TextStyle(color: muted, fontSize: 12, height: 1.5),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              FilledButton.icon(
                onPressed: _sending || _voiceBusy ? null : _voiceMessage,
                icon: const Icon(Icons.mic_none_rounded),
                label: Text(_voiceBusy ? '正在语音输入' : '语音留言'),
              ),
              OutlinedButton.icon(
                onPressed: _sending || _voiceBusy ? null : _sendMessage,
                icon: const Icon(Icons.edit_note_outlined),
                label: Text(_sending ? '正在发送' : '联系佩戴者'),
              ),
            ],
          ),
          if (_deliveryDetail != null) ...[
            const SizedBox(height: 12),
            Semantics(
              liveRegion: true,
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: _deliveryQueued ? successSurface : warningSurface,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  _deliveryDetail!,
                  style: TextStyle(
                    color: _deliveryQueued ? successInk : warningInk,
                    height: 1.5,
                  ),
                ),
              ),
            ),
          ],
          if (_messages.isEmpty) ...[
            const SizedBox(height: 16),
            const Text('还没有留言。', style: TextStyle(color: muted)),
          ],
        ],
        ..._messages.map(
          (message) => ListTile(
            contentPadding: EdgeInsets.zero,
            leading: CircleAvatar(
              child: Icon(
                message['message_type'] == 'voice_transcript'
                    ? Icons.mic_none_rounded
                    : Icons.chat_bubble_outline,
              ),
            ),
            title: Text(message['body']?.toString() ?? ''),
            subtitle: Text(
              '${message['sender_username'] ?? ''} · '
              '${message['message_type'] == 'voice_transcript' ? '语音转文字 · ' : ''}'
              '${timeLabel(message['created_at'])}',
            ),
          ),
        ),
      ],
    ),
  );

  Widget _permissionPanel() => Panel(
    padding: const EdgeInsets.all(16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('家属访问权限', style: TextStyle(fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(
          _familyGrants.isEmpty
              ? '此帽端属于当前账号，可授权其他家属查看画面与发送留言。'
              : '此帽端属于当前账号，可管理以下家属的访问权限。',
          style: const TextStyle(color: muted, fontSize: 12, height: 1.6),
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _busy ? null : _addBinding,
          icon: const Icon(Icons.person_add_alt_1),
          label: const Text('授权其他家属'),
        ),
        ..._familyGrants.map(
          (binding) => ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(binding['family_username']?.toString() ?? '家属账号'),
            trailing: IconButton(
              tooltip: '撤销家属授权',
              onPressed: _busy ? null : () => _revokeBinding(binding),
              icon: const Icon(Icons.link_off),
            ),
          ),
        ),
      ],
    ),
  );
}

class _RegisterDeviceDialog extends StatefulWidget {
  const _RegisterDeviceDialog();

  @override
  State<_RegisterDeviceDialog> createState() => _RegisterDeviceDialogState();
}

class _RegisterDeviceDialogState extends State<_RegisterDeviceDialog> {
  final _controller = TextEditingController(text: 'esp32s3_cam_001');

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('添加我的帽端'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            '填写帽端固件中的设备编号，登记到当前账号。设备在线后即可读取画面与发送留言。',
            style: TextStyle(color: bodyText, height: 1.6),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            autofocus: true,
            maxLength: 80,
            decoration: const InputDecoration(labelText: '帽端设备编号'),
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: _controller.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _controller.text.trim()),
        child: const Text('添加'),
      ),
    ],
  );
}

class _FamilyMessageDialog extends StatefulWidget {
  const _FamilyMessageDialog({
    required this.initialText,
    required this.voiceMessage,
  });
  final String initialText;
  final bool voiceMessage;

  @override
  State<_FamilyMessageDialog> createState() => _FamilyMessageDialogState();
}

class _FamilyMessageDialogState extends State<_FamilyMessageDialog> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.voiceMessage ? '确认语音留言' : '联系佩戴者'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.voiceMessage
                ? '请检查或修改识别文字。发送后由帽端合成语音播报。'
                : '请输入简短的协助留言，在线且支持播报的帽端会加入语音队列。',
            style: const TextStyle(color: bodyText, height: 1.5),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            autofocus: !widget.voiceMessage,
            minLines: 3,
            maxLines: 5,
            maxLength: 500,
            decoration: const InputDecoration(
              labelText: '留言内容',
              hintText: '例如：前面路口先停一下，我帮你确认方向。',
            ),
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: _controller.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _controller.text.trim()),
        child: Text(widget.voiceMessage ? '确认并发送' : '发送'),
      ),
    ],
  );
}

class _VoiceCaptureDialog extends StatefulWidget {
  const _VoiceCaptureDialog({required this.voice});
  final FamilyVoice voice;

  @override
  State<_VoiceCaptureDialog> createState() => _VoiceCaptureDialogState();
}

class _VoiceCaptureDialogState extends State<_VoiceCaptureDialog>
    with WidgetsBindingObserver {
  String? _error;
  bool _listening = true;
  int _attempt = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(_listen());
  }

  Future<void> _listen() async {
    final attempt = ++_attempt;
    if (mounted) {
      setState(() {
        _listening = true;
        _error = null;
      });
    }
    try {
      final available = await widget.voice.available();
      if (!mounted || attempt != _attempt || !_listening) return;
      if (!available) {
        throw const FamilyVoiceFailure(
          'unavailable',
          '当前设备无法启动录音，请检查麦克风或使用文字留言。',
        );
      }
      final text = await widget.voice.listen();
      if (mounted && attempt == _attempt) {
        _listening = false;
        Navigator.pop(context, text);
      }
    } on FamilyVoiceFailure catch (error) {
      if (!mounted || attempt != _attempt) return;
      if (error.code == 'cancelled') {
        _listening = false;
        Navigator.pop(context);
        return;
      }
      setState(() {
        _listening = false;
        _error = error.message;
      });
    } catch (error) {
      if (!mounted || attempt != _attempt) return;
      setState(() {
        _listening = false;
        _error = error is ApiFailure
            ? error.message
            : '录音或识别失败，请检查麦克风与电脑连接后重试。';
      });
    }
  }

  @override
  void dispose() {
    _attempt++;
    WidgetsBinding.instance.removeObserver(this);
    if (_listening) unawaited(widget.voice.cancel());
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      _attempt++;
      _listening = false;
      unawaited(widget.voice.cancel());
      if (mounted) Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<bool>(
    valueListenable: widget.voice.transcribing,
    builder: (context, transcribing, _) => AlertDialog(
      title: Text(
        !_listening
            ? '语音输入暂不可用'
            : transcribing
            ? '电脑正在识别'
            : '请说出协助内容',
      ),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              _listening ? Icons.mic_none_rounded : Icons.mic_off_outlined,
              color: _listening ? accent : warningInk,
              size: 48,
            ),
            const SizedBox(height: 16),
            Semantics(
              liveRegion: true,
              child: Text(
                _error ??
                    (transcribing
                        ? '录音已结束，正在由电脑识别。识别完成后请检查和修改文字。'
                        : '正在录音，请靠近麦克风。说完后点击结束，或静音片刻自动结束。'),
                style: const TextStyle(color: bodyText, height: 1.6),
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '录音由电脑识别，结束后确认发送。每次最长 20 秒。',
              style: TextStyle(color: muted, fontSize: 12, height: 1.5),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () {
            _attempt++;
            _listening = false;
            unawaited(widget.voice.cancel());
            Navigator.pop(context);
          },
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: transcribing
              ? null
              : _listening
              ? widget.voice.stop
              : _listen,
          child: Text(
            !_listening
                ? '重新录音'
                : transcribing
                ? '正在识别'
                : '结束录音',
          ),
        ),
      ],
    ),
  );
}
