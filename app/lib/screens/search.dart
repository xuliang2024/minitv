import 'package:flutter/material.dart';

import '../api.dart';
import '../copy.dart';
import '../img.dart';
import '../ui.dart';

/// Search: tappable trending terms and poster-backed results.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.language, required this.onOpen, this.onBack});

  final String language;
  final void Function(int id) onOpen;
  final VoidCallback? onBack;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final controller = TextEditingController();
  List<Map<String, dynamic>> items = [];
  List<String> trending = [];
  String? error;
  bool searched = false;

  Copy get copy => Copy(widget.language);

  @override
  void initState() {
    super.initState();
    loadTrending();
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  Future<void> loadTrending() async {
    try {
      final data = await MiniApi().getJson('/v1/search/trending');
      trending = ((data['items'] as List?) ?? []).cast<String>();
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() {});
  }

  Future<void> search([String? term]) async {
    final q = (term ?? controller.text).trim();
    if (q.isEmpty) return;
    controller.text = q;
    try {
      final data = await MiniApi().getJson('/v1/search?q=${Uri.encodeQueryComponent(q)}');
      items = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      searched = true;
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: TextField(
            controller: controller,
            autofocus: true,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => search(),
            style: const TextStyle(color: Colors.white),
            decoration: InputDecoration(
              hintText: copy.t('search'),
              hintStyle: const TextStyle(color: Colors.white38),
              prefixIcon: const Icon(Icons.search, color: Colors.white38, size: 20),
              filled: true,
              fillColor: const Color(0xFF241A1D),
              contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(22), borderSide: BorderSide.none),
            ),
          ),
        ),
        if (error != null) TextButton(onPressed: loadTrending, child: Text(copy.t('retry'))),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
            children: [
              if (trending.isNotEmpty && !searched) ...[
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final term in trending)
                      GestureDetector(
                        onTap: () => search(term),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                          decoration: BoxDecoration(
                            color: const Color(0xFF241A1D),
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: gold.withValues(alpha: 0.25)),
                          ),
                          child: Text(term, style: const TextStyle(color: gold, fontSize: 12.5)),
                        ),
                      ),
                  ],
                ),
              ],
              for (final item in items)
                GestureDetector(
                  onTap: () => widget.onOpen(item['id'] as int),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 48,
                          height: 64,
                          child: PosterImage(url: item['cover_url'] as String?, imgWidth: Img.rank),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            item['title'] as String? ?? '',
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
                          ),
                        ),
                        const Icon(Icons.chevron_right, color: Colors.white38, size: 18),
                      ],
                    ),
                  ),
                ),
              if (searched && items.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 60),
                  child: Center(child: Text(copy.t('empty'), style: const TextStyle(color: Colors.white54))),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
