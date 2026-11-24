import 'dart:async';
import 'dart:math' as math;

import 'package:material_ui/material_ui.dart';

import '../core/app_controller.dart';
import 'common.dart';

class OverviewPage extends StatefulWidget {
  const OverviewPage({
    super.key,
    required this.app,
    required this.onOpenCamera,
  });
  final AppController app;
  final VoidCallback onOpenCamera;
  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage>
    with WidgetsBindingObserver {
  Map<String, dynamic>? _data;
  String? _error;
  bool _busy = false;
  bool _active = true;
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 10), (_) {
      if (_active) _refresh();
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _active = state == AppLifecycleState.resumed;
    if (_active) _refresh();
  }

  Future<void> _refresh() async {
    if (_busy || !widget.app.loggedIn) return;
    setState(() => _busy = true);
    try {
      final data = await widget.app.api!.map(
        '/api/overview?tz=Asia%2FShanghai',
      );
      if (data['devices'] is! Map || data['trend'] is! List) {
        throw const FormatException('总览接口格式不正确，请更新电脑后端。');
      }
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
        });
      }
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
    final data = _data;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          PageHeading(
            '系统总览',
            '你好，${widget.app.username} · 连接电脑，感知身边',
            action: IconButton(
              tooltip: '刷新总览',
              onPressed: _busy ? null : _refresh,
              icon: const Icon(Icons.refresh),
            ),
          ),
          if (widget.app.notice != null) Notice(widget.app.notice!),
          if (_error != null)
            Notice('${_data == null ? '' : '刷新失败，下方保留上次数据。'}$_error'),
          if (_busy && data == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (data != null) ...[
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                _metric(
                  Icons.sensors,
                  '真实设备在线',
                  '${data['devices']['real_online'] ?? 0}',
                  '模拟在线 ${data['devices']['simulation_online'] ?? 0}',
                ),
                _metric(
                  Icons.warning_amber,
                  '今日真实预警',
                  '${data['today_alerts']?['real'] ?? 0}',
                  '模拟 ${data['today_alerts']?['simulation'] ?? 0}',
                ),
                _metric(
                  Icons.record_voice_over_outlined,
                  '今日真实交互',
                  '${data['today_voices']?['real'] ?? 0}',
                  '模拟 ${data['today_voices']?['simulation'] ?? 0}',
                ),
                _metric(
                  Icons.psychology_outlined,
                  '识字模型',
                  data['llm']?['label']?.toString() ?? '未知',
                  data['llm']?['detail']?.toString() ?? '',
                ),
              ],
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
                          '近 7 天预警趋势',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      Text(
                        '${data['timezone']}',
                        style: const TextStyle(color: muted, fontSize: 12),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  TrendChart(
                    points: List<Map<String, dynamic>>.from(
                      (data['trend'] as List).map(
                        (e) => Map<String, dynamic>.from(e as Map),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    '蓝色：真实预警　青绿色：模拟预警',
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),
            Panel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '开始使用',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    '对准纸张或标牌，拍照后在平板上朗读。也可以开启平板摄像头检测，或接收已连接帽端的检测结果。',
                    style: TextStyle(color: muted, height: 1.7),
                  ),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: widget.onOpenCamera,
                    icon: const Icon(Icons.camera_alt_outlined),
                    label: const Text('打开实时环境监测'),
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      StatusPill(
                        'YOLO ${data['yolo']?['label'] ?? '未知'}',
                        good: data['yolo']?['state'] == 'ready',
                      ),
                      StatusPill(
                        '识字模型 ${data['llm']?['label'] ?? '未知'}',
                        good: data['llm']?['state'] == 'available',
                      ),
                      if (data['mode'] == 'simulation')
                        const StatusPill('允许模拟设备；平板图像仍运行真实检测'),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),
            _records('最近预警', data['recent_alerts'] as List? ?? [], false),
            const SizedBox(height: 20),
            _records('最近交互', data['recent_voices'] as List? ?? [], true),
            const SizedBox(height: 16),
            Text(
              '上次更新：${timeLabel(data['generated_at'])} · 每 10 秒刷新',
              style: const TextStyle(color: muted, fontSize: 12),
            ),
          ],
        ],
      ),
    );
  }

  Widget _metric(IconData icon, String label, String value, String note) =>
      SizedBox(
        width: 238,
        child: Panel(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(icon, color: accent, size: 20),
                  const SizedBox(width: 8),
                  Text(label, style: const TextStyle(color: muted)),
                ],
              ),
              const SizedBox(height: 16),
              Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: accent,
                  fontSize: 28,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 10),
              Text(
                note,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: muted, fontSize: 12),
              ),
            ],
          ),
        ),
      );

  Widget _records(String title, List records, bool voices) => Panel(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 12),
        if (records.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 20),
            child: Text('暂无记录', style: TextStyle(color: muted)),
          ),
        ...records.map(
          (record) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  voices
                      ? '${record['user_text']} → ${record['reply_text']}'
                      : '${directionLabel(record['direction'])}有${objectLabel(record['object_class'])}',
                  style: const TextStyle(height: 1.7),
                ),
                const SizedBox(height: 4),
                Text(
                  '${record['source'] == 'simulation' ? '【模拟】' : '【真实】'} ${record['device_id']} · ${timeLabel(record[voices ? 'occurred_at' : 'detected_at'])}',
                  style: const TextStyle(color: muted, fontSize: 12),
                ),
              ],
            ),
          ),
        ),
      ],
    ),
  );
}

class TrendChart extends StatelessWidget {
  const TrendChart({super.key, required this.points});
  final List<Map<String, dynamic>> points;
  @override
  Widget build(BuildContext context) => Semantics(
    label:
        '最近七天预警。${points.map((p) => '${p['date']}真实${p['real']}次，模拟${p['simulation']}次').join('；')}',
    child: SizedBox(
      height: 205,
      width: double.infinity,
      child: CustomPaint(painter: _TrendPainter(points)),
    ),
  );
}

class _TrendPainter extends CustomPainter {
  _TrendPainter(this.points);
  final List<Map<String, dynamic>> points;
  void _text(Canvas canvas, String text, Offset at) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(color: muted, fontSize: 11),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, at);
  }

  @override
  void paint(Canvas canvas, Size size) {
    if (points.isEmpty) return;
    final maxValue = points
        .fold<num>(
          1,
          (value, p) => math.max(
            value,
            math.max((p['real'] as num?) ?? 0, (p['simulation'] as num?) ?? 0),
          ),
        )
        .toDouble();
    final scale = math.max(1, (maxValue / 4).ceil()) * 4;
    final width = math.max(1.0, size.width - 58);
    final height = size.height - 32;
    for (var i = 0; i <= 4; i++) {
      final y = height - height * i / 4;
      canvas.drawLine(
        Offset(38, y),
        Offset(size.width - 12, y),
        Paint()..color = chartGrid,
      );
      _text(canvas, '${(scale * i / 4).round()}', Offset(0, y - 5));
    }
    for (final entry in {'real': accent, 'simulation': chartAqua}.entries) {
      final path = Path();
      for (var i = 0; i < points.length; i++) {
        final x = 40 + width * i / math.max(1, points.length - 1);
        final y =
            height - ((points[i][entry.key] as num?) ?? 0) / scale * height;
        if (i == 0) {
          path.moveTo(x, y);
        } else {
          path.lineTo(x, y);
        }
        canvas.drawCircle(Offset(x, y), 3, Paint()..color = entry.value);
        if (entry.key == 'real') {
          _text(
            canvas,
            '${points[i]['date']}'.substring(5),
            Offset(x - 15, height + 14),
          );
        }
      }
      canvas.drawPath(
        path,
        Paint()
          ..color = entry.value
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }
  }

  @override
  bool shouldRepaint(_TrendPainter oldDelegate) => oldDelegate.points != points;
}
