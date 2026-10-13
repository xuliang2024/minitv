import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../ui.dart';

class ChannelScreen extends StatefulWidget {
  const ChannelScreen({super.key, required this.titleKey, required this.onOpen});
  final String titleKey;
  final void Function(int id) onOpen;
  @override
  State<ChannelScreen> createState() => _ChannelScreenState();
}

class _ChannelScreenState extends State<ChannelScreen> {
  final api = MiniApi();
  List<Map<String, dynamic>> items = [];
  String? error;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() { loading = true; error = null; });
    try {
      final home = await api.getJson('/v1/home');
      final list = (home['items'] as List?) ?? [];
      items = list.cast<Map<String, dynamic>>();
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() { loading = false; });
  }

  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    if (loading) return const Center(child: CircularProgressIndicator(color: red));
    if (error != null) return RetryBody(label: copy.t('retry'), onRetry: load);
    if (items.isEmpty) return Center(child: Text(copy.t('empty'), style: const TextStyle(color: Colors.white70)));
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 3, childAspectRatio: 0.62, crossAxisSpacing: 8, mainAxisSpacing: 8),
      itemCount: items.length,
      itemBuilder: (_, i) {
        final item = items[i];
        return InkWell(
          onTap: () => widget.onOpen(item['id'] as int),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Poster(url: item['cover_url'] as String?),
            const SizedBox(height: 6),
            Text(item['title'] as String? ?? '', maxLines: 2, style: const TextStyle(color: Colors.white, fontSize: 12)),
          ]),
        );
      },
    );
  }
}
