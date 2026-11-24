import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:qiminghat_tablet/core/api.dart';
import 'package:qiminghat_tablet/core/preferences.dart';

import 'helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('server root normalization and invalid destination rejection', () {
    expect(
      normalizeServerUrl('  http://192.168.1.9:8765/  '),
      'http://192.168.1.9:8765',
    );
    for (final input in [
      '0.0.0.0:8765',
      'http://0.0.0.0:8765',
      'file:///tmp',
      'http://user:pass@server:80',
      'http://server/api',
      'http://server?token=secret',
      '',
    ]) {
      expect(
        () => normalizeServerUrl(input),
        throwsFormatException,
        reason: input,
      );
    }
  });
  test('raw JPEG body and bearer token match the OCR contract', () async {
    final image = Uint8List.fromList([255, 216, 12, 34, 255, 217]);
    final api = mockApi((options, bytes) {
      expect(options.path, '/api/ocr/read');
      expect(options.headers['Authorization'], 'Bearer test-token');
      expect(options.contentType, 'image/jpeg');
      expect(bytes, image);
      return jsonBody({'text': '文字'});
    });
    expect(
      (await api.map('/api/ocr/read', method: 'POST', data: image))['text'],
      '文字',
    );
    expect(api.socketUri.toString(), 'ws://192.168.1.10:8765/ws/tablet');
    api.dispose();
  });
  test(
    'raw voice WAV can override content type without changing JPEG defaults',
    () async {
      final wav = Uint8List.fromList([82, 73, 70, 70, 0, 0, 0, 0]);
      final api = mockApi((options, bytes) {
        expect(options.headers['Authorization'], 'Bearer test-token');
        expect(options.contentType, 'audio/wav');
        expect(bytes, wav);
        return jsonBody({'text': '停一下', 'requires_confirmation': true});
      });
      final result = await api.map(
        '/api/family/voice/transcribe?device_id=esp32s3_cam_001',
        method: 'POST',
        data: wav,
        contentType: 'audio/wav',
      );
      expect(result['text'], '停一下');
      api.dispose();
    },
  );
  test(
    'auth expiry, login rejection, and missing old backend endpoints',
    () async {
      var expired = 0;
      final api = mockApi(
        (options, _) => jsonBody({
          'detail': '拒绝访问',
        }, status: options.path == '/old' ? 404 : 401),
      )..onUnauthorized = () => expired++;
      await expectLater(
        api.map('/login', authenticated: false),
        throwsA(isA<ApiFailure>()),
      );
      expect(expired, 0);
      await expectLater(api.map('/session'), throwsA(isA<ApiFailure>()));
      expect(expired, 1);
      await expectLater(
        api.map('/old'),
        throwsA(
          isA<ApiFailure>().having((e) => e.message, 'action', contains('重启')),
        ),
      );
      api.dispose();
    },
  );
  test('token is bound to server and clearSession removes it', () async {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
    final prefs = AppPreferences(await SharedPreferences.getInstance());
    final source = prefs.sourceId;
    expect(source, matches(RegExp(r'^tablet_[a-f0-9]{32}$')));
    expect(prefs.sourceId, source);
    await prefs.saveSession('http://one:8765', 'private-token', 'testuser');
    expect(await prefs.readToken('http://two:8765'), isNull);
    expect(await prefs.readToken('http://one:8765'), 'private-token');
    expect(prefs.values.getKeys(), isNot(contains('sessionToken')));
    await prefs.clearSession();
    expect(await prefs.readToken('http://one:8765'), isNull);
  });
}
