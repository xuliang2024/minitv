import 'package:flutter/material.dart';

import '../api.dart';
import '../copy.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';

const _pageSize = 20;
const _maxLength = 500;

/// One thread per episode — the App twin of H5 `ui/CommentSheet.tsx`.
/// Opening a different episode remounts the sheet (fresh state).
class CommentSheet extends StatefulWidget {
  const CommentSheet({
    super.key,
    required this.session,
    required this.language,
    required this.episodeId,
    this.initialCount = 0,
    this.onCountChange,
    required this.onClose,
  });

  final Session session;
  final String language;
  final int episodeId;
  final int initialCount;
  final void Function(int total)? onCountChange;
  final VoidCallback onClose;

  @override
  State<CommentSheet> createState() => _CommentSheetState();
}

class _CommentSheetState extends State<CommentSheet> {
  final List<Map<String, dynamic>> items = [];
  final composer = TextEditingController();
  late int total = widget.initialCount;
  int? cursor;
  bool loading = true;
  bool loadingMore = false;
  bool posting = false;

  Copy get copy => Copy(widget.language);
  MiniApi get api => widget.session.api;

  void publish(int next) {
    total = next;
    widget.onCountChange?.call(next);
  }

  @override
  void initState() {
    super.initState();
    fetchPage(reset: true);
  }

  @override
  void dispose() {
    composer.dispose();
    super.dispose();
  }

  Future<void> fetchPage({required bool reset}) async {
    setState(() {
      if (reset) {
        loading = true;
      } else {
        loadingMore = true;
      }
    });
    try {
      final query = cursor == null || reset ? '' : '?cursor=$cursor&limit=$_pageSize';
      final path = reset ? '/v1/episodes/${widget.episodeId}/comments?limit=$_pageSize' : '/v1/episodes/${widget.episodeId}/comments$query';
      final page = await api.getJson(path);
      final next = ((page['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      if (reset) {
        items
          ..clear()
          ..addAll(next);
      } else {
        items.addAll(next);
      }
      cursor = page['next_cursor'] as int?;
      publish((page['total'] as int?) ?? total);
    } on ApiException {
      if (mounted) toast(context, copy.t('err_comments_load'));
    } finally {
      if (mounted) {
        setState(() {
          loading = false;
          loadingMore = false;
        });
      }
    }
  }

  Future<void> submit() async {
    final body = composer.text.trim();
    if (body.isEmpty || posting) return;
    widget.session.requireAuth(() async {
      setState(() => posting = true);
      try {
        final result = await api.postJson('/v1/episodes/${widget.episodeId}/comments', {'body': body});
        composer.clear();
        // The author's view is identical to a published comment; the server
        // decides visibility for everyone else (async moderation).
        final item = result['item'];
        if (item is Map<String, dynamic>) {
          setState(() => items.insert(0, item));
          publish((result['total'] as int?) ?? total + 1);
        }
      } on ApiException catch (error) {
        if (mounted) toast(context, copy.t(error.status == 429 ? 'comment_too_fast' : 'err_comment'));
      } finally {
        if (mounted) setState(() => posting = false);
      }
    });
  }

  Future<void> toggleLike(Map<String, dynamic> item) async {
    final liked = item['liked'] == true;
    widget.session.requireAuth(() async {
      void apply(bool value) {
        item['liked'] = value;
        item['like_count'] = ((item['like_count'] as int?) ?? 0) + (value ? 1 : -1);
        if ((item['like_count'] as int) < 0) item['like_count'] = 0;
      }

      setState(() => apply(!liked));
      try {
        await api.postJson('/v1/reactions', {
          'target_type': 'comment',
          'target_id': item['id'],
          'kind': 'like',
          'remove': liked,
        });
      } on ApiException {
        setState(() => apply(liked));
        if (mounted) toast(context, copy.t('err_reaction'));
      }
    });
  }

  String timeAgo(String? iso) {
    if (iso == null || iso.isEmpty) return '';
    final date = DateTime.tryParse(iso);
    if (date == null) return '';
    final seconds = DateTime.now().difference(date.toLocal()).inSeconds.abs();
    if (seconds < 60) return copy.t('just_now');
    if (seconds < 3600) return copy.t('minutes_ago', {'n': seconds ~/ 60});
    if (seconds < 86400) return copy.t('hours_ago', {'n': seconds ~/ 3600});
    if (seconds < 604800) return copy.t('days_ago', {'n': seconds ~/ 86400});
    final local = date.toLocal();
    return '${local.year}-${local.month.toString().padLeft(2, '0')}-${local.day.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return AppSheet(
      title: total > 0 ? '${copy.t('comments')} · ${formatCount(total, zh: copy.zh)}' : copy.t('comments'),
      onClose: widget.onClose,
      heightFactor: 0.72,
      child: Column(
        children: [
          Expanded(
            child: loading && items.isEmpty
                ? const Center(child: CircularProgressIndicator(color: red))
                : items.isEmpty
                    ? Center(child: Text(copy.t('comments_empty'), style: const TextStyle(color: Colors.white54, fontSize: 13)))
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                        itemCount: items.length + (cursor != null ? 1 : 0),
                        itemBuilder: (_, i) {
                          if (i == items.length) {
                            return TextButton(
                              onPressed: loadingMore ? null : () => fetchPage(reset: false),
                              child: Text(
                                loadingMore ? '…' : copy.t('comments_more'),
                                style: const TextStyle(color: gold, fontSize: 13),
                              ),
                            );
                          }
                          return _commentRow(items[i]);
                        },
                      ),
          ),
          _composerBar(),
        ],
      ),
    );
  }

  Widget _commentRow(Map<String, dynamic> item) {
    final user = (item['user'] as Map?)?.cast<String, dynamic>() ?? const {};
    final liked = item['liked'] == true;
    final likeCount = (item['like_count'] as int?) ?? 0;
    final mine = item['mine'] == true;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 9),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          UserAvatar(url: user['avatar_url'] as String?, name: user['display_name'] as String? ?? '', size: 34, radius: 17),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        user['display_name'] as String? ?? '',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white70, fontSize: 12.5, fontWeight: FontWeight.w600),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(timeAgo(item['created_at'] as String?), style: const TextStyle(color: Colors.white38, fontSize: 11)),
                    if (mine) ...[
                      const SizedBox(width: 6),
                      Text(copy.t('comment_you'), style: const TextStyle(color: gold, fontSize: 11, fontWeight: FontWeight.w600)),
                    ],
                  ],
                ),
                const SizedBox(height: 3),
                Text(item['body'] as String? ?? '', style: const TextStyle(color: Colors.white, fontSize: 13.5, height: 1.35)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: () => toggleLike(item),
            child: Column(
              children: [
                Icon(liked ? Icons.favorite : Icons.favorite_border, color: liked ? gold : Colors.white38, size: 16),
                if (likeCount > 0)
                  Text(formatCount(likeCount, zh: copy.zh), style: const TextStyle(color: Colors.white38, fontSize: 10)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _composerBar() {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 16, 10),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: composer,
                maxLength: _maxLength,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => submit(),
                style: const TextStyle(color: Colors.white, fontSize: 14),
                decoration: InputDecoration(
                  hintText: copy.t('comment_placeholder'),
                  hintStyle: const TextStyle(color: Colors.white38),
                  counterText: '',
                  filled: true,
                  fillColor: const Color(0xFF241A1D),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(20), borderSide: BorderSide.none),
                ),
                onChanged: (_) => setState(() {}),
              ),
            ),
            const SizedBox(width: 10),
            GestureDetector(
              onTap: composer.text.trim().isEmpty || posting ? null : submit,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
                decoration: BoxDecoration(
                  color: composer.text.trim().isEmpty ? const Color(0x22FFFFFF) : red,
                  borderRadius: BorderRadius.circular(18),
                ),
                child: Text(
                  posting ? copy.t('sending') : copy.t('send'),
                  style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
