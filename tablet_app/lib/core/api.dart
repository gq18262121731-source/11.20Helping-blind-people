import 'dart:typed_data';

import 'package:dio/dio.dart';

String normalizeServerUrl(String input) {
  final value = input.trim();
  final uri = Uri.tryParse(value);
  if (uri == null ||
      !['http', 'https'].contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      (uri.path.isNotEmpty && uri.path != '/')) {
    throw const FormatException('请输入服务器根地址，如 http://192.168.1.10:8765');
  }
  if (['0.0.0.0', '::'].contains(uri.host)) {
    throw const FormatException('0.0.0.0 是监听地址，请填写电脑的局域网 IP');
  }
  return value.replaceFirst(RegExp(r'/+$'), '');
}

class ApiFailure implements Exception {
  const ApiFailure(this.message, [this.status]);
  final String message;
  final int? status;
  @override
  String toString() => message;
}

class QimingApi {
  QimingApi(String baseUrl, {Dio? dio}) : dio = dio ?? Dio() {
    this.dio.options = BaseOptions(
      baseUrl: normalizeServerUrl(baseUrl),
      connectTimeout: const Duration(seconds: 8),
      sendTimeout: const Duration(seconds: 20),
      receiveTimeout: const Duration(seconds: 15),
    );
  }
  final Dio dio;
  String? token;
  void Function()? onUnauthorized;

  String get baseUrl => dio.options.baseUrl;
  Uri get socketUri {
    final uri = Uri.parse(baseUrl);
    return uri.replace(
      scheme: uri.scheme == 'https' ? 'wss' : 'ws',
      path: '/ws/tablet',
    );
  }

  Future<dynamic> request(
    String path, {
    String method = 'GET',
    dynamic data,
    bool authenticated = true,
    CancelToken? cancelToken,
    Duration? timeout,
    ResponseType? responseType,
    String? contentType,
  }) async {
    try {
      final response = await dio.request<dynamic>(
        path,
        data: data,
        cancelToken: cancelToken,
        options: Options(
          method: method,
          receiveTimeout: timeout,
          responseType: responseType,
          contentType:
              contentType ??
              (data is Uint8List ? 'image/jpeg' : Headers.jsonContentType),
          headers: authenticated && token != null
              ? {'Authorization': 'Bearer $token'}
              : null,
        ),
      );
      return response.data;
    } on DioException catch (error) {
      if (CancelToken.isCancel(error)) rethrow;
      final status = error.response?.statusCode;
      if (status == 401 && authenticated) onUnauthorized?.call();
      if (status == 404) {
        throw const ApiFailure('电脑后端缺少此接口，请更新代码并用 yolo-blind 环境重启服务。', 404);
      }
      final body = error.response?.data;
      final detail = body is Map ? body['detail'] : null;
      if (detail is String) throw ApiFailure(detail, status);
      if (status != null) {
        throw ApiFailure('服务器返回错误（$status），请检查电脑后端日志。', status);
      }
      if ([
        DioExceptionType.receiveTimeout,
        DioExceptionType.sendTimeout,
        DioExceptionType.connectionTimeout,
      ].contains(error.type)) {
        throw const ApiFailure('请求超时，请检查电脑、局域网连接和模型运行状态。');
      }
      throw const ApiFailure('无法连接电脑后端，请检查服务器地址、同一 Wi-Fi 和防火墙。');
    }
  }

  Future<Map<String, dynamic>> map(
    String path, {
    String method = 'GET',
    dynamic data,
    bool authenticated = true,
    CancelToken? cancelToken,
    Duration? timeout,
    String? contentType,
  }) async {
    final value = await request(
      path,
      method: method,
      data: data,
      authenticated: authenticated,
      cancelToken: cancelToken,
      timeout: timeout,
      contentType: contentType,
    );
    if (value is! Map) throw const ApiFailure('接口格式不正确，请检查服务器地址和后端版本。');
    return Map<String, dynamic>.from(value);
  }

  void dispose() => dio.close(force: true);
}
