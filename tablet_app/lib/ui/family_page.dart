import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:material_ui/material_ui.dart';

import '../core/app_controller.dart';
import 'common.dart';

class FamilyAssistPage extends StatefulWidget {
  const FamilyAssistPage({super.key, required this.app});
  final AppController app;
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
  bool _active = true;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 8), (_) {
      if (_active) _refresh(silent: true);
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _active = state == AppLifecycleState.resumed;
    if (_active) _refresh();
  }

  Future<void> _refresh({bool silent = false}) async {
    if (_busy || !widget.app.loggedIn) return;
    if (!silent && mounted) setState(() => _busy = true);
    try {
      final api = widget.app.api!;
      final bindings = await api.request('/api/family/bindings');
      if (bindings is! List) throw const FormatException('家属绑定接口格式不正确。');
      final parsed = bindings
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      if (parsed.isEmpty) {
        if (mounted) {
          setState(() {
            _bindings = const [];
            _overview = null;
            _messages = const [];
            _frame = null;
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
      );
      final messages = await api.request(
        '/api/family/messages?device_id=${Uri.encodeQueryComponent(selected)}',
      );
      Uint8List? frame;
      if (overview['permissions']?['camera'] == true &&
          overview['camera']?['online'] == true) {
        try {
          final bytes = await api.request(
            '/api/family/cameras/$selected/frame',
            responseType: ResponseType.bytes,
            timeout: const Duration(seconds: 5),
          );
          if (bytes is List<int>) frame = Uint8List.fromList(bytes);
        } catch (_) {
          frame = null;
        }
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
          _frame = frame;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted && !silent) setState(() => _error = error.toString());
    } finally {
      if (mounted && !silent) setState(() => _busy = false);
    }
  }

  Future<void> _sendMessage() async {
    final controller = TextEditingController();
    final body = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('联系佩戴者'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 4,
          maxLength: 500,
          decoration: const InputDecoration(hintText: '例如：我看到你的位置了，需要我打电话吗？'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('发送'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (body == null || body.trim().isEmpty || _selectedDevice == null) return;
    setState(() => _sending = true);
    try {
      await widget.app.api!.map(
        '/api/family/messages',
        method: 'POST',
        data: {'device_id': _selectedDevice, 'body': body.trim()},
      );
      await _refresh(silent: true);
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
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

  Future<void> _revokeBinding() async {
    final binding = _bindings.cast<Map<String, dynamic>>().firstWhere(
      (item) => item['device_id'] == _selectedDevice,
      orElse: () => <String, dynamic>{},
    );
    final id = binding['id'];
    if (id == null || _overview?['role'] != 'owner') return;
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
    final role = _overview?['role']?.toString();
    final name = role == 'owner'
        ? (binding?['family_username'])
        : (binding?['owner_username']);
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
          if (value != null) {
            setState(() => _selectedDevice = value);
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
          '还没有授权设备',
          style: TextStyle(fontSize: 21, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 12),
        const Text(
          '请由佩戴者账号在后端创建家属绑定，再使用本账号登录。绑定建立后，家属才能查看画面、位置并发送协助消息。',
          textAlign: TextAlign.center,
          style: TextStyle(color: muted, height: 1.7),
        ),
        const SizedBox(height: 18),
        Wrap(
          alignment: WrapAlignment.center,
          spacing: 12,
          runSpacing: 12,
          children: [
            const StatusPill('默认关闭未授权访问'),
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
      borderRadius: BorderRadius.circular(18),
      border: Border.all(color: const Color(0xff27394e)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: good ? accent : const Color(0xffffd68a), size: 22),
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
                  : camera?['online'] == true
                  ? '画面在线'
                  : '等待画面',
              good:
                  permissions?['camera'] != false && camera?['online'] == true,
            ),
          ],
        ),
        const SizedBox(height: 16),
        AspectRatio(
          aspectRatio: 16 / 10,
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: const Color(0xff091322),
              borderRadius: BorderRadius.circular(14),
            ),
            child: _frame == null
                ? Center(
                    child: Text(
                      permissions?['camera'] == false
                          ? '佩戴者尚未授权远程查看画面'
                          : '暂无实时画面\n请确认帽端在线并已授权查看',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: muted, height: 1.7),
                    ),
                  )
                : ClipRRect(
                    borderRadius: BorderRadius.circular(14),
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
                '更新于 ${timeLabel(camera?['updated_at'])} · 每 8 秒自动刷新',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
            ),
            IconButton.filledTonal(
              onPressed: _busy ? null : _refresh,
              icon: const Icon(Icons.refresh),
              tooltip: '刷新画面',
            ),
          ],
        ),
        Padding(
          padding: const EdgeInsets.only(left: 2, top: 2),
          child: Text(
            '麦克风 ${_overview?['device']?['mic_ok'] == true ? '正常' : '状态未知'}',
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
            FilledButton.icon(
              onPressed: _sending || permissions?['messages'] == false
                  ? null
                  : _sendMessage,
              icon: const Icon(Icons.send_outlined),
              label: Text(
                permissions?['messages'] == false ? '未授权联系' : '联系佩戴者',
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (permissions?['messages'] == false)
          const Text(
            '当前授权不包含文字沟通。',
            style: TextStyle(color: muted, height: 1.7),
          )
        else if (_messages.isEmpty)
          const Text(
            '还没有消息。遇到困难时，可以发送简短的文字协助。',
            style: TextStyle(color: muted, height: 1.7),
          ),
        ..._messages.map(
          (message) => ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const CircleAvatar(child: Icon(Icons.chat_bubble_outline)),
            title: Text(message['body']?.toString() ?? ''),
            subtitle: Text(
              '${message['sender_username'] ?? ''} · ${timeLabel(message['created_at'])}',
            ),
          ),
        ),
      ],
    ),
  );

  Widget _permissionPanel() => Panel(
    padding: const EdgeInsets.fromLTRB(16, 14, 10, 8),
    child: Row(
      children: [
        const Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('家属访问权限', style: TextStyle(fontWeight: FontWeight.w700)),
              SizedBox(height: 4),
              Text(
                '当前账号是佩戴者，可随时撤销家属访问。',
                style: TextStyle(color: muted, fontSize: 12),
              ),
            ],
          ),
        ),
        IconButton(
          tooltip: '撤销家属授权',
          onPressed: _busy ? null : _revokeBinding,
          icon: const Icon(Icons.link_off),
        ),
      ],
    ),
  );
}
