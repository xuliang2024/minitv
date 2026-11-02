import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/player.dart';
import 'package:minitv/screens/episode_player.dart';

/// Records play/pause calls instead of touching a real video surface.
class _FakePlayer extends MiniPlayer {
  int plays = 0;
  int pauses = 0;

  @override
  Future<void> play() async => plays++;

  @override
  Future<void> pause() async => pauses++;
}

void main() {
  testWidgets('tapping the video pauses, tapping again resumes', (tester) async {
    final fake = _FakePlayer();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: EpisodePlayer(dramaId: 1, episodeId: 2, player: fake)),
    ));
    await tester.pump();

    // Playing: no overlay icon.
    expect(find.byIcon(Icons.play_arrow_rounded), findsNothing);

    await tester.tapAt(const Offset(200, 300));
    await tester.pump();
    expect(fake.pauses, 1);
    expect(find.byIcon(Icons.play_arrow_rounded), findsOneWidget);

    await tester.tapAt(const Offset(200, 300));
    await tester.pump();
    expect(fake.plays, 1);
    expect(find.byIcon(Icons.play_arrow_rounded), findsNothing);
  });
}
