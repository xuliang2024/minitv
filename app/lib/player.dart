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

  /// Playback rate. 1.0 is normal speed.
  Future<void> setSpeed(double speed) => _controller?.setPlaybackSpeed(speed) ?? Future.value();

  Future<void> release() async {
    final current = _controller;
    _controller = null;
    await current?.dispose();
  }

  VideoPlayerController? get controller => _controller;

  void addListener(void Function() listener) => _controller?.addListener(listener);
  void removeListener(void Function() listener) => _controller?.removeListener(listener);

  Duration get position => _controller?.value.position ?? Duration.zero;
  Duration get duration => _controller?.value.duration ?? Duration.zero;
  bool get isPlaying => _controller?.value.isPlaying ?? false;

  /// True once the playhead is within 1.5s of the end (player UIs rarely emit
  /// an exact end position).
  bool get isFinished {
    final value = _controller?.value;
    if (value == null || value.duration <= Duration.zero) return false;
    return value.isCompleted || value.position >= value.duration - const Duration(milliseconds: 1500);
  }
}
