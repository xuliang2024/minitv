import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../ui.dart';

class DetailScreen extends StatefulWidget {
  const DetailScreen({super.key, required this.dramaId, required this.onPlay});
  final int dramaId;
  final void Function(int dramaId, int episodeId) onPlay;
  @override
  State<DetailScreen> createState() => _DetailScreenState();
}

class _DetailScreenState extends State<DetailScreen> {
  Map<String, dynamic>? drama;
  String? error;
  @override
  void initState() { super.initState(); load(); }
  Future<void> load() async {
    try {
      drama = await MiniApi().getJson('/v1/dramas/${widget.dramaId}');
      error = null;
    } catch (_) { error = 'network'; }
    if (mounted) setState(() {});
  }
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    if (error != null) return RetryBody(label: copy.t('retry'), onRetry: load);
    if (drama == null) return const Center(child: CircularProgressIndicator(color: red));
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(drama!['title'] as String? ?? '', style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(drama!['synopsis'] as String? ?? '', style: const TextStyle(color: Colors.white70)),
        const Spacer(),
        SizedBox(width: double.infinity, child: FilledButton(
          style: FilledButton.styleFrom(backgroundColor: red),
          onPressed: () => widget.onPlay(widget.dramaId, 0),
          child: Text(copy.t('play')),
        )),
      ]),
    );
  }
}
