import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../ui.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.onOpen});
  final void Function(int id) onOpen;
  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final controller = TextEditingController();
  List<Map<String, dynamic>> items = [];
  List<String> trending = [];
  String? error;
  @override
  void initState() { super.initState(); loadTrending(); }
  Future<void> loadTrending() async {
    try {
      final data = await MiniApi().getJson('/v1/search/trending');
      trending = ((data['items'] as List?) ?? []).cast<String>();
      error = null;
    } catch (_) { error = 'network'; }
    if (mounted) setState(() {});
  }
  Future<void> search() async {
    try {
      final data = await MiniApi().getJson('/v1/search?q=${Uri.encodeQueryComponent(controller.text)}');
      items = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      error = null;
    } catch (_) { error = 'network'; }
    if (mounted) setState(() {});
  }
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(children: [
        TextField(
          controller: controller,
          style: const TextStyle(color: Colors.white),
          decoration: InputDecoration(hintText: copy.t('search'), hintStyle: const TextStyle(color: Colors.white38)),
          onSubmitted: (_) => search(),
        ),
        if (error != null) TextButton(onPressed: loadTrending, child: Text(copy.t('retry'))),
        Expanded(child: ListView(children: [
          ...trending.map((term) => ListTile(title: Text(term, style: const TextStyle(color: gold)))),
          ...items.map((item) => ListTile(
            title: Text(item['title'] as String? ?? '', style: const TextStyle(color: Colors.white)),
            onTap: () => widget.onOpen(item['id'] as int),
          )),
        ])),
      ]),
    );
  }
}
