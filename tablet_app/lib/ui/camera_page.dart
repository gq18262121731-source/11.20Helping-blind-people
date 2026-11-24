import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:dio/dio.dart';
import 'package:material_ui/material_ui.dart';

import '../core/api.dart';
import '../core/app_controller.dart';
import '../core/camera_feeds.dart';
import '../core/text_reader.dart';
import 'common.dart';

class CameraPage extends StatefulWidget {
  const CameraPage({super.key, required this.app});
  final AppController app;
  @override
  State<CameraPage> createState() => _CameraPageState();
}

class _CameraPageState extends State<CameraPage> with WidgetsBindingObserver {
  CameraController? _camera;
  List<CameraDescription> _cameras = [];
  int _cameraIndex = 0;
  int _cameraGeneration = 0;
  bool _opening = false;
  bool _taking = false;
  bool _uploading = false;
  bool _alertsEnabled = false;
  bool _active = true;
  bool _switchingSource = false;
  int _seq = 0;
  int _frameGeneration = 0;
  Timer? _frameTimer;
  Timer? _staleTimer;
  CancelToken? _uploadCancel;
  String? _error;
  Map<String, dynamic>? _detection;
  DateTime? _received;
  late String _source;
  late final TextReader _reader;
  late final CameraFeeds _feeds;
  StreamSubscription<Map<String, dynamic>>? _events;
  final _sources = <String>{'local_camera'};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _source = widget.app.sourceId;
    _sources.add(_source);
    _feeds = CameraFeeds(widget.app.api!, widget.app.sourceId)..start();
    _reader = TextReader(
      widget.app.api!,
      widget.app.speech,
      autoRead: () => widget.app.preferences.autoRead,
    );
    _events = widget.app.live!.events.listen(_event);
    _staleTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (_received != null &&
          DateTime.now().difference(_received!).inSeconds > 15 &&
          mounted) {
        setState(() {
          _detection = null;
          _received = null;
        });
      }
    });
    _loadSources();
  }

  bool get _isLocal => _source == widget.app.sourceId;

  Future<void> _selectSource(String value) async {
    if (value == _source || _switchingSource) return;
    setState(() {
      _switchingSource = true;
      _source = value;
      _error = null;
    });
    _feeds.select(value);
    await _close();
    if (mounted) {
      setState(() {
        _reader.text = '';
        _reader.error = null;
        _reader.status = '对准文字后拍照，识别结果会显示在这里。';
        _switchingSource = false;
      });
    }
  }

  Future<void> _loadSources() async {
    try {
      final devices = await widget.app.api!.request('/api/tablet/devices');
      if (mounted && devices is List) {
        setState(() {
          _sources.addAll(devices.map((d) => d['device_id'].toString()));
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
  }

  void _event(Map<String, dynamic> event) {
    if (!mounted || !_active) return;
    final id = event['device_id']?.toString();
    if (id != null && event['type'] == 'device_status') {
      setState(() => _sources.add(id));
    }
    if (id != _source) return;
    if (event['type'] == 'detection_update') {
      setState(() {
        _detection = event;
        _received = DateTime.now();
      });
    }
    if (event['type'] == 'obstacle_alert' &&
        _alertsEnabled &&
        !_reader.busy &&
        _reader.text.isEmpty) {
      final simulated = _source.startsWith('simulator_');
      unawaited(
        widget.app.speech.speak(
          '${simulated ? '模拟提醒，' : ''}${directionLabel(event['direction'])}有${objectLabel(event['object'])}',
          alert: true,
        ),
      );
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // The permission dialog temporarily makes the app inactive. Allow the
    // first permission grant to finish opening the camera.
    if (state == AppLifecycleState.inactive && _opening) return;
    _active = state == AppLifecycleState.resumed;
    _feeds.setActive(_active);
    if (!_active) unawaited(_close());
    // Require an explicit reopen after returning; never resume camera capture silently.
  }

  Future<void> _open({bool next = false}) async {
    if (_opening || !_active || !_isLocal || _switchingSource) return;
    await _close();
    if (!mounted || !_active) return;
    setState(() {
      _opening = true;
      _error = null;
    });
    final generation = ++_cameraGeneration;
    CameraController? candidate;
    try {
      _cameras = await availableCameras().timeout(const Duration(seconds: 12));
      if (_cameras.isEmpty) throw const ApiFailure('没有找到可用摄像头。');
      if (next) {
        _cameraIndex = (_cameraIndex + 1) % _cameras.length;
      } else {
        final back = _cameras.indexWhere(
          (c) => c.lensDirection == CameraLensDirection.back,
        );
        if (back >= 0) _cameraIndex = back;
      }
      candidate = CameraController(
        _cameras[_cameraIndex],
        ResolutionPreset.high,
        enableAudio: false,
      );
      await candidate.initialize().timeout(const Duration(seconds: 20));
      if (!mounted || !_active || generation != _cameraGeneration) {
        await candidate.dispose();
        return;
      }
      setState(() => _camera = candidate);
    } on CameraException catch (error) {
      await candidate?.dispose();
      if (mounted && generation == _cameraGeneration) {
        setState(
          () => _error = error.code.contains('Access')
              ? '摄像头权限未授予，请在 Android 设置 → 应用 → 启明帽中允许摄像头。'
              : '摄像头无法启动：${error.description ?? error.code}',
        );
      }
    } catch (error) {
      await candidate?.dispose();
      if (mounted && generation == _cameraGeneration) {
        setState(() => _error = error.toString());
      }
    } finally {
      if (mounted && generation == _cameraGeneration) {
        setState(() => _opening = false);
      }
    }
  }

  void _stopFrames() {
    _frameGeneration++;
    _frameTimer?.cancel();
    _uploadCancel?.cancel();
    _uploading = false;
  }

  Future<void> _close() async {
    _cameraGeneration++;
    _stopFrames();
    _reader.cancel();
    final camera = _camera;
    _camera = null;
    if (mounted) {
      setState(() {
        _opening = false;
        _detection = null;
        _received = null;
      });
    }
    try {
      await camera?.dispose();
    } catch (_) {
      /* Camera can already be released by OS. */
    }
  }

  Future<Uint8List?> _photo() async {
    if (!_isLocal) {
      final image = _feeds.image;
      return image == null ? null : Uint8List.fromList(image);
    }
    final camera = _camera;
    if (_taking || camera == null || !camera.value.isInitialized) return null;
    setState(() => _taking = true);
    XFile? file;
    try {
      file = await camera.takePicture().timeout(const Duration(seconds: 15));
      return await file.readAsBytes();
    } finally {
      if (file != null) {
        try {
          await File(file.path).delete();
        } catch (_) {
          /* Temporary cleanup only. */
        }
      }
      if (mounted) setState(() => _taking = false);
    }
  }

  Future<void> _readText() async {
    _stopFrames();
    setState(() => _error = null);
    final generation = _cameraGeneration;
    try {
      final bytes = await _photo();
      if (bytes == null ||
          !mounted ||
          !_active ||
          generation != _cameraGeneration) {
        return;
      }
      await _reader.recognize(bytes);
    } catch (error) {
      if (mounted && generation == _cameraGeneration) {
        setState(() => _error = '拍照失败：$error');
      }
    }
  }

  void _startFrames() {
    if (!_isLocal || _camera == null || _reader.busy) return;
    _reader.cancel();
    _stopFrames();
    setState(() {
      _source = widget.app.sourceId;
      _uploading = true;
      _error = null;
      _detection = null;
    });
    _frame(_frameGeneration);
  }

  Future<void> _frame(int generation) async {
    if (!mounted || !_uploading || !_active || generation != _frameGeneration) {
      return;
    }
    try {
      if (!widget.app.live!.connected) {
        throw const ApiFailure('实时通道未连接，已暂停上传。连接恢复后请重新开始检测。');
      }
      final bytes = await _photo();
      if (!mounted || generation != _frameGeneration) return;
      if (bytes != null) {
        if (bytes.length >= 2_000_000) {
          throw const ApiFailure('检测照片超过 2 MB，请缩小画面复杂度后重试。');
        }
        _uploadCancel = CancelToken();
        await widget.app.api!.map(
          '/api/tablet/frame?source_id=${widget.app.sourceId}&seq=${++_seq}',
          method: 'POST',
          data: bytes,
          cancelToken: _uploadCancel,
        );
      }
    } catch (error) {
      if (mounted && generation == _frameGeneration) {
        _stopFrames();
        setState(() => _error = error.toString());
      }
      return;
    }
    if (mounted && generation == _frameGeneration && _uploading) {
      _frameTimer = Timer(
        const Duration(milliseconds: 1000),
        () => _frame(generation),
      );
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _cameraGeneration++;
    _stopFrames();
    _events?.cancel();
    _staleTimer?.cancel();
    _feeds.dispose();
    _reader.dispose();
    _camera?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: Listenable.merge([_reader, widget.app.speech, _feeds]),
    builder: (context, _) {
      final ready = _camera?.value.isInitialized ?? false;
      final captureReady =
          !_switchingSource &&
          (_isLocal ? ready : _feeds.watching && _feeds.image != null);
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const PageHeading('实时环境监测', '拍照识字、中文朗读与实时检测'),
          if (_error != null) Notice(_error!),
          if (widget.app.speech.error != null) Notice(widget.app.speech.error!),
          Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _sourceSelector(),
                if (_feeds.sourcesError != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Notice(_feeds.sourcesError!),
                  ),
                const SizedBox(height: 16),
                if (_isLocal)
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      FilledButton.icon(
                        onPressed: _opening || ready || _switchingSource
                            ? null
                            : _open,
                        icon: const Icon(Icons.videocam_outlined),
                        label: Text(_opening ? '正在打开…' : '打开摄像头'),
                      ),
                      OutlinedButton.icon(
                        onPressed: ready && !_taking && !_reader.busy
                            ? () => _open(next: true)
                            : null,
                        icon: const Icon(Icons.cameraswitch_outlined),
                        label: const Text('切换镜头'),
                      ),
                      OutlinedButton.icon(
                        onPressed: ready || _opening ? _close : null,
                        icon: const Icon(Icons.videocam_off_outlined),
                        label: const Text('关闭摄像头'),
                      ),
                    ],
                  ),
                if (!_isLocal)
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      OutlinedButton.icon(
                        onPressed: _switchingSource
                            ? null
                            : _feeds.watching
                            ? _feeds.pause
                            : _feeds.resume,
                        icon: Icon(
                          _feeds.watching ? Icons.pause : Icons.play_arrow,
                        ),
                        label: Text(_feeds.watching ? '暂停观看' : '继续观看'),
                      ),
                      OutlinedButton.icon(
                        onPressed: _feeds.refreshSources,
                        icon: const Icon(Icons.refresh),
                        label: const Text('刷新摄像头列表'),
                      ),
                    ],
                  ),
                const SizedBox(height: 20),
                ClipRRect(
                  borderRadius: BorderRadius.circular(14),
                  child: Container(
                    color: Colors.black,
                    constraints: const BoxConstraints(maxHeight: 300),
                    child: AspectRatio(
                      aspectRatio: 16 / 9,
                      child: !_isLocal
                          ? _remotePreview()
                          : ready
                          ? Center(child: CameraPreview(_camera!))
                          : const Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Icon(
                                    Icons.camera_alt_outlined,
                                    size: 48,
                                    color: muted,
                                  ),
                                  SizedBox(height: 12),
                                  Text(
                                    '点击「打开摄像头」开始',
                                    style: TextStyle(color: muted),
                                  ),
                                ],
                              ),
                            ),
                    ),
                  ),
                ),
                if (!_isLocal)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      _feeds.status,
                      style: const TextStyle(color: muted, height: 1.7),
                    ),
                  ),
                const SizedBox(height: 20),
                Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  children: [
                    FilledButton.icon(
                      onPressed: captureReady && !_taking && !_reader.busy
                          ? _readText
                          : null,
                      icon: const Icon(Icons.document_scanner_outlined),
                      label: Text(
                        _reader.busy
                            ? '正在识字…'
                            : !_isLocal
                            ? widget.app.preferences.autoRead
                                  ? '当前画面识字并朗读'
                                  : '当前画面识字'
                            : widget.app.preferences.autoRead
                            ? '拍照识字并朗读'
                            : '拍照识字',
                      ),
                    ),
                    OutlinedButton.icon(
                      onPressed: _reader.busy ? _reader.cancel : null,
                      icon: const Icon(Icons.cancel_outlined),
                      label: const Text('取消识别'),
                    ),
                    OutlinedButton.icon(
                      onPressed: _reader.text.isNotEmpty && !_reader.busy
                          ? () => widget.app.speech.speak(_reader.text)
                          : null,
                      icon: const Icon(Icons.replay),
                      label: const Text('重播文字'),
                    ),
                    OutlinedButton.icon(
                      onPressed: widget.app.speech.speaking
                          ? widget.app.speech.stop
                          : null,
                      icon: const Icon(Icons.stop_circle_outlined),
                      label: const Text('停止朗读'),
                    ),
                    if (_isLocal)
                      OutlinedButton.icon(
                        onPressed: ready && !_reader.busy && !_taking
                            ? () {
                                if (_uploading) {
                                  setState(_stopFrames);
                                } else {
                                  _startFrames();
                                }
                              }
                            : null,
                        icon: Icon(_uploading ? Icons.pause : Icons.radar),
                        label: Text(_uploading ? '暂停检测' : '开始平板检测'),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  _isLocal
                      ? '识字会暂停本机图像检测。离开页面或切到后台会关闭摄像头并停止朗读；重新进入后请手动打开。'
                      : '画面来自选中的摄像头，识字读取当前收到的一帧。关闭观看不会关闭电脑摄像头；画面过期后会清空。',
                  style: TextStyle(color: muted, fontSize: 12, height: 1.7),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Expanded(
                      child: Text(
                        '识字结果',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    StatusPill(
                      widget.app.speech.speaking
                          ? '正在朗读'
                          : _reader.busy
                          ? '识别中'
                          : '等待操作',
                      good: widget.app.speech.speaking,
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                if (_reader.busy)
                  const Padding(
                    padding: EdgeInsets.only(bottom: 16),
                    child: LinearProgressIndicator(),
                  ),
                Semantics(
                  liveRegion: true,
                  child: Text(
                    _reader.status,
                    style: const TextStyle(color: muted, height: 1.7),
                  ),
                ),
                if (_reader.error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Notice(_reader.error!),
                  ),
                if (_reader.text.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 18),
                    child: SelectableText(
                      _reader.text,
                      style: const TextStyle(fontSize: 22, height: 1.8),
                    ),
                  ),
                if (_reader.text.isNotEmpty)
                  TextButton(
                    onPressed: () {
                      _reader.text = '';
                      _reader.status = '已清除文字，可继续环境提醒。';
                      setState(() {});
                    },
                    child: const Text('清除文字，恢复环境提醒'),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          _detectionPanel(),
          const SizedBox(height: 16),
          const Text(
            '检测结果与所选摄像头来源一致；画面与检测结果分别更新。当前功能不提供真实距离或通行保证。',
            style: TextStyle(color: muted, fontSize: 12, height: 1.7),
          ),
        ],
      );
    },
  );

  Widget _sourceSelector() {
    final ids = <String>{
      widget.app.sourceId,
      ..._sources,
      ..._feeds.sources.keys,
    };
    return DropdownButtonFormField<String>(
      key: ValueKey(_source),
      initialValue: _source,
      decoration: const InputDecoration(labelText: '摄像头来源'),
      isExpanded: true,
      items: ids.map((id) {
        final info = _feeds.sources[id];
        final label = id == widget.app.sourceId
            ? '本机摄像头（手机 / 平板）'
            : id == 'local_camera'
            ? '电脑网页摄像头'
            : info?.label ?? '${id.startsWith('simulator_') ? '【模拟】' : ''}$id';
        return DropdownMenuItem(
          value: id,
          child: Text(
            '$label${info == null
                ? ''
                : info.online
                ? ' · 有画面'
                : ' · 等待画面'}',
            overflow: TextOverflow.ellipsis,
          ),
        );
      }).toList(),
      onChanged: _switchingSource
          ? null
          : (value) {
              if (value != null) unawaited(_selectSource(value));
            },
    );
  }

  Widget _remotePreview() {
    final image = _feeds.image;
    if (image == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            _feeds.status,
            textAlign: TextAlign.center,
            style: const TextStyle(color: muted, height: 1.7),
          ),
        ),
      );
    }
    return Image.memory(
      image,
      fit: BoxFit.contain,
      gaplessPlayback: true,
      semanticLabel: '所选摄像头的实时画面',
      errorBuilder: (_, _, _) => const Center(
        child: Text('画面无法解码，正在等待下一帧。', style: TextStyle(color: muted)),
      ),
    );
  }

  Widget _detectionPanel() {
    final objects = (_detection?['objects'] as List?) ?? [];
    final simulation =
        _detection?['simulation'] == true || _source.startsWith('simulator_');
    final status = _detection?['model_status'];
    return Panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            '检测结果与预警',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 16),
          Text(
            '当前检测来源：${_isLocal
                ? '本机摄像头'
                : _source == 'local_camera'
                ? '电脑网页摄像头'
                : _source}',
            style: const TextStyle(color: muted, height: 1.7),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _alertsEnabled,
            title: const Text('环境预警在平板播报'),
            subtitle: const Text('仅播报关注来源；识字、朗读或保留识字结果时暂缓提醒。'),
            onChanged: (value) {
              setState(() => _alertsEnabled = value);
              if (!value) widget.app.speech.stop();
            },
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              StatusPill(
                !_isLocal
                    ? '远端摄像头画面'
                    : _uploading
                    ? '平板图像上传中（约 1 帧/秒）'
                    : '平板图像上传已暂停',
                good: _uploading,
              ),
              if (simulation) const StatusPill('模拟数据'),
              if (_detection != null)
                StatusPill(
                  '模型：${status == 'ready'
                      ? '已就绪'
                      : status == 'simulation'
                      ? '模拟'
                      : status == 'unavailable'
                      ? '不可用'
                      : '异常'}',
                  good: status == 'ready',
                ),
            ],
          ),
          const SizedBox(height: 16),
          if (_detection == null)
            const Text(
              '暂无新鲜检测结果。请开始平板检测，或让选中的设备上传画面。超过 15 秒的结果会自动清除。',
              style: TextStyle(color: muted, height: 1.7),
            )
          else if (status != 'ready' && status != 'simulation')
            const Text(
              '检测模型不可用，请检查电脑后端日志。',
              style: TextStyle(color: warningInk),
            )
          else if (objects.isEmpty)
            const Text('本帧未检测到目标。这不表示周围一定安全。', style: TextStyle(color: muted))
          else
            ...objects.map(
              (object) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Row(
                  children: [
                    const Icon(Icons.near_me_outlined, color: accent),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        '${directionLabel(object['direction'])} · ${objectLabel(object['class'])}',
                        style: const TextStyle(fontSize: 20),
                      ),
                    ),
                    Text(
                      '${(((object['confidence'] as num?) ?? 0) * 100).round()}%',
                      style: const TextStyle(color: muted),
                    ),
                  ],
                ),
              ),
            ),
          if (_detection != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                '结果时间：${timeLabel(_detection!['timestamp'])}',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
            ),
        ],
      ),
    );
  }
}
