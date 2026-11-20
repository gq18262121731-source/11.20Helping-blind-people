import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:qiminghat_tablet/core/app_controller.dart';
import 'package:qiminghat_tablet/core/live_connection.dart';
import 'package:qiminghat_tablet/core/preferences.dart';
import 'package:qiminghat_tablet/ui/family_page.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('family overview is readable and usable on a narrow phone', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final preview = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNU697KQAgwEVQx7BUBAKUMAXYMhEojAAAAAElFTkSuQmCC',
    );
    final api = mockApi((options, _) {
      if (options.path == '/api/family/bindings') {
        return jsonBody([
          {
            'id': 1,
            'device_id': 'esp32s3_cam_001',
            'owner_username': '佩戴者小林',
            'family_username': '家属',
          },
        ]);
      }
      if (options.path.startsWith('/api/family/overview')) {
        return jsonBody({
          'role': 'family',
          'device': {
            'name': 'esp32s3_cam_001',
            'online': true,
            'mic_ok': true,
            'last_heartbeat': '2026-11-20T08:00:00Z',
          },
          'camera': {
            'online': true,
            'seq': 42,
            'updated_at': '2026-11-20T08:00:00Z',
          },
          'location': {
            'available': true,
            'latitude': 31.23,
            'longitude': 121.47,
            'accuracy_m': 120,
            'updated_at': '2026-11-20T08:00:00Z',
          },
          'permissions': {'camera': true, 'location': true, 'messages': true},
          'current_alert': null,
        });
      }
      if (options.path == '/api/family/messages') return jsonBody([]);
      if (options.path.endsWith('/frame')) {
        return ResponseBody.fromBytes(
          preview,
          200,
          headers: {
            Headers.contentTypeHeader: ['image/jpeg'],
          },
        );
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
        home: Scaffold(body: FamilyAssistPage(app: controller)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('家属远程协助'), findsOneWidget);
    expect(find.text('佩戴者小林 · 帽端 在线'), findsOneWidget);
    expect(find.text('实时视觉协助'), findsOneWidget);
    expect(find.text('当前位置'), findsOneWidget);
    expect(find.text('当前预警'), findsOneWidget);
    expect(find.text('联系佩戴者'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
