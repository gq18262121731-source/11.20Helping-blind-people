import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:qiminghat_tablet/core/app_controller.dart';
import 'package:qiminghat_tablet/core/family_voice.dart';
import 'package:qiminghat_tablet/core/live_connection.dart';
import 'package:qiminghat_tablet/core/preferences.dart';
import 'package:qiminghat_tablet/ui/family_page.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'helpers.dart';

class TestFamilyVoice extends FamilyVoice {
  final result = Completer<String>();
  int starts = 0;
  int cancellations = 0;
  bool supported = true;

  @override
  Future<bool> available() async => supported;

  @override
  Future<String> listen() {
    starts++;
    return result.future;
  }

  @override
  Future<void> stop() async {}

  @override
  Future<void> cancel() async {
    cancellations++;
  }
}

final familyPreview = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNU697KQAgwEVQx7BUBAKUMAXYMhEojAAAAAElFTkSuQmCC',
);

Map<String, dynamic> familyOverview({bool camera = true}) => {
  'role': 'family',
  'device': {'name': 'esp32s3_cam_001', 'online': true},
  'camera': {'online': true},
  'permissions': {'camera': camera, 'location': false, 'messages': true},
  'speech': {'available': true, 'status': 'ready', 'detail': '帽端支持合成语音播报。'},
};

Future<AppController> showFamilyPage(
  WidgetTester tester, {
  TestFamilyVoice? voice,
  FutureOr<ResponseBody> Function(RequestOptions, ResponseBody Function())?
  onRequest,
}) async {
  tester.view.physicalSize = const Size(360, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final api = mockApi((options, _) {
    ResponseBody fallback() {
      if (options.path == '/api/family/bindings') {
        return jsonBody([
          {
            'id': 1,
            'device_id': 'esp32s3_cam_001',
            'owner_username': '佩戴者',
            'family_username': '家属',
          },
        ]);
      }
      if (options.path.startsWith('/api/family/overview')) {
        return jsonBody(familyOverview());
      }
      if (options.path.startsWith('/api/family/messages')) return jsonBody([]);
      return ResponseBody.fromBytes(
        familyPreview,
        200,
        headers: {
          Headers.contentTypeHeader: ['image/jpeg'],
        },
      );
    }

    return onRequest?.call(options, fallback) ?? fallback();
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
      home: Scaffold(
        body: FamilyAssistPage(app: controller, voice: voice),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return controller;
}

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

  testWidgets(
    'voice transcript is editable and only sends after confirmation',
    (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final voice = TestFamilyVoice();
      final posted = <Map<String, dynamic>>[];
      final controller = await showFamilyPage(
        tester,
        voice: voice,
        onRequest: (options, fallback) {
          if (options.path == '/api/family/messages' &&
              options.method == 'POST') {
            posted.add(Map<String, dynamic>.from(options.data as Map));
            return jsonBody({
              'id': 1,
              'delivery': {'status': 'queued', 'detail': '已加入帽端播报队列，等待设备播放。'},
            });
          }
          return fallback();
        },
      );
      await tester.scrollUntilVisible(
        find.text('语音留言'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('语音留言'));
      await tester.pumpAndSettle();
      expect(voice.starts, 1);
      expect(find.text('请说出协助内容'), findsOneWidget);
      voice.transcribing.value = true;
      await tester.pumpAndSettle();
      expect(find.text('电脑正在识别'), findsOneWidget);
      expect(find.text('正在录音，请靠近麦克风。说完后点击结束，或静音片刻自动结束。'), findsNothing);
      voice.result.complete('前面先停一下');
      await tester.pumpAndSettle();
      expect(find.text('确认语音留言'), findsOneWidget);
      expect(posted, isEmpty);
      await tester.enterText(find.byType(TextField), '前面路口先停一下');
      await tester.tap(find.text('确认并发送'));
      await tester.pumpAndSettle();
      expect(posted, [
        {
          'device_id': 'esp32s3_cam_001',
          'body': '前面路口先停一下',
          'message_type': 'voice_transcript',
        },
      ]);
      expect(find.text('已加入帽端播报队列，等待设备播放。'), findsOneWidget);
      expect(find.text('已播放'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'cancelled recognition never opens confirmation or sends late text',
    (tester) async {
      final voice = TestFamilyVoice();
      var posts = 0;
      final controller = await showFamilyPage(
        tester,
        voice: voice,
        onRequest: (options, fallback) {
          if (options.method == 'POST') posts++;
          return fallback();
        },
      );
      await tester.scrollUntilVisible(
        find.text('语音留言'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('语音留言'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      voice.result.complete('取消后的迟到文字');
      await tester.pumpAndSettle();
      expect(find.text('确认语音留言'), findsNothing);
      expect(posts, 0);
      expect(voice.cancellations, greaterThan(0));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets('camera polling pauses, resumes and clears an expired frame', (
    tester,
  ) async {
    var frames = 0;
    var expired = false;
    final controller = await showFamilyPage(
      tester,
      onRequest: (options, fallback) {
        if (options.path.endsWith('/frame')) {
          frames++;
          if (expired) return jsonBody({'detail': '画面已过期'}, status: 410);
        }
        return fallback();
      },
    );
    expect(find.byType(Image), findsOneWidget);
    final first = frames;
    await tester.pump(const Duration(milliseconds: 750));
    await tester.pumpAndSettle();
    expect(frames, greaterThan(first));
    await tester.ensureVisible(find.byTooltip('暂停观看'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('暂停观看'));
    await tester.pumpAndSettle();
    final paused = frames;
    expect(find.byType(Image), findsNothing);
    await tester.pump(const Duration(seconds: 2));
    expect(frames, paused);
    await tester.tap(find.byTooltip('继续观看'));
    await tester.pumpAndSettle();
    expect(frames, greaterThan(paused));
    expect(find.byType(Image), findsOneWidget);
    expired = true;
    await tester.pump(const Duration(milliseconds: 750));
    await tester.pumpAndSettle();
    expect(find.byType(Image), findsNothing);
    expect(find.text('摄像头未开启或画面已过期，正在等待新的画面'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets(
    'background cancels preview and prevents returning stale frames',
    (tester) async {
      final lateFrame = Completer<ResponseBody>();
      var frames = 0;
      final controller = await showFamilyPage(
        tester,
        onRequest: (options, fallback) {
          if (options.path.endsWith('/frame')) {
            frames++;
            if (frames == 2) return lateFrame.future;
          }
          return fallback();
        },
      );
      expect(find.byType(Image), findsOneWidget);
      await tester.pump(const Duration(milliseconds: 750));
      await tester.pump();
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      await tester.pumpAndSettle();
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
      await tester.pumpAndSettle();
      final backgroundFrames = frames;
      expect(find.byType(Image), findsNothing);
      lateFrame.complete(
        ResponseBody.fromBytes(
          familyPreview,
          200,
          headers: {
            Headers.contentTypeHeader: ['image/jpeg'],
          },
        ),
      );
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(frames, backgroundFrames);
      expect(find.byType(Image), findsNothing);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pumpAndSettle();
      expect(frames, greaterThan(backgroundFrames));
      expect(find.byType(Image), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets('temporary inactive permission UI preserves the voice session', (
    tester,
  ) async {
    final voice = TestFamilyVoice();
    final controller = await showFamilyPage(tester, voice: voice);
    await tester.scrollUntilVisible(
      find.text('语音留言'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('语音留言'));
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pumpAndSettle();
    expect(voice.cancellations, 0);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    voice.result.complete('请靠右侧走');
    await tester.pumpAndSettle();
    expect(find.text('确认语音留言'), findsOneWidget);
    expect(find.text('请靠右侧走'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('microphone denial stays visible and leaves a retry action', (
    tester,
  ) async {
    final voice = TestFamilyVoice();
    final controller = await showFamilyPage(tester, voice: voice);
    await tester.scrollUntilVisible(
      find.text('语音留言'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('语音留言'));
    await tester.pumpAndSettle();
    voice.result.completeError(
      const FamilyVoiceFailure(
        'permission_permanently_denied',
        '请到系统设置的应用权限中允许麦克风，再重试。',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('语音输入暂不可用'), findsOneWidget);
    expect(find.text('请到系统设置的应用权限中允许麦克风，再重试。'), findsOneWidget);
    expect(find.text('重新录音'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets(
    'revoked camera authorization clears the frame and stops polling',
    (tester) async {
      var frames = 0;
      var revoked = false;
      final controller = await showFamilyPage(
        tester,
        onRequest: (options, fallback) {
          if (options.path.endsWith('/frame')) {
            frames++;
            if (revoked) return jsonBody({'detail': '画面查看权限已撤销'}, status: 403);
          }
          return fallback();
        },
      );
      expect(find.byType(Image), findsOneWidget);
      revoked = true;
      await tester.pump(const Duration(milliseconds: 750));
      await tester.pumpAndSettle();
      expect(find.byType(Image), findsNothing);
      final denied = frames;
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(frames, denied);
      expect(find.text('画面查看权限已撤销，请刷新授权状态。'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'one account can register its own offline hat from the empty page',
    (tester) async {
      String? registered;
      final posted = <Map<String, dynamic>>[];
      final controller = await showFamilyPage(
        tester,
        onRequest: (options, fallback) {
          if (options.path == '/api/family/devices' &&
              options.method == 'POST') {
            final body = Map<String, dynamic>.from(options.data as Map);
            posted.add(body);
            registered = body['device_id'] as String;
            return jsonBody({'created': true, 'device_id': registered});
          }
          if (options.path == '/api/family/bindings') {
            return jsonBody([
              if (registered != null)
                {
                  'id': 11,
                  'device_id': registered,
                  'owner_username': 'admin',
                  'family_username': 'admin',
                  'is_owner_device': true,
                },
            ]);
          }
          if (options.path.startsWith('/api/family/overview')) {
            return jsonBody({
              ...familyOverview(),
              'role': 'owner',
              'device': {'name': registered, 'online': false},
              'camera': {'online': false},
              'speech': {
                'available': false,
                'status': 'offline',
                'detail': '帽端离线，暂无法播报。',
              },
            });
          }
          if (options.path.endsWith('/frame')) {
            return jsonBody({'detail': '摄像头未开启'}, status: 410);
          }
          return fallback();
        },
      );
      expect(find.text('还没有可访问的帽端'), findsOneWidget);
      await tester.tap(find.text('添加我的帽端'));
      await tester.pumpAndSettle();
      expect(find.text('esp32s3_cam_001'), findsOneWidget);
      await tester.enterText(find.byType(TextField), 'esp32s3_cam_002');
      await tester.tap(find.text('添加'));
      await tester.pumpAndSettle();
      expect(posted, [
        {'device_id': 'esp32s3_cam_002'},
      ]);
      expect(find.text('admin · 帽端 离线'), findsOneWidget);
      expect(find.byType(Image), findsNothing);
      await tester.scrollUntilVisible(
        find.text('授权其他家属'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('此帽端属于当前账号，可授权其他家属查看画面与发送留言。'), findsOneWidget);
      expect(find.byTooltip('撤销家属授权'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'family revocation targets a grant and never the self owner record',
    (tester) async {
      final deleted = <String>[];
      var familyRevoked = false;
      final controller = await showFamilyPage(
        tester,
        onRequest: (options, fallback) {
          if (options.method == 'DELETE') {
            deleted.add(options.path);
            familyRevoked = true;
            return jsonBody({'deleted': true});
          }
          if (options.path == '/api/family/bindings') {
            return jsonBody([
              {
                'id': 11,
                'device_id': 'esp32s3_cam_001',
                'owner_username': 'admin',
                'family_username': 'admin',
              },
              if (!familyRevoked)
                {
                  'id': 12,
                  'device_id': 'esp32s3_cam_001',
                  'owner_username': 'admin',
                  'family_username': 'familyuser',
                },
            ]);
          }
          if (options.path.startsWith('/api/family/overview')) {
            return jsonBody({...familyOverview(), 'role': 'owner'});
          }
          return fallback();
        },
      );
      await tester.scrollUntilVisible(
        find.byTooltip('撤销家属授权'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.byTooltip('撤销家属授权'), findsOneWidget);
      await tester.tap(find.byTooltip('撤销家属授权'));
      await tester.pumpAndSettle();
      expect(find.text('撤销后，familyuser 将无法继续查看此设备。'), findsOneWidget);
      await tester.tap(find.text('撤销授权'));
      await tester.pumpAndSettle();
      expect(deleted, ['/api/family/bindings/12']);
      expect(find.byTooltip('撤销家属授权'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
