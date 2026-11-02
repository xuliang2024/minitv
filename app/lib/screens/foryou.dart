import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../api.dart';
import '../copy.dart';
import '../img.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';
import 'comments.dart';
import 'episode_player.dart';

/// For You: one vertical page == one drama, opened on the episode that drama
/// should be entered through.
///
/// Visual language follows the design draft (P6 播放器流): full-bleed video,
/// no app bar; the action rail (avatar, like, comment, share) floats on the
/// right; the drama title, an episode chip, hashtag tags and a white 选集 pill
/// sit above a thin playback progress bar at the bottom.
///
/// Swiping up means "next show", not "next episode" - the drama is the unit of
/// the feed. Continuing a drama happens on its own play page, reached from the
/// 选集 pill or the overlay shown once a feed episode finishes.
const int feedPageSize = 5;

/// Fetch the following page once this many slots are left.
const int feedPreloadRemaining = 2;

class ForYouScreen extends StatefulWidget {
  const ForYouScreen({
    super.key,
    this.language = 'en',
    required this.onOpenDrama,
  });

  final String language;
  final void Function(int dramaId) onOpenDrama;

  @override
  State<ForYouScreen> createState() => _ForYouScreenState();
}

class _ForYouScreenState extends State<ForYouScreen> {
  final List<Map<String, dynamic>> items = [];
  final PageController controller = PageController();
  String? cursor;
  bool loading = true;
  bool hasMore = true;
  String? error;
  int index = 0;

  @override
  void initState() {
    super.initState();
    // SessionScope lookups are only legal after initState, so defer the load.
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadMore());
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  Future<void> _loadMore() async {
    if (!hasMore) return;
    if (loading && items.isNotEmpty) return; // a fetch is already in flight
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final token = SessionScope.maybeOf(context)?.token;
      final query = StringBuffer('/v1/feed?limit=$feedPageSize');
      if (cursor != null && cursor!.isNotEmpty) query.write('&cursor=$cursor');
      final page = await MiniApi(token: token).getJson(query.toString());
      final known = items.map((item) => item['item_id']).toSet();
      for (final item in ((page['items'] as List?) ?? []).cast<Map<String, dynamic>>()) {
        if (known.contains(item['item_id'])) continue;
        items.add(item);
      }
      cursor = page['next_cursor'] as String?;
      hasMore = page['has_more'] == true && (cursor ?? '').isNotEmpty;
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> refresh() async {
    items.clear();
    cursor = null;
    hasMore = true;
    await _loadMore();
  }

  void _onPageChanged(int value) {
    index = value;
    // Preload: two slots left means the request should already be on its way.
    if (hasMore && items.length - value - 1 <= feedPreloadRemaining) _loadMore();
  }

  @override
  Widget build(BuildContext context) {
    final copy = Copy(widget.language);
    if (error != null && items.isEmpty) {
      return RetryBody(label: copy.t('retry'), onRetry: refresh);
    }
    if (items.isEmpty) {
      return loading
          ? const Center(child: CircularProgressIndicator(color: red))
          : Center(child: Text(copy.t('empty'), style: const TextStyle(color: Colors.white70)));
    }
    return RefreshIndicator(
      color: red,
      onRefresh: refresh,
      child: PageView.builder(
        controller: controller,
        scrollDirection: Axis.vertical,
        itemCount: items.length,
        onPageChanged: _onPageChanged,
        itemBuilder: (context, position) => _FeedSlot(
          item: items[position],
          active: position == index,
          language: widget.language,
          onOpenDrama: widget.onOpenDrama,
        ),
      ),
    );
  }
}

/// One feed page: video, right action rail, bottom info and the finish overlay
/// that hands the viewer off to the drama's play page.
class _FeedSlot extends StatefulWidget {
  const _FeedSlot({
    required this.item,
    required this.active,
    required this.language,
    required this.onOpenDrama,
  });

  final Map<String, dynamic> item;
  final bool active;
  final String language;
  final void Function(int dramaId) onOpenDrama;

  @override
  State<_FeedSlot> createState() => _FeedSlotState();
}

class _FeedSlotState extends State<_FeedSlot> {
  bool finished = false;
  late bool liked;
  late int likeCount;
  late int commentCount;
  bool commentOpen = false;

  bool get zh => widget.language == 'zh';
  Copy get copy => Copy(widget.language);
  Map<String, dynamic> get drama => widget.item['drama'] as Map<String, dynamic>;
  Map<String, dynamic> get episode => widget.item['episode'] as Map<String, dynamic>;
  int get dramaId => drama['id'] as int;
  int get episodeId => episode['id'] as int;

  @override
  void initState() {
    super.initState();
    _readPayload();
  }

  @override
  void didUpdateWidget(covariant _FeedSlot oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.item['item_id'] != oldWidget.item['item_id']) {
      finished = false;
      commentOpen = false;
      _readPayload();
    }
  }

  /// The feed payload already carries the viewer's reaction state — trust it
  /// instead of a separate round trip.
  void _readPayload() {
    liked = drama['liked'] == true;
    likeCount = (drama['like_count'] as int?) ?? (drama['play_count'] as int?) ?? 0;
    commentCount = (episode['comment_count'] as int?) ?? 0;
  }

  void _toggleLike() {
    final session = SessionScope.maybeOf(context);
    if (session == null) return;
    session.requireAuth(() async {
      final next = !liked;
      void apply(bool value) {
        liked = value;
        likeCount = (likeCount + (value ? 1 : -1)).clamp(0, 1 << 31);
      }

      setState(() => apply(next));
      try {
        await MiniApi(token: session.token).postJson('/v1/reactions', {
          'target_type': 'drama',
          'target_id': dramaId,
          'kind': 'like',
          'remove': !next,
        });
      } on ApiException {
        if (!mounted) return;
        setState(() => apply(!next));
        toast(context, copy.t('err_reaction'));
      }
    });
  }

  void _share() {
    final title = drama['title'] as String? ?? 'MiniTV';
    SharePlus.instance.share(ShareParams(text: '$title\nhttps://h5.minitv.us', subject: title));
  }

  @override
  Widget build(BuildContext context) {
    final session = SessionScope.maybeOf(context);
    final tags = ((drama['tags'] as List?) ?? []).map((tag) => '#$tag').join(' ');
    final count = (drama['published_episode_count'] as int?) ?? 0;
    return ColoredBox(
      color: Colors.black,
      child: Stack(
        fit: StackFit.expand,
        children: [
          EpisodePlayer(
            dramaId: dramaId,
            episodeId: episodeId,
            resumeSeconds: (widget.item['resume_seconds'] as int?) ?? 0,
            active: widget.active,
            token: session?.token,
            showProgressBar: true,
            onFinished: () => setState(() => finished = true),
          ),
          // Bottom scrim so the title and rail stay readable over bright frames.
          const Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            height: 220,
            child: IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.bottomCenter,
                    end: Alignment.topCenter,
                    colors: [Color(0x99000000), Colors.transparent],
                  ),
                ),
              ),
            ),
          ),
          // Right action rail: avatar, like, comment, share.
          Positioned(
            right: 6,
            bottom: 128,
            child: _ActionRail(
              coverUrl: drama['cover_url'] as String?,
              likes: formatCount(likeCount, zh: zh),
              comments: commentCount > 0 ? formatCount(commentCount, zh: zh) : copy.t('comment'),
              liked: liked,
              onLike: _toggleLike,
              onComment: () => setState(() => commentOpen = true),
              onShare: _share,
              onAvatar: () => widget.onOpenDrama(dramaId),
            ),
          ),
          // Bottom info: title, episode chip + hashtags.
          Positioned(
            left: 12,
            right: 86,
            bottom: 34,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  drama['title'] as String? ?? '',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        copy.t('episode_chip', {'number': episode['episode_number']}),
                        style: const TextStyle(color: Colors.black, fontSize: 11, fontWeight: FontWeight.w700),
                      ),
                    ),
                    if (tags.isNotEmpty) ...[
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          tags,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: Colors.white70, fontSize: 12),
                        ),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          // 选集 pill: opens the drama's own episode scroller.
          Positioned(
            right: 10,
            bottom: 34,
            child: GestureDetector(
              onTap: () => widget.onOpenDrama(dramaId),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.list, size: 15, color: Colors.black),
                    const SizedBox(width: 4),
                    Text(
                      copy.t('select'),
                      style: const TextStyle(color: Colors.black, fontSize: 12, fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
            ),
          ),
          if (finished)
            Positioned(
              right: 10,
              bottom: 132,
              child: FilledButton.icon(
                style: FilledButton.styleFrom(backgroundColor: red),
                onPressed: () => widget.onOpenDrama(dramaId),
                icon: const Icon(Icons.playlist_play, size: 18),
                label: Text(
                  copy.t('all_episodes', {'count': count}),
                  style: const TextStyle(fontSize: 12),
                ),
              ),
            ),
          if (commentOpen && session != null)
            Positioned.fill(
              child: CommentSheet(
                key: ValueKey('feed-comments-$episodeId'),
                session: session,
                language: widget.language,
                episodeId: episodeId,
                initialCount: commentCount,
                onCountChange: (next) => setState(() => commentCount = next),
                onClose: () => setState(() => commentOpen = false),
              ),
            ),
        ],
      ),
    );
  }
}

/// The floating action rail on the right edge.
class _ActionRail extends StatelessWidget {
  const _ActionRail({
    required this.coverUrl,
    required this.likes,
    required this.comments,
    required this.liked,
    required this.onLike,
    required this.onComment,
    required this.onShare,
    required this.onAvatar,
  });

  final String? coverUrl;
  final String likes;
  final String comments;
  final bool liked;
  final VoidCallback onLike;
  final VoidCallback onComment;
  final VoidCallback onShare;
  final VoidCallback onAvatar;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _AvatarBadge(url: coverUrl, onTap: onAvatar),
        const SizedBox(height: 18),
        _RailButton(
          icon: liked ? Icons.favorite : Icons.favorite_border,
          color: liked ? gold : Colors.white,
          label: likes,
          onTap: onLike,
        ),
        const SizedBox(height: 18),
        _RailButton(
          icon: Icons.mode_comment_outlined,
          color: Colors.white,
          label: comments,
          onTap: onComment,
        ),
        const SizedBox(height: 18),
        _RailButton(
          icon: Icons.share_outlined,
          color: Colors.white,
          label: null,
          onTap: onShare,
        ),
      ],
    );
  }
}

class _RailButton extends StatelessWidget {
  const _RailButton({
    required this.icon,
    required this.color,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final Color color;
  final String? label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: color, size: 30),
          if (label != null && label!.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              label!,
              style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w600),
            ),
          ],
        ],
      ),
    );
  }
}

/// Drama cover avatar with the red "+" badge (visual language from P6).
class _AvatarBadge extends StatelessWidget {
  const _AvatarBadge({required this.url, required this.onTap});
  final String? url;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final src = imgUrl(url, Img.avatar);
    final hasImage = src.isNotEmpty;
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 46,
        height: 56,
        child: Stack(
          clipBehavior: Clip.none,
          alignment: Alignment.topCenter,
          children: [
            Container(
              width: 46,
              height: 46,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.white, width: 1.5),
                image: hasImage
                    ? DecorationImage(image: NetworkImage(src, headers: imageAcceptHeaders), fit: BoxFit.cover)
                    : null,
                color: sheet,
              ),
              child: hasImage
                  ? null
                  : const Center(
                      child: Text(
                        'M',
                        style: TextStyle(color: red, fontSize: 20, fontWeight: FontWeight.w900),
                      ),
                    ),
            ),
            const Positioned(
              bottom: 0,
              child: CircleAvatar(
                radius: 9,
                backgroundColor: red,
                child: Icon(Icons.add, color: Colors.white, size: 14),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
