import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:minitv/screens/detail.dart';
import 'package:minitv/session.dart';
import 'package:minitv/ui.dart';

const _master = '''
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=682816,RESOLUTION=198x352
240p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1430680,RESOLUTION=360x640
360p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2007840,RESOLUTION=480x854
480p/video.m3u8
''';

Map<String, dynamic> get _play => {
      'drama': {
        'id': 1,
        'title': 'A Drama With A Long Title',
        'cover_url': '',
        'synopsis': 'A short synopsis for the sheet.',
        'tags': ['Rebirth'],
      },
      'items': [
        {'id': 2, 'episode_number': 1, 'locked': false, 'progress_seconds': 0},
      ],
      'resume': {},
    };

class _FakeHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _FakeClient();
}

class _FakeClient extends Fake implements HttpClient {
  @override
  Future<HttpClientRequest> getUrl(Uri url) => openUrl('GET', url);

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    final path = url.path;
    final Object body;
    if (path.endsWith('playlist.m3u8')) {
      body = _master;
    } else if (path.startsWith('/v1/dramas/')) {
      body = _play; // /v1/dramas/{id}/play
    } else {
      body = {
        'allowed': true,
        'url': 'https://vz.test/tok/vid/playlist.m3u8',
        'expires_at': 0,
        'renditions': ['240P', '360P', '480P'],
      };
    }
    // The playlist is raw text; only the JSON routes get encoded.
    return _FakeRequest(body is String ? body : jsonEncode(body));
  }

  @override
  void close({bool force = false}) {}
}

class _FakeRequest extends Fake implements HttpClientRequest {
  _FakeRequest(this.body);
  final String body;
  @override
  final headers = _FakeHeaders();
  @override
  void add(List<int> data) {}
  @override
  Future<HttpClientResponse> close() async => _FakeResponse(body);
}

class _FakeHeaders extends Fake implements HttpHeaders {
  @override
  void set(String name, Object value, {bool preserveHeaderCase = false}) {}
}

class _FakeResponse extends Fake implements HttpClientResponse {
  _FakeResponse(this.body);
  final String body;
  @override
  int get statusCode => 200;
  @override
  Stream<S> transform<S>(StreamTransformer<List<int>, S> streamTransformer) =>
      Stream<List<int>>.value(utf8.encode(body)).transform(streamTransformer);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _FakeHttpOverrides();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('golden: playback settings offer real quality rungs', (tester) async {
    final session = Session();
    session.token = 'test-token';
    session.booting = false;
    session.user = const {
      'id': 1,
      'public_id': '1',
      'display_name': 'Ava Patel',
      'avatar_url': '',
      'coins': 420,
      'membership': {'active': false},
    };

    await tester.pumpWidget(SessionScope(
      session: session,
      child: MaterialApp(
        theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
        home: const Scaffold(body: DetailScreen(dramaId: 1, language: 'en')),
      ),
    ));
    await tester.pumpAndSettle();

    // The gear opens the settings sheet.
    await tester.tap(find.byIcon(Icons.tune));
    await tester.pumpAndSettle();

    expect(find.text('Speed'), findsOneWidget);
    expect(find.text('Quality'), findsOneWidget);
    expect(find.text('Auto'), findsOneWidget);
    expect(find.text('240P'), findsOneWidget);
    expect(find.text('360P'), findsOneWidget);
    expect(find.text('480P'), findsOneWidget);

    await expectLater(find.byType(Scaffold), matchesGoldenFile('playback_settings.png'));
  });
}
