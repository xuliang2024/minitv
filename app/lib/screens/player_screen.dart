import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../player.dart';
import '../ui.dart';

class PlayerScreen extends StatefulWidget {
  const PlayerScreen({super.key, required this.dramaId, required this.episodeId, this.token});
  final int dramaId;
  final int episodeId;
  final String? token;
  @override
  State<PlayerScreen> createState() => _PlayerScreenState();
}

class _PlayerScreenState extends State<PlayerScreen> {
  final player = MiniPlayer();
  Map<String, dynamic>? auth;
  String? error;
  String speed = '1x';
  String quality = '720P';
  bool showSheet = false;

  @override
  void initState() { super.initState(); load(); }
  @override
  void dispose() { player.release(); super.dispose(); }

  Future<void> load() async {
    try {
      final api = MiniApi(token: widget.token);
      final feed = widget.episodeId == 0 ? await api.getJson('/v1/feed') : null;
      final dramaId = widget.episodeId == 0 ? (feed?['items'] as List).first['drama_id'] : widget.dramaId;
      final episodeId = widget.episodeId == 0 ? (feed?['items'] as List).first['episode_id'] : widget.episodeId;
      auth = await api.postJson('/v1/playback', {'drama_id': dramaId, 'episode_id': episodeId});
      error = null;
      if (auth?['allowed'] == true) {
        await player.load(auth!['url'] as String);
        await player.play();
      }
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    if (error != null) return RetryBody(label: copy.t('retry'), onRetry: load);
    if (auth == null) return const Center(child: CircularProgressIndicator(color: red));
    if (auth!['allowed'] != true) {
      return Center(child: Text(copy.t('locked'), style: const TextStyle(color: gold, fontSize: 18)));
    }
    return Stack(children: [
      const Positioned.fill(child: ColoredBox(color: Colors.black)),
      Positioned(left: 16, right: 72, bottom: 24, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(auth!['title'] as String? ?? '', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
        Text('Episode ${auth!['episode_number']}', style: const TextStyle(color: Colors.white70)),
        TextButton(onPressed: () => setState(() => showSheet = !showSheet), child: const Text('Episodes', style: TextStyle(color: Colors.white))),
      ])),
      if (showSheet) Align(alignment: Alignment.bottomCenter, child: Container(
        color: const Color(0xFF171013),
        padding: const EdgeInsets.all(16),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Wrap(spacing: 8, children: ['1x', '1.25x', '1.5x', '2x'].map((item) => ChoiceChip(label: Text(item), selected: speed == item, onSelected: (_) async { setState(() => speed = item); })).toList()),
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: ((auth!['renditions'] as List?) ?? []).map((item) => ChoiceChip(label: Text('$item'), selected: quality == item, onSelected: (_) async { setState(() => quality = '$item'); await player.setQuality('$item'); })).toList()),
          const Text('Download unavailable', style: TextStyle(color: Colors.white38)),
        ]),
      )),
    ]);
  }
}
