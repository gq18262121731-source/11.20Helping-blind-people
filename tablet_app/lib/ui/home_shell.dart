import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../core/app_controller.dart';
import 'camera_page.dart';
import 'common.dart';
import 'family_page.dart';
import 'management_pages.dart';
import 'overview_page.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.app});
  final AppController app;
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  int _index = 0;
  final _labels = const ['系统总览', '家属远程协助', '实时环境监测', '设备管理', '系统设置'];
  final _icons = const [
    Icons.dashboard_outlined,
    Icons.family_restroom_outlined,
    Icons.camera_alt_outlined,
    Icons.sensors,
    Icons.settings_outlined,
  ];
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      widget.app.live?.start();
    } else {
      widget.app.live?.stop();
      unawaited(widget.app.speech.stop());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  void _navigate(int index) {
    unawaited(widget.app.speech.stop());
    setState(() => _index = index);
  }

  Widget get _page => switch (_index) {
    0 => OverviewPage(app: widget.app, onOpenCamera: () => _navigate(2)),
    1 => FamilyAssistPage(app: widget.app),
    2 => CameraPage(app: widget.app),
    3 => DevicesPage(app: widget.app),
    _ => SettingsPage(app: widget.app),
  };
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final wide = constraints.maxWidth >= 900;
      return Scaffold(
        appBar: AppBar(
          title: const Text(
            '启明帽 · 智能视觉辅助',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
          ),
          actions: [
            AnimatedBuilder(
              animation: widget.app.live!,
              builder: (_, _) => Padding(
                padding: const EdgeInsets.only(right: 12),
                child: StatusPill(
                  '实时通道 ${widget.app.live!.status}',
                  good: widget.app.live!.connected,
                ),
              ),
            ),
          ],
        ),
        body: SafeArea(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (wide)
                NavigationRail(
                  extended: constraints.maxWidth >= 1180,
                  minExtendedWidth: 206,
                  selectedIndex: _index,
                  onDestinationSelected: _navigate,
                  leading: const Padding(
                    padding: EdgeInsets.all(16),
                    child: Icon(
                      Icons.visibility_outlined,
                      color: accent,
                      size: 36,
                    ),
                  ),
                  destinations: List.generate(
                    _labels.length,
                    (i) => NavigationRailDestination(
                      icon: Icon(_icons[i]),
                      label: Text(_labels[i]),
                    ),
                  ),
                  trailing: Expanded(
                    child: Align(
                      alignment: Alignment.bottomCenter,
                      child: Padding(
                        padding: const EdgeInsets.only(bottom: 24),
                        child: IconButton(
                          tooltip: '退出登录',
                          onPressed: widget.app.logout,
                          icon: const Icon(Icons.logout),
                        ),
                      ),
                    ),
                  ),
                ),
              if (wide) const VerticalDivider(width: 1),
              Expanded(child: _page),
            ],
          ),
        ),
        bottomNavigationBar: wide
            ? null
            : NavigationBar(
                selectedIndex: _index,
                onDestinationSelected: _navigate,
                destinations: List.generate(
                  _labels.length,
                  (i) => NavigationDestination(
                    icon: Icon(_icons[i]),
                    label: _labels[i],
                  ),
                ),
              ),
      );
    },
  );
}
