import 'dart:convert';
import 'dart:io';

const String apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://127.0.0.1:18110');

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

class MiniApi {
  MiniApi({this.token});
  String? token;

  Future<Map<String, dynamic>> getJson(String path) => _send('GET', path, null);
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) => _send('POST', path, body);

  Future<Map<String, dynamic>> _send(String method, String path, Map<String, dynamic>? body) async {
    final client = HttpClient();
    try {
      final request = await client.openUrl(method, Uri.parse('$apiBase$path'));
      request.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
      if (token != null) request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      if (body != null) request.add(utf8.encode(jsonEncode(body)));
      final response = await request.close();
      final text = await response.transform(utf8.decoder).join();
      if (response.statusCode >= 400) {
        throw ApiException('HTTP ${response.statusCode}');
      }
      final decoded = jsonDecode(text);
      return decoded is Map<String, dynamic> ? decoded : {'data': decoded};
    } on ApiException {
      rethrow;
    } catch (error) {
      throw ApiException('network');
    } finally {
      client.close();
    }
  }
}
