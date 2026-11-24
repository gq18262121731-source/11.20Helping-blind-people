import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:qiminghat_tablet/core/app_controller.dart';
import 'package:qiminghat_tablet/core/live_connection.dart';
import 'package:qiminghat_tablet/core/preferences.dart';
import 'package:qiminghat_tablet/main.dart';
import 'package:qiminghat_tablet/ui/camera_page.dart';
import 'package:qiminghat_tablet/ui/common.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    mockTts();
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
  });
  Future<AppController> app({bool signedIn = true, bool fail = false}) async {
    final controller = AppController(
      AppPreferences(await SharedPreferences.getInstance()),
      RecordingSpeech(),
    );
    controller.loading = false;
    if (signedIn) {
      controller.api = mockApi((options, _) {
        if (fail) return jsonBody({'detail': '模型服务暂不可用'}, status: 503);
        if (options.path.startsWith('/api/overview')) {
          return jsonBody(overviewFixture());
        }
        if (options.path == '/api/tablet/config') {
          return jsonBody({'confidence': .35});
        }
        return jsonBody([]);
      });
      controller.live = LiveConnection(controller.api!);
      controller.loggedIn = true;
      controller.username = '测试用户';
    }
    return controller;
  }

  testWidgets('app uses the PC light palette and readable navigation colors', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(600, 960);
    tester.view.devicePixelRatio = 1;
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
    final controller = await app();
    await tester.pumpWidget(QimingTablet(controller: controller));
    await tester.pumpAndSettle();

    final navigation = find.byType(NavigationBar);
    expect(navigation, findsOneWidget);
    final theme = Theme.of(tester.element(navigation));
    expect(theme.brightness, Brightness.light);
    expect(theme.scaffoldBackgroundColor, const Color(0xfff5f7fa));
    expect(theme.colorScheme.primary, const Color(0xff1677ff));
    expect(theme.colorScheme.surface, Colors.white);
    expect(theme.colorScheme.onSurface, const Color(0xff1d2129));
    expect(
      theme.appBarTheme.backgroundColor,
      isIn([Colors.white, const Color(0xfff5f7fa)]),
    );
    expect(theme.navigationBarTheme.indicatorColor, const Color(0xff1677ff));
    expect(
      theme.navigationBarTheme.iconTheme?.resolve({
        WidgetState.selected,
      })?.color,
      Colors.white,
    );
    expect(
      theme.navigationBarTheme.iconTheme?.resolve({})?.color,
      isIn([const Color(0xff4e5969), const Color(0xff667085)]),
    );
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('shared panels have white surfaces and eight pixel corners', (
    tester,
  ) async {
    final controller = await app(signedIn: false);
    await tester.pumpWidget(QimingTablet(controller: controller));
    await tester.pumpAndSettle();

    final panel = find.byType(Panel).first;
    final material = tester.widget<Material>(
      find.descendant(of: panel, matching: find.byType(Material)).first,
    );
    expect(material.color, Colors.white);
    expect(material.shape, isA<RoundedRectangleBorder>());
    final shape = material.shape! as RoundedRectangleBorder;
    expect(shape.borderRadius, BorderRadius.circular(8));
    expect(find.text('连接电脑后端'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  for (final size in [
    const Size(1280, 800),
    const Size(600, 960),
    const Size(360, 800),
  ]) {
    testWidgets(
      'navigation and empty states fit ${size.width}x${size.height}',
      (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final controller = await app();
        await tester.pumpWidget(QimingTablet(controller: controller));
        await tester.pumpAndSettle();
        await tester.scrollUntilVisible(
          find.text('近 7 天预警趋势'),
          300,
          scrollable: find.byType(Scrollable).last,
        );
        expect(find.text('近 7 天预警趋势'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('实时环境监测'));
        await tester.pumpAndSettle();
        expect(find.text('打开摄像头'), findsOneWidget);
        expect(find.text('拍照识字并朗读'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('设备管理'));
        await tester.pumpAndSettle();
        expect(find.text('暂无帽端设备'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('系统设置'));
        await tester.pumpAndSettle();
        expect(find.text('平板本机语音'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
      },
    );
  }
  testWidgets('connection failure is explicit and loading indicator ends', (
    tester,
  ) async {
    final controller = await app(fail: true);
    await tester.pumpWidget(QimingTablet(controller: controller));
    await tester.pumpAndSettle();
    expect(find.textContaining('模型服务暂不可用'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('近 7 天预警趋势'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
  testWidgets('first run shows editable server URL and LAN guidance', (
    tester,
  ) async {
    final controller = await app(signedIn: false);
    await tester.pumpWidget(QimingTablet(controller: controller));
    await tester.pumpAndSettle();
    expect(find.text('连接电脑后端'), findsOneWidget);
    expect(find.text('电脑后端地址'), findsOneWidget);
    expect(find.textContaining('10.0.2.2'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('remote source shows the selected image and OCR uses that image', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final preview = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNU697KQAgwEVQx7BUBAKUMAXYMhEojAAAAAElFTkSuQmCC',
    );
    var stopped = false;
    var recognized = false;
    final api = mockApi((options, bytes) {
      if (options.path == '/api/tablet/cameras') {
        return jsonBody([
          {'source_id': 'local_camera', 'label': '电脑网页摄像头', 'online': true},
        ]);
      }
      if (options.path.endsWith('/local_camera/frame')) {
        return stopped
            ? jsonBody({'detail': 'stopped'}, status: 410)
            : ResponseBody.fromBytes(
                preview,
                200,
                headers: {
                  Headers.contentTypeHeader: ['image/jpeg'],
                },
              );
      }
      if (options.path == '/api/ocr/read') {
        recognized = true;
        expect(bytes, preview);
        return jsonBody({
          'text': '来自电脑摄像头的文字',
          'has_text': true,
          'truncated': false,
        });
      }
      return jsonBody([]);
    });
    final controller = AppController(
      AppPreferences(await SharedPreferences.getInstance()),
      RecordingSpeech(),
    )..api = api;
    controller.live = LiveConnection(api);
    controller.loggedIn = true;
    controller.loading = false;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: CameraPage(app: controller)),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('电脑网页摄像头').last);
    await tester.pumpAndSettle();
    expect(find.text('打开摄像头'), findsNothing);
    expect(find.text('开始平板检测'), findsNothing);
    expect(find.text('暂停观看'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
    final read = find.text('当前画面识字并朗读');
    await tester.ensureVisible(read);
    await tester.tap(read);
    await tester.pumpAndSettle();
    expect(recognized, isTrue);
    expect(find.text('来自电脑摄像头的文字'), findsOneWidget);
    stopped = true;
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.byType(Image), findsNothing);
    expect(find.textContaining('请先在电脑网页端启动摄像头'), findsWidgets);
    expect(tester.takeException(), isNull);
    await tester.ensureVisible(find.byType(DropdownButtonFormField<String>));
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('本机摄像头（手机 / 平板）').last);
    await tester.pumpAndSettle();
    expect(find.text('打开摄像头'), findsOneWidget);
    expect(find.text('来自电脑摄像头的文字'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
