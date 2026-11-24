import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:qiminghat_tablet/core/family_voice.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('qiming/family_voice_test');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  final wav = Uint8List.fromList(List<int>.generate(48, (index) => index));

  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test(
    'native recording is transcribed by the computer before returning text',
    () async {
      final calls = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call.method);
        return call.method == 'available' ? true : wav;
      });
      final voice = FamilyVoice(
        channel: channel,
        transcribe: (recording, cancelToken) async {
          expect(recording, wav);
          expect(cancelToken.isCancelled, isFalse);
          return '  前面先停一下  ';
        },
      );
      expect(await voice.available(), isTrue);
      expect(await voice.listen(), '前面先停一下');
      expect(calls, ['available', 'start']);
    },
  );

  test('permanent microphone denial keeps actionable native error', () async {
    messenger.setMockMethodCallHandler(channel, (call) async {
      throw PlatformException(
        code: 'permission_permanently_denied',
        message: '请到系统设置的应用权限中允许麦克风，再重试。',
      );
    });
    final voice = FamilyVoice(channel: channel);
    await expectLater(
      voice.listen(),
      throwsA(
        isA<FamilyVoiceFailure>()
            .having(
              (error) => error.code,
              'code',
              'permission_permanently_denied',
            )
            .having((error) => error.message, 'message', contains('系统设置')),
      ),
    );
  });

  test(
    'cancel rejects a transcript returned by a late native callback',
    () async {
      final nativeResult = Completer<Uint8List>();
      var transcriptionCalls = 0;
      messenger.setMockMethodCallHandler(channel, (call) async {
        if (call.method == 'start') return nativeResult.future;
        return null;
      });
      final voice = FamilyVoice(
        channel: channel,
        transcribe: (_, _) async {
          transcriptionCalls++;
          return '这条迟到结果不能发送';
        },
      );
      final listening = voice.listen();
      final expectation = expectLater(
        listening,
        throwsA(
          isA<FamilyVoiceFailure>().having((e) => e.code, 'code', 'cancelled'),
        ),
      );
      await voice.cancel();
      nativeResult.complete(wav);
      await expectation;
      expect(transcriptionCalls, 0);
    },
  );

  test(
    'cancelling computer recognition cancels HTTP work and ignores late text',
    () async {
      messenger.setMockMethodCallHandler(channel, (call) async {
        return call.method == 'start' ? wav : null;
      });
      final response = Completer<String>();
      CancelToken? requestCancel;
      final voice = FamilyVoice(
        channel: channel,
        transcribe: (_, cancelToken) {
          requestCancel = cancelToken;
          return response.future;
        },
      );
      final listening = voice.listen();
      final expectation = expectLater(
        listening,
        throwsA(
          isA<FamilyVoiceFailure>().having((e) => e.code, 'code', 'cancelled'),
        ),
      );
      await Future<void>.delayed(Duration.zero);
      expect(voice.transcribing.value, isTrue);
      await voice.cancel();
      expect(requestCancel?.isCancelled, isTrue);
      expect(voice.transcribing.value, isFalse);
      response.complete('取消之后返回的文字');
      await expectation;
    },
  );

  test(
    'missing native bridge remains available as typed messaging fallback',
    () async {
      final voice = FamilyVoice(channel: channel);
      expect(await voice.available(), isFalse);
      await expectLater(
        voice.listen(),
        throwsA(
          isA<FamilyVoiceFailure>().having(
            (e) => e.code,
            'code',
            'unavailable',
          ),
        ),
      );
    },
  );
}
