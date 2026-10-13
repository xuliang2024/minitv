import 'package:video_player/video_player.dart';

/// Playback surface used by For You and the player sheets.
/// Token refresh stays outside this class.
class MiniPlayer {
  VideoPlayerController? _controller;

  Future<void> load(String url) async {
    await release();
    final controller = VideoPlayerController.networkUrl(Uri.parse(url));
    _controller = controller;
    await controller.initialize();
  }

  Future<void> play() => _controller?.play() ?? Future.value();
  Future<void> pause() => _controller?.pause() ?? Future.value();
  Future<void> seek(Duration position) => _controller?.seekTo(position) ?? Future.value();
  Future<void> setQuality(String quality) async {}
  Future<void> setCaptions(bool enabled) async {}

  Future<void> release() async {
    final current = _controller;
    _controller = null;
    await current?.dispose();
  }

  VideoPlayerController? get controller => _controller;
}
