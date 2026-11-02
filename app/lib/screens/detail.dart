import 'dart:async';

import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

import '../api.dart';
import '../copy.dart';
import '../img.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';
import 'comments.dart';
import 'episode_player.dart';
import 'premium.dart';
import 'unlock.dart';

const _speedKey = 'minitv_app_speed';
const _qualityKey = 'minitv_app_quality';

/// Debug only: force an initial rendition (`--dart-define=DEBUG_QUALITY=480P`).
const _debugQuality = String.fromEnvironment('DEBUG_QUALITY', defaultValue: '');

/// Where the share sheet points — the H5 viewer is the public watch page.
const shareBaseUrl = 'https://h5.minitv.us';

/// The drama's play page (design draft P8 播放态 + P9 选集面板).
///
/// A vertical scroller over every episode of one drama: opening the drama from
/// the homepage, the For You feed or anywhere else lands here on the resume
/// point (episode one for a fresh viewer), and finishing an episode scrolls to
/// the next one and plays it automatically.
///
/// Locked episodes offer the three exits H5 shows: unlock with coins
/// (idempotent POST /v1/unlock), go Premium, or earn coins on the Rewards tab.
class DetailScreen extends StatefulWidget {
  const DetailScreen({
    super.key,
    required this.dramaId,
    this.language = 'en',
    this.onBack,
    this.onOpenRewards,
  });

  final int dramaId;
  final String language;
  final VoidCallback? onBack;

  /// "Earn coins" from a locked episode jumps to the Rewards tab.
  final VoidCallback? onOpenRewards;

  @override
  State<DetailScreen> createState() => _DetailScreenState();
}

class _DetailScreenState extends State<DetailScreen> {
  Map<String, dynamic>? payload;
  String? error;
  PageController? controller;
  int index = 0;
  bool synopsisExpanded = false;
  bool liked = false;
  bool favorited = false;
  int likeCount = 0;

  /// Playback rate survives swiping between episodes and relaunches.
  double speed = 1.0;

  /// 'auto' or an HLS variant label such as '480P'. Persisted like the speed.
  String quality = _debugQuality.isEmpty ? 'auto' : _debugQuality;

  /// Real rendition ladder reported by the player for the current episode.
  List<String> renditions = const [];

  /// Live comment counts keyed by episode id (updated by CommentSheet).
  final commentCounts = <int, int>{};

  /// One idempotency key per episode: a retry after a timeout repeats the
  /// exact same request, which the server recognises instead of double-charging.
  final unlockKeys = <int, String>{};
  Map<String, dynamic>? unlockTarget;
  bool premiumOpen = false;

  String? sheet = () {
    // Debug-only entry so a build can open a sheet straight away.
    const value = String.fromEnvironment('DEBUG_SHEET');
    return value.isEmpty ? null : value;
  }(); // episodes | settings | comments
  Session? _session;
  bool _member = false;
  bool _started = false;

  bool get zh => widget.language == 'zh';
  Copy get copy => Copy(widget.language);

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((prefs) {
      final savedSpeed = prefs.getDouble(_speedKey);
      final savedQuality = prefs.getString(_qualityKey);
      if (!mounted) return;
      setState(() {
        if (savedSpeed != null) speed = savedSpeed;
        if (_debugQuality.isEmpty && savedQuality != null && savedQuality.isNotEmpty) quality = savedQuality;
      });
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final session = SessionScope.maybeOf(context);
    if (session != _session) {
      _session?.removeListener(_onSessionChange);
      _session = session;
      _session?.addListener(_onSessionChange);
      _member = _session?.membership?['active'] == true;
    }
    // The first load must see the session token (liked/locked depend on it),
    // so it fires here, after the scope is reachable, not in initState.
    if (!_started) {
      _started = true;
      load();
    }
  }

  @override
  void dispose() {
    _session?.removeListener(_onSessionChange);
    controller?.dispose();
    super.dispose();
  }

  /// Buying Premium in the external browser flips `membership.active` when the
  /// app resumes; re-pull the payload so locked episodes open up in place.
  void _onSessionChange() {
    final active = _session?.membership?['active'] == true;
    if (active && !_member) load();
    _member = active;
  }

  Map<String, dynamic> get drama => (payload?['drama'] as Map?)?.cast<String, dynamic>() ?? const {};
  List<Map<String, dynamic>> get episodes => ((payload?['items'] as List?) ?? []).cast<Map<String, dynamic>>();

  Future<void> load() async {
    try {
      final data = await MiniApi(token: _session?.token).getJson('/v1/dramas/${widget.dramaId}/play');
      final items = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      final resumeId = ((data['resume'] as Map?) ?? const {})['episode_id'];
      final startAt = items.indexWhere((item) => item['id'] == resumeId);
      final dramaData = (data['drama'] as Map?)?.cast<String, dynamic>() ?? const {};
      payload = data;
      index = startAt < 0 ? 0 : startAt;
      liked = dramaData['liked'] == true;
      favorited = dramaData['favorited'] == true;
      likeCount = (dramaData['like_count'] as int?) ?? (dramaData['play_count'] as int?) ?? 0;
      controller?.dispose();
      controller = PageController(initialPage: index);
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() {});
  }

  void _goTo(int target) {
    if (target < 0 || target >= episodes.length) return;
    // Jump rather than animate: the next episode starts playing the instant the
    // page settles, and the info strip (which reads `index`) stays in step.
    setState(() => index = target);
    controller?.jumpToPage(target);
  }

  void _setSpeed(double value) {
    setState(() => speed = value);
    SharedPreferences.getInstance().then((prefs) => prefs.setDouble(_speedKey, value));
  }

  void _setQuality(String value) {
    setState(() => quality = value);
    SharedPreferences.getInstance().then((prefs) => prefs.setString(_qualityKey, value));
  }

  /// The player reports the ladder it actually found; keeping the sheet in
  /// step also drops a saved choice the new episode doesn't offer.
  void _onQualities(List<String> labels) {
    if (labels.join('|') == renditions.join('|')) return;
    setState(() {
      renditions = labels;
      if (quality != 'auto' && !labels.contains(quality)) quality = 'auto';
    });
  }

  void _toggleReaction(String kind) {
    final session = _session;
    if (session == null) return;
    session.requireAuth(() async {
      final active = kind == 'like' ? liked : favorited;
      final next = !active;
      void apply(bool value) {
        if (kind == 'like') {
          liked = value;
          likeCount = (likeCount + (value ? 1 : -1)).clamp(0, 1 << 31);
        } else {
          favorited = value;
        }
      }

      setState(() => apply(next));
      try {
        await MiniApi(token: session.token).postJson('/v1/reactions', {
          'target_type': 'drama',
          'target_id': widget.dramaId,
          'kind': kind,
          'remove': !next,
        });
      } on ApiException {
        if (!mounted) return;
        setState(() => apply(active));
        toast(context, copy.t('err_reaction'));
      }
    });
  }

  void _share() {
    final title = drama['title'] as String? ?? 'MiniTV';
    SharePlus.instance.share(ShareParams(text: '$title\n$shareBaseUrl', subject: title));
  }

  void _openUnlock(Map<String, dynamic> episode) {
    final session = _session;
    if (session == null) return;
    session.requireAuth(() {
      final price = ((episode['unlock'] as Map?) ?? const {})['coin_price'] as int? ?? 0;
      // A zero price is membership-only (or a misconfigured drama): never
      // create a coin flow for it, send the viewer to the plans instead.
      if (price <= 0) {
        setState(() => premiumOpen = true);
        return;
      }
      setState(() => unlockTarget = episode);
    });
  }

  String _unlockKeyFor(int episodeId) => unlockKeys.putIfAbsent(episodeId, () => const Uuid().v4());

  void _finishUnlock(Map<String, dynamic> result) {
    final episodeId = result['episode_id'] as int?;
    final isSubscription = result['status'] == 'subscription';
    setState(() {
      unlockTarget = null;
      if (episodeId != null) unlockKeys.remove(episodeId);
      for (final item in episodes) {
        if (isSubscription || item['id'] == episodeId) {
          item['locked'] = false;
          item['unlock'] = null;
        }
      }
      final entitlement = (payload?['entitlement'] as Map?)?.cast<String, dynamic>() ?? const {};
      final known = ((entitlement['unlocked_episode_ids'] as List?) ?? []).cast<int>().toSet();
      if (episodeId != null) known.add(episodeId);
      payload?['entitlement'] = {
        'member': (entitlement['member'] == true) || isSubscription,
        'unlocked_episode_ids': known.toList(),
      };
    });
    unawaited(_session?.refresh());
    toastUnlockResult(context, widget.language, result);
  }

  void _openSettings() => setState(() => sheet = 'settings');
  void _openSelector() => setState(() => sheet = 'episodes');
  void _openComments() => setState(() => sheet = 'comments');

  @override
  Widget build(BuildContext context) {
    if (error != null) return RetryBody(label: copy.t('retry'), onRetry: load);
    if (payload == null) return const Center(child: CircularProgressIndicator(color: red));
    final items = episodes;
    if (items.isEmpty) {
      return Center(child: Text(copy.t('empty'), style: const TextStyle(color: Colors.white70)));
    }
    final current = items[index];
    final synopsis = (drama['synopsis'] as String?) ?? '';
    final tags = ((drama['tags'] as List?) ?? []).map((tag) => '#$tag').join(' ');
    final session = _session;
    final commentCount = commentCounts[current['id']] ?? (current['comment_count'] as int?) ?? 0;

    return ColoredBox(
      color: Colors.black,
      child: Stack(
        fit: StackFit.expand,
        children: [
          PageView.builder(
            controller: controller,
            scrollDirection: Axis.vertical,
            itemCount: items.length,
            onPageChanged: (value) => setState(() => index = value),
            itemBuilder: (context, position) {
              final episode = items[position];
              if (episode['locked'] == true) {
                return LockedEpisodeView(
                  key: ValueKey('locked-${episode['id']}'),
                  episode: episode,
                  session: session,
                  language: widget.language,
                  onUnlock: () => _openUnlock(episode),
                  onMember: () {
                    if (session == null) return;
                    session.requireAuth(() => setState(() => premiumOpen = true));
                  },
                  onNeedCoins: () => widget.onOpenRewards?.call(),
                );
              }
              return _EpisodePage(
                key: ValueKey(episode['id']),
                dramaId: widget.dramaId,
                episode: episode,
                // Only the settled page plays; the incoming page starts once the
                // scroll lands, so two episodes never play at once.
                active: position == index,
                token: session?.token,
                speed: speed,
                quality: quality,
                onQualities: _onQualities,
                onCompleted: () => _goTo(position + 1),
              );
            },
          ),
          // Floating top bar over the full-bleed video: back, episode number,
          // playback settings.
          Positioned(
            top: MediaQuery.of(context).padding.top + 4,
            left: 4,
            right: 12,
            child: Row(
              children: [
                IconButton(
                  onPressed: widget.onBack,
                  icon: const Icon(Icons.arrow_back, color: Colors.white),
                ),
                Text(
                  copy.t('episode_short', {'number': current['episode_number']}),
                  style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
                ),
                const Spacer(),
                GestureDetector(
                  onTap: _openSettings,
                  child: Container(
                    width: 34,
                    height: 34,
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.35),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.tune, color: Colors.white, size: 18),
                  ),
                ),
              ],
            ),
          ),
          // Readability scrim behind the bottom info.
          const Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            height: 250,
            child: IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.bottomCenter,
                    end: Alignment.topCenter,
                    colors: [Color(0xCC000000), Colors.transparent],
                  ),
                ),
              ),
            ),
          ),
          // Right action rail (P8): like, comment, favorite, share.
          Positioned(
            right: 6,
            bottom: 150,
            child: _ActionRail(
              likes: formatCount(likeCount, zh: zh),
              comments: commentCount > 0 ? formatCount(commentCount, zh: zh) : copy.t('comment'),
              favorite: copy.t('favorite'),
              liked: liked,
              favorited: favorited,
              onLike: () => _toggleReaction('like'),
              onComment: _openComments,
              onFavorite: () => _toggleReaction('favorite'),
              onShare: _share,
            ),
          ),
          // Bottom info: title, episode line + synopsis, tags, AI disclaimer.
          Positioned(
            left: 14,
            right: 86,
            bottom: 62,
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
                if (synopsis.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  GestureDetector(
                    onTap: () => setState(() => synopsisExpanded = !synopsisExpanded),
                    child: Text(
                      '${copy.t('episode_short', {'number': current['episode_number']})} | $synopsis ${synopsisExpanded ? copy.t('collapse') : copy.t('expand')}',
                      maxLines: synopsisExpanded ? 6 : 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white70, fontSize: 12, height: 1.3),
                    ),
                  ),
                ],
                if (tags.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    tags,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white54, fontSize: 12),
                  ),
                ],
                const SizedBox(height: 4),
                Text(copy.t('ai_disclaimer'), style: const TextStyle(color: Colors.white38, fontSize: 11)),
              ],
            ),
          ),
          // 选集 bar (P8 bottom bar): opens the episode selector sheet.
          Positioned(
            left: 12,
            right: 12,
            bottom: 12,
            child: GestureDetector(
              onTap: _openSelector,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.list, size: 16, color: Colors.white),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        copy.t('all_episodes_bar', {'count': items.length}),
                        style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                      ),
                    ),
                    const Icon(Icons.keyboard_arrow_up, size: 18, color: Colors.white70),
                  ],
                ),
              ),
            ),
          ),
          if (sheet == 'episodes')
            Positioned.fill(
              child: _EpisodeSelectorSheet(
                drama: drama,
                episodes: items,
                currentIndex: index,
                language: widget.language,
                onClose: () => setState(() => sheet = null),
                onPick: (picked) {
                  setState(() => sheet = null);
                  _goTo(picked);
                },
              ),
            ),
          if (sheet == 'settings')
            Positioned.fill(
              child: _PlaybackSettingsSheet(
                language: widget.language,
                speed: speed,
                quality: quality,
                renditions: renditions,
                onSpeed: _setSpeed,
                onQuality: _setQuality,
                onClose: () => setState(() => sheet = null),
              ),
            ),
          if (sheet == 'comments' && session != null)
            Positioned.fill(
              child: CommentSheet(
                key: ValueKey('comments-${current['id']}'),
                session: session,
                language: widget.language,
                episodeId: current['id'] as int,
                initialCount: commentCount,
                onCountChange: (next) => setState(() => commentCounts[current['id'] as int] = next),
                onClose: () => setState(() => sheet = null),
              ),
            ),
          if (unlockTarget != null && session != null)
            Positioned.fill(
              child: UnlockSheet(
                session: session,
                language: widget.language,
                episode: unlockTarget!,
                price: ((unlockTarget!['unlock'] as Map?) ?? const {})['coin_price'] as int? ?? 0,
                idempotencyKey: _unlockKeyFor(unlockTarget!['id'] as int),
                onClose: () => setState(() => unlockTarget = null),
                onDone: _finishUnlock,
                onNeedCoins: () {
                  setState(() => unlockTarget = null);
                  unawaited(session.refresh());
                  widget.onOpenRewards?.call();
                },
              ),
            ),
          if (premiumOpen && session != null)
            Positioned.fill(
              child: PremiumSheet(
                session: session,
                language: widget.language,
                onClose: () => setState(() => premiumOpen = false),
              ),
            ),
        ],
      ),
    );
  }
}

/// One episode inside the scroller. Locked episodes never reach this widget.
class _EpisodePage extends StatelessWidget {
  const _EpisodePage({
    super.key,
    required this.dramaId,
    required this.episode,
    required this.active,
    required this.token,
    required this.speed,
    required this.quality,
    required this.onQualities,
    required this.onCompleted,
  });

  final int dramaId;
  final Map<String, dynamic> episode;
  final bool active;
  final String? token;
  final double speed;
  final String quality;
  final ValueChanged<List<String>> onQualities;
  final VoidCallback onCompleted;

  @override
  Widget build(BuildContext context) {
    return EpisodePlayer(
      dramaId: dramaId,
      episodeId: episode['id'] as int,
      resumeSeconds: (episode['progress_seconds'] as int?) ?? 0,
      active: active,
      token: token,
      speed: speed,
      quality: quality,
      onQualities: onQualities,
      onFinished: onCompleted,
    );
  }
}

/// The floating action rail on the right edge (P8).
class _ActionRail extends StatelessWidget {
  const _ActionRail({
    required this.likes,
    required this.comments,
    required this.favorite,
    required this.liked,
    required this.favorited,
    required this.onLike,
    required this.onComment,
    required this.onFavorite,
    required this.onShare,
  });

  final String likes;
  final String comments;
  final String favorite;
  final bool liked;
  final bool favorited;
  final VoidCallback onLike;
  final VoidCallback onComment;
  final VoidCallback onFavorite;
  final VoidCallback onShare;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _RailButton(
          icon: liked ? Icons.favorite : Icons.favorite_border,
          color: liked ? gold : Colors.white,
          label: likes,
          onTap: onLike,
        ),
        const SizedBox(height: 18),
        _RailButton(icon: Icons.mode_comment_outlined, color: Colors.white, label: comments, onTap: onComment),
        const SizedBox(height: 18),
        _RailButton(
          icon: favorited ? Icons.star : Icons.star_border,
          color: favorited ? gold : Colors.white,
          label: favorite,
          onTap: onFavorite,
        ),
        const SizedBox(height: 18),
        _RailButton(icon: Icons.share_outlined, color: Colors.white, label: null, onTap: onShare),
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
            Text(label!, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w600)),
          ],
        ],
      ),
    );
  }
}

/// Episode selector bottom sheet (design draft P9): synopsis / episodes tabs,
/// episode ranges and a six-column grid with the current episode in red.
class _EpisodeSelectorSheet extends StatefulWidget {
  const _EpisodeSelectorSheet({
    required this.drama,
    required this.episodes,
    required this.currentIndex,
    required this.language,
    required this.onClose,
    required this.onPick,
  });

  final Map<String, dynamic> drama;
  final List<Map<String, dynamic>> episodes;
  final int currentIndex;
  final String language;
  final VoidCallback onClose;
  final ValueChanged<int> onPick;

  @override
  State<_EpisodeSelectorSheet> createState() => _EpisodeSelectorSheetState();
}

class _EpisodeSelectorSheetState extends State<_EpisodeSelectorSheet> {
  static const rangeSize = 20;
  int tab = 1; // 0 synopsis, 1 episodes
  int rangeIndex = 0;

  @override
  void initState() {
    super.initState();
    rangeIndex = widget.currentIndex ~/ rangeSize;
  }

  int get rangeCount => (widget.episodes.length + rangeSize - 1) ~/ rangeSize;

  @override
  Widget build(BuildContext context) {
    final copy = Copy(widget.language);
    final drama = widget.drama;
    final episodes = widget.episodes;
    final rangeStart = rangeIndex * rangeSize;
    final rangeEnd = (rangeStart + rangeSize).clamp(0, episodes.length);
    // Counted over the whole drama so the line stays put while paging ranges.
    final watched = episodes.where((item) => item['completed'] == true).length;
    final watching = episodes
        .where((item) =>
            item['completed'] != true &&
            item['locked'] != true &&
            ((item['progress_seconds'] as num?) ?? 0) > 0)
        .length;
    final summary = [
      if (watched > 0) copy.t('ep_watched', {'count': watched}),
      if (watching > 0) copy.t('ep_watching', {'count': watching}),
    ].join(' · ');

    return GestureDetector(
      onTap: widget.onClose,
      child: ColoredBox(
        color: Colors.black54,
        child: Align(
          alignment: Alignment.bottomCenter,
          child: GestureDetector(
            onTap: () {},
            child: Container(
              height: MediaQuery.of(context).size.height * 0.62,
              decoration: const BoxDecoration(
                color: sheet,
                borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
              ),
              child: SafeArea(
                top: false,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 8),
                    Center(
                      child: Container(
                        width: 36,
                        height: 4,
                        decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)),
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                      child: Row(
                        children: [
                          ClipRRect(
                            borderRadius: BorderRadius.circular(6),
                            child: SizedBox(
                              width: 42,
                              height: 56,
                              child: Poster(url: drama['cover_url'] as String?, imgWidth: Img.rank),
                            ),
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  drama['title'] as String? ?? '',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700),
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  copy.t('all_episodes_bar', {'count': episodes.length}),
                                  style: const TextStyle(color: Colors.white54, fontSize: 12),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                      child: Row(
                        children: [
                          _SheetTab(label: copy.t('synopsis'), active: tab == 0, onTap: () => setState(() => tab = 0)),
                          const SizedBox(width: 18),
                          _SheetTab(label: copy.t('episodes_tab'), active: tab == 1, onTap: () => setState(() => tab = 1)),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    Expanded(
                      child: tab == 0
                          ? SingleChildScrollView(
                              padding: const EdgeInsets.symmetric(horizontal: 16),
                              child: Text(
                                (drama['synopsis'] as String?) ?? '',
                                style: const TextStyle(color: Colors.white70, fontSize: 13, height: 1.5),
                              ),
                            )
                          : Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                if (rangeCount > 1 || summary.isNotEmpty)
                                  SizedBox(
                                    height: 28,
                                    child: Row(
                                      children: [
                                        const SizedBox(width: 16),
                                        if (rangeCount > 1)
                                          Expanded(
                                            child: ListView.separated(
                                              scrollDirection: Axis.horizontal,
                                              itemCount: rangeCount,
                                              separatorBuilder: (_, _) => const SizedBox(width: 8),
                                              itemBuilder: (_, i) {
                                                final from = i * rangeSize + 1;
                                                final to = ((i + 1) * rangeSize).clamp(1, episodes.length);
                                                return GestureDetector(
                                                  onTap: () => setState(() => rangeIndex = i),
                                                  child: Container(
                                                    padding: const EdgeInsets.symmetric(horizontal: 10),
                                                    alignment: Alignment.center,
                                                    decoration: BoxDecoration(
                                                      color: i == rangeIndex ? red : const Color(0x22FFFFFF),
                                                      borderRadius: BorderRadius.circular(14),
                                                    ),
                                                    child: Text(
                                                      copy.t('episode_range', {'from': from, 'to': to}),
                                                      style: const TextStyle(color: Colors.white, fontSize: 12),
                                                    ),
                                                  ),
                                                );
                                              },
                                            ),
                                          )
                                        else
                                          const Spacer(),
                                        if (summary.isNotEmpty) ...[
                                          const SizedBox(width: 8),
                                          Text(
                                            summary,
                                            style: TextStyle(color: Colors.white.withValues(alpha: .45), fontSize: 11),
                                          ),
                                        ],
                                        const SizedBox(width: 16),
                                      ],
                                    ),
                                  ),
                                const SizedBox(height: 10),
                                Expanded(
                                  child: GridView.builder(
                                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                      crossAxisCount: 6,
                                      mainAxisSpacing: 10,
                                      crossAxisSpacing: 10,
                                      childAspectRatio: 1,
                                    ),
                                    itemCount: rangeEnd - rangeStart,
                                    itemBuilder: (_, i) {
                                      final position = rangeStart + i;
                                      final episode = episodes[position];
                                      final current = position == widget.currentIndex;
                                      final locked = episode['locked'] == true;
                                      final completed = episode['completed'] == true;
                                      final duration = (episode['duration_seconds'] as num?)?.toDouble() ?? 0;
                                      final progress = (episode['progress_seconds'] as num?)?.toDouble() ?? 0;
                                      final ratio = !locked && !completed && duration > 0
                                          ? (progress / duration).clamp(0.0, 1.0)
                                          : 0.0;
                                      return GestureDetector(
                                        onTap: () => widget.onPick(position),
                                        child: Container(
                                          decoration: BoxDecoration(
                                            color: current
                                                ? red
                                                : completed
                                                    ? const Color(0x0AFFFFFF)
                                                    : const Color(0x22FFFFFF),
                                            borderRadius: BorderRadius.circular(8),
                                          ),
                                          child: Stack(
                                            fit: StackFit.expand,
                                            children: [
                                              Center(
                                                child: locked
                                                    ? Icon(Icons.lock_outline,
                                                        size: 14, color: current ? Colors.white : Colors.white54)
                                                    : Text(
                                                        '${episode['episode_number']}',
                                                        style: TextStyle(
                                                          color: completed && !current ? Colors.white38 : Colors.white,
                                                          fontSize: 13,
                                                          fontWeight: current ? FontWeight.w800 : FontWeight.w500,
                                                        ),
                                                      ),
                                              ),
                                              if (completed)
                                                Positioned(
                                                  top: 4,
                                                  right: 4,
                                                  child: Icon(Icons.check_rounded,
                                                      size: 11, color: current ? Colors.white : Colors.white54),
                                                ),
                                              if (ratio > 0)
                                                Positioned(
                                                  left: 6,
                                                  right: 6,
                                                  bottom: 5,
                                                  child: ClipRRect(
                                                    borderRadius: BorderRadius.circular(2),
                                                    child: Container(
                                                      height: 3,
                                                      color: current ? Colors.black26 : Colors.white24,
                                                      child: FractionallySizedBox(
                                                        alignment: Alignment.centerLeft,
                                                        widthFactor: ratio,
                                                        child: ColoredBox(color: current ? Colors.white : red),
                                                      ),
                                                    ),
                                                  ),
                                                ),
                                            ],
                                          ),
                                        ),
                                      );
                                    },
                                  ),
                                ),
                              ],
                            ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Playback settings sheet (design draft P10): speed and quality, each a row
/// of chips.
///
/// Quality options come from the episode's real rendition ladder parsed out of
/// the HLS master playlist; picking one reloads that variant's own playlist so
/// AVPlayer stops adapting (see [EpisodePlayer]).
class _PlaybackSettingsSheet extends StatefulWidget {
  const _PlaybackSettingsSheet({
    required this.language,
    required this.speed,
    required this.quality,
    required this.renditions,
    required this.onSpeed,
    required this.onQuality,
    required this.onClose,
  });

  final String language;
  final double speed;
  final String quality;
  final List<String> renditions;
  final ValueChanged<double> onSpeed;
  final ValueChanged<String> onQuality;
  final VoidCallback onClose;

  @override
  State<_PlaybackSettingsSheet> createState() => _PlaybackSettingsSheetState();
}

class _PlaybackSettingsSheetState extends State<_PlaybackSettingsSheet> {
  static const speeds = [0.75, 1.0, 1.25, 1.5, 2.0];
  late double speed = widget.speed;
  late String quality = widget.quality;

  @override
  Widget build(BuildContext context) {
    final zh = widget.language == 'zh';
    final qualities = ['auto', ...widget.renditions];
    String qualityLabel(String value) => value == 'auto' ? (zh ? '自动' : 'Auto') : value;
    String speedLabel(double value) => value == 1.0 ? '1.0x' : '${value}x';

    return GestureDetector(
      onTap: widget.onClose,
      child: ColoredBox(
        color: Colors.black54,
        child: Align(
          alignment: Alignment.bottomCenter,
          child: GestureDetector(
            onTap: () {},
            child: Container(
              decoration: const BoxDecoration(
                color: sheet,
                borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
              ),
              child: SafeArea(
                top: false,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 20),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Center(
                        child: Container(
                          width: 36,
                          height: 4,
                          decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        zh ? '播放设置' : 'Playback',
                        style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 16),
                      _SettingRow(
                        label: zh ? '倍速' : 'Speed',
                        options: [for (final value in speeds) (value: '$value', label: speedLabel(value))],
                        selected: '$speed',
                        onPick: (value) {
                          final parsed = double.parse(value);
                          setState(() => speed = parsed);
                          widget.onSpeed(parsed);
                        },
                      ),
                      const SizedBox(height: 16),
                      _SettingRow(
                        label: zh ? '清晰度' : 'Quality',
                        options: [for (final value in qualities) (value: value, label: qualityLabel(value))],
                        selected: qualities.contains(quality) ? quality : 'auto',
                        onPick: (value) {
                          setState(() => quality = value);
                          widget.onQuality(value);
                        },
                      ),
                      const SizedBox(height: 6),
                      Text(
                        zh ? '自动会跟随网速切换；选定后固定该清晰度。' : 'Auto adapts to your network; picking one pins that quality.',
                        style: const TextStyle(color: Colors.white38, fontSize: 11),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SettingRow extends StatelessWidget {
  const _SettingRow({
    required this.label,
    required this.options,
    required this.selected,
    required this.onPick,
  });

  final String label;
  final List<({String value, String label})> options;
  final String selected;
  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(color: Colors.white54, fontSize: 12)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final option in options)
              GestureDetector(
                onTap: () => onPick(option.value),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
                  decoration: BoxDecoration(
                    color: option.value == selected ? red : const Color(0x22FFFFFF),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Text(
                    option.label,
                    style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _SheetTab extends StatelessWidget {
  const _SheetTab({required this.label, required this.active, required this.onTap});
  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: TextStyle(
              color: active ? Colors.white : Colors.white54,
              fontSize: 14,
              fontWeight: active ? FontWeight.w700 : FontWeight.w400,
            ),
          ),
          const SizedBox(height: 4),
          Container(
            width: active ? 16 : 0,
            height: 3,
            decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(2)),
          ),
        ],
      ),
    );
  }
}
