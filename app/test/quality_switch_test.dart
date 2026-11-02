import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/player.dart';
import 'package:minitv/screens/episode_player.dart';
import 'package:video_player/video_player.dart';

const _master = '''
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1430680,RESOLUTION=360x640
360p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2007840,RESOLUTION=480x854
480p/video.m3u8
''';

class _FakeHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _FakeClient();
}

class _FakeClient extends Fake implements HttpClient {
  @override
  Future<HttpClientRequest> getUrl(Uri url) => openUrl('GET', url);

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    final body = url.path.endsWith('playlist.m3u8')
        ? _master
        : jsonEncode({
            'allowed': true,
            'url': 'https://vz.test/tok/vid/playlist.m3u8',
            'expires_at': 0,
            'renditions': ['360P', '480P'],
          });
    return _FakeRequest(body);
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

/// Records what the player actually loads so the switch is observable.
class _RecordingPlayer extends MiniPlayer {
  final loaded = <String>[];
  final seeks = <Duration>[];

  @override
  Future<void> load(String url) async => loaded.add(url);
  @override
  Future<void> play() async {}
  @override
  Future<void> pause() async {}
  @override
  Future<void> seek(Duration position) async => seeks.add(position);
  @override
  Future<void> setSpeed(double speed) async {}
  @override
  Future<void> release() async {}
  @override
  Duration get position => const Duration(seconds: 42);
  @override
  Duration get duration => const Duration(seconds: 78);
  @override
  bool get isPlaying => true;
  @override
  VideoPlayerController? get controller => null;
  @override
  void addListener(void Function() listener) {}
  @override
  void removeListener(void Function() listener) {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _FakeHttpOverrides();

  testWidgets('picking a quality reloads that exact variant and keeps the playhead', (tester) async {
    final player = _RecordingPlayer();
    var qualities = <String>[];

    Widget build(String quality) => MaterialApp(
          home: Scaffold(
            body: EpisodePlayer(
              dramaId: 1,
              episodeId: 2,
              player: player,
              quality: quality,
              onQualities: (labels) => qualities = labels,
            ),
          ),
        );

    await tester.pumpWidget(build('auto'));
    await tester.pumpAndSettle();

    // Auto → the master playlist, and the ladder is reported to the sheet.
    expect(player.loaded.single, 'https://vz.test/tok/vid/playlist.m3u8');
    expect(qualities, ['360P', '480P']);

    // Pin 480P mid-episode.
    await tester.pumpWidget(build('480P'));
    await tester.pumpAndSettle();

    expect(player.loaded.last, 'https://vz.test/tok/vid/480p/video.m3u8');
    expect(player.seeks.last, const Duration(seconds: 42));

    // Back to Auto → master again, still at the same position.
    await tester.pumpWidget(build('auto'));
    await tester.pumpAndSettle();
    expect(player.loaded.last, 'https://vz.test/tok/vid/playlist.m3u8');
    expect(player.seeks.last, const Duration(seconds: 42));
  });

  testWidgets('an unknown quality falls back to auto instead of a blank surface', (tester) async {
    final player = _RecordingPlayer();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: EpisodePlayer(dramaId: 1, episodeId: 2, player: player, quality: '1080P'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(player.loaded.single, 'https://vz.test/tok/vid/playlist.m3u8');
  });
}
