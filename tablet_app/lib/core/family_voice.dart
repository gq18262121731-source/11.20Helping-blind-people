import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class FamilyVoiceFailure implements Exception {
  const FamilyVoiceFailure(this.code, this.message);
  final String code;
  final String message;

  @override
  String toString() => message;
}

/// One user-initiated utterance. Sending always happens after UI confirmation.
class FamilyVoice {
  FamilyVoice({
    MethodChannel? channel,
    Future<String> Function(Uint8List wav, CancelToken cancelToken)? transcribe,
  }) : _channel = channel ?? const MethodChannel('qiming/family_voice'),
       _transcribe = transcribe;

  final MethodChannel _channel;
  final Future<String> Function(Uint8List wav, CancelToken cancelToken)?
  _transcribe;
  final ValueNotifier<bool> transcribing = ValueNotifier(false);
  CancelToken? _transcriptionCancel;
  int _generation = 0;
  bool _disposed = false;

  Future<bool> available() async {
    if (_disposed) return false;
    try {
      return await _channel.invokeMethod<bool>('available') == true;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  Future<String> listen() async {
    if (_disposed) {
      throw const FamilyVoiceFailure('cancelled', '语音输入已结束。');
    }
    final generation = ++_generation;
    try {
      final wav = await _channel
          .invokeMethod<Uint8List>('start')
          .timeout(const Duration(seconds: 35));
      if (_disposed || generation != _generation) {
        throw const FamilyVoiceFailure('cancelled', '本次语音输入已取消。');
      }
      if (wav == null || wav.length < 44) {
        throw const FamilyVoiceFailure('empty_audio', '没有获取到有效录音，请重新录制。');
      }
      final transcribe = _transcribe;
      if (transcribe == null) {
        throw const FamilyVoiceFailure('unavailable', '电脑语音识别接口未接通，请更新 App。');
      }
      final token = CancelToken();
      _transcriptionCancel = token;
      transcribing.value = true;
      final text = await transcribe(wav, token);
      if (_disposed || generation != _generation || token.isCancelled) {
        throw const FamilyVoiceFailure('cancelled', '本次语音输入已取消。');
      }
      final value = text.trim();
      if (value.isEmpty) {
        throw const FamilyVoiceFailure('no_match', '没有识别到文字，请靠近麦克风重试。');
      }
      return value;
    } on MissingPluginException {
      throw const FamilyVoiceFailure(
        'unavailable',
        '此版本尚未接通录音，请安装更新后的 Android App，或使用文字留言。',
      );
    } on PlatformException catch (error) {
      throw FamilyVoiceFailure(error.code, error.message ?? '录音失败，请重试。');
    } on DioException catch (error) {
      if (CancelToken.isCancel(error) ||
          _disposed ||
          generation != _generation) {
        throw const FamilyVoiceFailure('cancelled', '本次语音输入已取消。');
      }
      rethrow;
    } on TimeoutException {
      await cancel();
      throw const FamilyVoiceFailure('timeout', '录音超时，请重新录制。');
    } finally {
      if (!_disposed && generation == _generation) {
        transcribing.value = false;
        _transcriptionCancel = null;
      }
    }
  }

  Future<void> stop() async {
    try {
      await _channel.invokeMethod<void>('stop');
    } on PlatformException {
      // The recognition result reports the actual failure to the pending call.
    } on MissingPluginException {
      // Old APKs can still use typed messages.
    }
  }

  Future<void> cancel() async {
    _generation++;
    _transcriptionCancel?.cancel('本次语音输入已取消');
    _transcriptionCancel = null;
    if (!_disposed) transcribing.value = false;
    try {
      await _channel.invokeMethod<void>('cancel');
    } on PlatformException {
      // Cancellation is best-effort while the activity is going away.
    } on MissingPluginException {
      // No native session was started by an older APK.
    }
  }

  void dispose() {
    _disposed = true;
    unawaited(cancel());
    transcribing.dispose();
  }
}
