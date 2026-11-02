import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

const String apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://127.0.0.1:18110');

/// Identifies the install for guest sessions so feed de-duplication still
/// works before the viewer signs in. Persisted on first launch — call
/// [initDeviceId] before runApp.
const _deviceKey = 'minitv_app_device';
String? _deviceId;

Future<void> initDeviceId() async {
  final prefs = await SharedPreferences.getInstance();
  final saved = prefs.getString(_deviceKey);
  if (saved != null && saved.isNotEmpty) {
    _deviceId = saved;
    return;
  }
  _deviceId = 'app-${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}';
  await prefs.setString(_deviceKey, _deviceId!);
}

String get deviceId => _deviceId ??= 'app-${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}';

class ApiException implements Exception {
  ApiException(this.status, this.message, [this.data]);

  /// HTTP status; 0 means the request never reached the server.
  final int status;
  final String message;

  /// Parsed error body — e.g. the {need, have} map behind a 402.
  final Object? data;

  @override
  String toString() => message;
}

class MiniApi {
  MiniApi({this.token});
  String? token;

  /// The session layer hooks this so a dead token flips the app back to guest.
  static void Function()? onUnauthorized;

  Future<Map<String, dynamic>> getJson(String path) => _send('GET', path, null, null);

  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body, {Map<String, String>? headers}) =>
      _send('POST', path, body, headers);

  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) => _send('PATCH', path, body, null);

  Future<Map<String, dynamic>> _send(String method, String path, Map<String, dynamic>? body, Map<String, String>? headers) async {
    final client = HttpClient();
    try {
      final request = await client.openUrl(method, Uri.parse('$apiBase$path'));
      request.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
      request.headers.set('X-Device-Id', deviceId);
      if (token != null) request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      headers?.forEach(request.headers.set);
      if (body != null) request.add(utf8.encode(jsonEncode(body)));
      final response = await request.close();
      final text = await response.transform(utf8.decoder).join();
      Object? payload;
      try {
        payload = jsonDecode(text);
      } catch (_) {
        /* not JSON */
      }
      if (response.statusCode >= 400) {
        String detail = 'HTTP ${response.statusCode}';
        if (payload is Map && payload['detail'] is String) detail = payload['detail'] as String;
        // A dead session flips the app to guest; a wrong code (401 on /v1/auth/) must not.
        if (response.statusCode == 401 && !path.startsWith('/v1/auth/')) onUnauthorized?.call();
        throw ApiException(response.statusCode, detail, payload);
      }
      if (payload is Map<String, dynamic>) return payload;
      return {'data': payload};
    } on ApiException {
      rethrow;
    } catch (error) {
      // Status 0 means the request never reached the server. Keep the reason in
      // the log — `err_network` alone hides DNS/TLS/VPN split-tunnel failures.
      debugPrint('[api] $method $path failed: $error');
      throw ApiException(0, 'network: $error');
    } finally {
      client.close();
    }
  }
}
