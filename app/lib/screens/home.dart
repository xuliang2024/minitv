import 'dart:async';

import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../img.dart';
import '../ui.dart';

/// Home page driven by the 首页运营 configuration:
/// - channel tabs from GET /v1/config (first locked tab = Discover)
/// - banner carousel + floors from GET /v1/home/groups
/// Floor styles: poster3 (three-column poster cards), grid2 (two-column
/// landscape grid), rank (vertical ranking list card). Badge: rank paints
/// 1/2/3 marks, label paints a red marketing chip on cards.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.language, required this.onOpen});
  final String language;
  final void Function(int id) onOpen;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final api = MiniApi();
  List<Map<String, dynamic>> tabs = [];
  List<Map<String, dynamic>> banners = [];
  List<Map<String, dynamic>> groups = [];
  bool loading = true;
  String? error;
  int tabIndex = 0;

  bool get zh => widget.language == 'zh';

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final results = await Future.wait([
        api.getJson('/v1/config?locale=${zh ? 'zh' : 'en'}'),
        api.getJson('/v1/home/groups'),
      ]);
      tabs = ((results[0]['tabs'] as List?) ?? []).cast<Map<String, dynamic>>();
      banners = ((results[1]['banners'] as List?) ?? []).cast<Map<String, dynamic>>();
      groups = ((results[1]['groups'] as List?) ?? []).cast<Map<String, dynamic>>();
      if (tabIndex >= tabs.length) tabIndex = 0;
      error = null;
    } catch (_) {
      error = 'network';
    }
    if (mounted) setState(() => loading = false);
  }
  Map<String, dynamic>? groupById(int? id) {
    if (id == null) return null;
    for (final g in groups) {
      if (g['id'] == id) return g;
    }
    return null;
  }

  String tabTitle(Map<String, dynamic> tab) {
    final titleZh = (tab['title_zh'] as String?) ?? '';
    if (zh && titleZh.isNotEmpty) return titleZh;
    return (tab['title_en'] as String?) ?? '';
  }

  void openGroup(Map<String, dynamic> group) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => GroupListScreen(
        group: group,
        // The list is a route pushed over the shell. Pop it first so the
        // shell's drama play page (driven by onOpen) is actually visible.
        onOpen: (id) {
          Navigator.of(context).pop();
          widget.onOpen(id);
        },
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final copy = Copy(widget.language);
    if (loading) return const _HomeSkeleton();
    if (error != null) return RetryBody(label: copy.t('retry'), onRetry: load);
    if (tabs.isEmpty) {
      return Center(child: Text(copy.t('empty'), style: const TextStyle(color: Colors.white70)));
    }
    final tab = tabs[tabIndex];
    final isDiscover = tab['locked'] == true || tabIndex == 0;
    return Column(
      children: [
        _TabStrip(
          tabs: tabs,
          index: tabIndex,
          title: tabTitle,
          onSelect: (i) => setState(() => tabIndex = i),
        ),
        Expanded(
          child: RefreshIndicator(
            color: red,
            onRefresh: load,
            child: isDiscover
                ? _DiscoverBody(banners: banners, groups: groups, zh: zh, onOpen: widget.onOpen, onMore: openGroup)
                : _GroupTabBody(group: groupById(tab['group_id'] as int?), onOpen: widget.onOpen),
          ),
        ),
      ],
    );
  }
}

class _TabStrip extends StatelessWidget {
  const _TabStrip({required this.tabs, required this.index, required this.title, required this.onSelect});
  final List<Map<String, dynamic>> tabs;
  final int index;
  final String Function(Map<String, dynamic>) title;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 44,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: tabs.length,
        separatorBuilder: (_, _) => const SizedBox(width: 18),
        itemBuilder: (_, i) {
          final selected = i == index;
          return GestureDetector(
            onTap: () => onSelect(i),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  title(tabs[i]),
                  style: TextStyle(
                    color: selected ? Colors.white : Colors.white54,
                    fontSize: selected ? 16 : 14,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
                  ),
                ),
                const SizedBox(height: 4),
                AnimatedContainer(
                  duration: const Duration(milliseconds: 180),
                  width: selected ? 18 : 0,
                  height: 3,
                  decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(2)),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _DiscoverBody extends StatelessWidget {
  const _DiscoverBody({required this.banners, required this.groups, required this.zh, required this.onOpen, required this.onMore});
  final List<Map<String, dynamic>> banners;
  final List<Map<String, dynamic>> groups;
  final bool zh;
  final void Function(int id) onOpen;
  final void Function(Map<String, dynamic> group) onMore;

  @override
  Widget build(BuildContext context) {
    // No banner configured: promote the first drama of the first floor.
    final hero = banners.isNotEmpty ? banners : heroFromGroups(groups, zh ? 'zh' : 'en');
    final children = <Widget>[
      if (hero.isNotEmpty) BannerCarousel(banners: hero, onOpen: onOpen),
    ];
    for (final group in groups) {
      final dramas = ((group['dramas'] as List?) ?? []).cast<Map<String, dynamic>>();
      if (dramas.isEmpty) continue;
      children.add(_Floor(group: group, dramas: dramas, zh: zh, onOpen: onOpen, onMore: () => onMore(group)));
    }
    if (children.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 120),
        Center(child: Text('No content yet', style: TextStyle(color: Colors.white54))),
      ]);
    }
    return ListView(padding: const EdgeInsets.only(bottom: 24), children: children);
  }
}
class BannerCarousel extends StatefulWidget {
  const BannerCarousel({super.key, required this.banners, required this.onOpen});
  final List<Map<String, dynamic>> banners;
  final void Function(int id) onOpen;
  @override
  State<BannerCarousel> createState() => _BannerCarouselState();
}

class _BannerCarouselState extends State<BannerCarousel> {
  final controller = PageController();
  Timer? timer;
  int page = 0;

  @override
  void initState() {
    super.initState();
    if (widget.banners.length > 1) {
      timer = Timer.periodic(const Duration(seconds: 5), (_) {
        if (!controller.hasClients) return;
        final next = (page + 1) % widget.banners.length;
        controller.animateToPage(next, duration: const Duration(milliseconds: 350), curve: Curves.easeOut);
      });
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width - 24;
    final height = width * 5 / 4;
    // Full-bleed hero: ask the edge for exactly the pixels we paint.
    final imgPx = (width * MediaQuery.devicePixelRatioOf(context)).round();
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      child: SizedBox(
        height: height,
        child: Stack(
          children: [
            PageView.builder(
              controller: controller,
              itemCount: widget.banners.length,
              onPageChanged: (i) => setState(() => page = i),
              itemBuilder: (_, i) {
                final b = widget.banners[i];
                return GestureDetector(
                  onTap: () {
                    final id = b['drama_id'] as int?;
                    if (id != null) widget.onOpen(id);
                  },
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(14),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        PosterImage(url: b['cover_url'] as String?, imgWidth: imgPx),
                        const DecoratedBox(
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topCenter,
                              end: Alignment.bottomCenter,
                              colors: [Colors.transparent, Color(0xCC000000)],
                              stops: [0.45, 1],
                            ),
                          ),
                        ),
                        Positioned(
                          left: 14,
                          right: 14,
                          bottom: 14,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(4)),
                                child: const Text('MiniTV 独播', style: TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
                              ),
                              if (((b['kicker'] as String?) ?? '').isNotEmpty) ...[
                                const SizedBox(height: 8),
                                Text(b['kicker'] as String, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: gold, fontSize: 13, fontWeight: FontWeight.w600)),
                              ],
                              const SizedBox(height: 4),
                              Text(
                                ((b['headline'] as String?) ?? '').isNotEmpty ? b['headline'] as String : ((b['title'] as String?) ?? ''),
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800, height: 1.2),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
            if (widget.banners.length > 1)
              Positioned(
                right: 14,
                bottom: 16,
                child: Row(
                  children: List.generate(widget.banners.length, (i) {
                    final active = i == page;
                    return AnimatedContainer(
                      duration: const Duration(milliseconds: 180),
                      margin: const EdgeInsets.only(left: 4),
                      width: active ? 14 : 5,
                      height: 5,
                      decoration: BoxDecoration(
                        color: active ? red : Colors.white38,
                        borderRadius: BorderRadius.circular(3),
                      ),
                    );
                  }),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Floor header: red accent bar + name on the left, "更多 >" on the right.
class _FloorHeader extends StatelessWidget {
  const _FloorHeader({required this.name, required this.zh, required this.onMore, required this.showMore, required this.isHot});
  final String name;
  final bool zh;
  final VoidCallback onMore;
  final bool showMore;
  final bool isHot;

  @override
  Widget build(BuildContext context) {
    final copy = Copy(zh ? 'zh' : 'en');
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 18, 8, 10),
      child: Row(
        children: [
          Container(
            width: 3.5,
            height: 16,
            decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(width: 7),
          // Title + badge take the leading slot; "More" is pinned to the right
          // edge. Do NOT split the leftover space with a Spacer — the loose
          // Flexible would keep half of it, pushing "More" toward the middle
          // whenever the floor name is short (e.g. "Hot").
          Expanded(
            child: Row(
              children: [
                Flexible(
                  child: Text(copy.floorName(name),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w800)),
                ),
                if (isHot) ...[
                  const SizedBox(width: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                    decoration: BoxDecoration(
                      color: red.withValues(alpha: 0.18),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(color: red.withValues(alpha: 0.5)),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.local_fire_department, color: Color(0xFFFF6B3E), size: 11),
                        const SizedBox(width: 2),
                        Text(copy.t('hot'), style: const TextStyle(color: Color(0xFFFF6B3E), fontSize: 9, fontWeight: FontWeight.w800)),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (showMore)
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: onMore,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                child: Row(
                  children: [
                    Text(zh ? '更多' : 'More', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                    const Icon(Icons.chevron_right, color: Colors.white54, size: 16),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _Floor extends StatelessWidget {
  const _Floor({required this.group, required this.dramas, required this.zh, required this.onOpen, required this.onMore});
  final Map<String, dynamic> group;
  final List<Map<String, dynamic>> dramas;
  final bool zh;
  final void Function(int id) onOpen;
  final VoidCallback onMore;

  @override
  Widget build(BuildContext context) {
    final style = (group['style'] as String?) ?? 'poster3';
    final badge = (group['badge'] as String?) ?? 'rank';
    final name = (group['name'] as String?) ?? '';
    // Same caps as H5: rank 3, grid2 4, poster3 6 — "More" only appears when
    // the group actually has more to show.
    final limit = style == 'rank' ? 3 : style == 'grid2' ? 4 : 6;
    final isHot = name.toLowerCase().contains('hot') || name.contains('热');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _FloorHeader(name: name, zh: zh, onMore: onMore, showMore: dramas.length > limit, isHot: isHot),
        if (style == 'rank')
          _RankListCard(dramas: dramas.take(3).toList(), zh: zh, onOpen: onOpen)
        else if (style == 'grid2')
          _LandscapeGrid(dramas: dramas.take(4).toList(), badge: badge, zh: zh, onOpen: onOpen)
        else
          _PosterGrid(dramas: dramas.take(6).toList(), columns: 3, badge: badge, onOpen: onOpen),
      ],
    );
  }
}

/// Loading placeholder shaped like the page: hero block, floor header, cards.
class _HomeSkeleton extends StatelessWidget {
  const _HomeSkeleton();

  @override
  Widget build(BuildContext context) {
    Widget box(double w, double h) => Container(
          width: w,
          height: h,
          decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(8)),
        );
    final screenW = MediaQuery.of(context).size.width;
    return ListView(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
      children: [
        Container(
          height: (screenW - 24) * 5 / 4,
          decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(14)),
        ),
        const SizedBox(height: 20),
        box(120, 16),
        const SizedBox(height: 12),
        Row(
          children: [
            for (var i = 0; i < 3; i++) ...[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    AspectRatio(aspectRatio: 3 / 4, child: Container(
                      decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(8)),
                    )),
                    const SizedBox(height: 6),
                    box(double.infinity, 10),
                  ],
                ),
              ),
              if (i < 2) const SizedBox(width: 8),
            ],
          ],
        ),
      ],
    );
  }
}

/// No banner configured: promote the first drama of the first floor instead.
List<Map<String, dynamic>> heroFromGroups(List<Map<String, dynamic>> groups, String language) {
  final copy = Copy(language);
  for (final group in groups) {
    final dramas = ((group['dramas'] as List?) ?? []);
    if (dramas.isEmpty) continue;
    final first = dramas.first as Map<String, dynamic>;
    if (((first['cover_url'] as String?) ?? '').isEmpty) continue;
    return [
      {
        'id': -(first['id'] as int),
        'drama_id': first['id'],
        'slot': 'hero',
        'title': first['title'],
        'cover_url': first['cover_url'],
        'kicker': copy.floorName((group['name'] as String?) ?? ''),
        'headline': first['title'],
      },
    ];
  }
  return [];
}

/// poster3: three-column portrait poster cards. badge=rank paints corner
/// numbers (1 gold, then lighter) on the posters.
class _PosterGrid extends StatelessWidget {
  const _PosterGrid({required this.dramas, required this.columns, required this.onOpen, this.badge});
  final List<Map<String, dynamic>> dramas;
  final int columns;
  final String? badge;
  final void Function(int id) onOpen;

  @override
  Widget build(BuildContext context) {
    final rows = <Widget>[];
    for (var start = 0; start < dramas.length; start += columns) {
      final slice = dramas.sublist(start, start + columns > dramas.length ? dramas.length : start + columns);
      rows.add(Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (var i = 0; i < columns; i++)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.only(left: i == 0 ? 0 : 8),
                  child: i < slice.length
                      ? PosterCard(drama: slice[i], rank: badge == 'rank' ? start + i + 1 : null, onOpen: onOpen)
                      : const SizedBox.shrink(),
                ),
              ),
          ],
        ),
      ));
      rows.add(const SizedBox(height: 12));
    }
    return Column(children: rows);
  }
}

/// grid2: two-column landscape still cards. badge=label paints a red chip
/// (热播 / HOT) at the card's top-left corner.
class _LandscapeGrid extends StatelessWidget {
  const _LandscapeGrid({required this.dramas, required this.badge, required this.zh, required this.onOpen});
  final List<Map<String, dynamic>> dramas;
  final String badge;
  final bool zh;
  final void Function(int id) onOpen;

  @override
  Widget build(BuildContext context) {
    final rows = <Widget>[];
    for (var start = 0; start < dramas.length; start += 2) {
      final slice = dramas.sublist(start, start + 2 > dramas.length ? dramas.length : start + 2);
      rows.add(Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (var i = 0; i < 2; i++)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.only(left: i == 0 ? 0 : 8),
                  child: i < slice.length
                      ? _LandscapeCard(drama: slice[i], chip: badge == 'label' ? (zh ? '热播' : 'HOT') : null, onOpen: onOpen)
                      : const SizedBox.shrink(),
                ),
              ),
          ],
        ),
      ));
      rows.add(const SizedBox(height: 12));
    }
    return Column(children: rows);
  }
}

class _LandscapeCard extends StatelessWidget {
  const _LandscapeCard({required this.drama, required this.chip, required this.onOpen});
  final Map<String, dynamic> drama;
  final String? chip;
  final void Function(int id) onOpen;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => onOpen(drama['id'] as int),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 16 / 10,
            child: Stack(
              fit: StackFit.expand,
              children: [
                PosterImage(url: drama['cover_url'] as String?, imgWidth: Img.landscape),
                if (chip != null)
                  Positioned(
                    left: 0,
                    top: 0,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: const BoxDecoration(
                        color: red,
                        borderRadius: BorderRadius.only(topLeft: Radius.circular(8), bottomRight: Radius.circular(8)),
                      ),
                      child: Text(chip!, style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          SizedBox(
            height: 17,
            child: Text(
              (drama['title'] as String?) ?? '',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: Colors.white, fontSize: 12, height: 1.3),
            ),
          ),
        ],
      ),
    );
  }
}

/// rank: vertical ranking list inside a dark card — big rank number, small
/// poster thumb, title + "NO.x" caption. Shows the top 3; #1 wears a crown.
class _RankListCard extends StatelessWidget {
  const _RankListCard({required this.dramas, required this.zh, required this.onOpen});
  final List<Map<String, dynamic>> dramas;
  final bool zh;
  final void Function(int id) onOpen;

  static const _rankColors = [Color(0xFFFFB23E), Color(0xFFB9C0CC), Color(0xFF8A8F9C)];

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF1B1216),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFF3D2228)),
      ),
      child: Column(
        children: [
          for (var i = 0; i < dramas.length; i++)
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => onOpen(dramas[i]['id'] as int),
              child: Padding(
                padding: EdgeInsets.fromLTRB(12, i == 0 ? 12 : 8, 12, i == dramas.length - 1 ? 12 : 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 26,
                      child: Column(
                        children: [
                          if (i == 0) const Icon(Icons.workspace_premium, color: Color(0xFFFFB23E), size: 13),
                          Text(
                            '${i + 1}',
                            style: TextStyle(
                              color: _rankColors[i.clamp(0, 2)],
                              fontSize: 24,
                              fontWeight: FontWeight.w900,
                              fontStyle: FontStyle.italic,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    PosterImage(url: dramas[i]['cover_url'] as String?, width: 48, height: 64, imgWidth: Img.rank),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            (dramas[i]['title'] as String?) ?? '',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 13.5, fontWeight: FontWeight.w600),
                          ),
                          const SizedBox(height: 5),
                          Row(
                            children: [
                              Text('NO.${i + 1}', style: const TextStyle(color: Colors.white38, fontSize: 11, letterSpacing: 0.5)),
                              const SizedBox(width: 8),
                              const Icon(Icons.local_fire_department, color: Color(0xFFFF6B3E), size: 11),
                              const SizedBox(width: 2),
                              Text(
                                formatCount((dramas[i]['play_count'] as int?) ?? 0, zh: zh),
                                style: const TextStyle(color: Colors.white38, fontSize: 11),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _GroupTabBody extends StatelessWidget {
  const _GroupTabBody({required this.group, required this.onOpen});
  final Map<String, dynamic>? group;
  final void Function(int id) onOpen;

  @override
  Widget build(BuildContext context) {
    final dramas = ((group?['dramas'] as List?) ?? []).cast<Map<String, dynamic>>();
    if (group == null || dramas.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 120),
        Center(child: Text('No content yet', style: TextStyle(color: Colors.white54))),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 24),
      children: [_PosterGrid(dramas: dramas, columns: 3, onOpen: onOpen)],
    );
  }
}

/// "更多" page: every drama of one group in a three-column grid.
class GroupListScreen extends StatelessWidget {
  const GroupListScreen({super.key, required this.group, required this.onOpen});
  final Map<String, dynamic> group;
  final void Function(int id) onOpen;

  @override
  Widget build(BuildContext context) {
    final dramas = ((group['dramas'] as List?) ?? []).cast<Map<String, dynamic>>();
    return Scaffold(
      backgroundColor: bg,
      appBar: AppBar(
        backgroundColor: bg,
        foregroundColor: Colors.white,
        title: Text((group['name'] as String?) ?? ''),
      ),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 24),
        children: [
          if (dramas.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 120),
              child: Center(child: Text('No content yet', style: TextStyle(color: Colors.white54))),
            )
          else
            _PosterGrid(dramas: dramas, columns: 3, onOpen: onOpen),
        ],
      ),
    );
  }
}

/// Poster + two-line title card. Optional corner rank number (poster3 floors
/// with badge=rank). Title area is fixed height so rows never overflow.
class PosterCard extends StatelessWidget {
  const PosterCard({super.key, required this.drama, required this.onOpen, this.rank});
  final Map<String, dynamic> drama;
  final void Function(int id) onOpen;
  final int? rank;

  static const _rankColors = [Color(0xFFFFB23E), Color(0xFFB9C0CC), Color(0xFF8A8F9C)];

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => onOpen(drama['id'] as int),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 3 / 4,
            child: Stack(
              fit: StackFit.expand,
              children: [
                PosterImage(url: drama['cover_url'] as String?, imgWidth: Img.card),
                if (rank != null)
                  Positioned(
                    left: 5,
                    top: 2,
                    child: Row(
                      children: [
                        if (rank == 1)
                          const Padding(
                            padding: EdgeInsets.only(right: 2),
                            child: Icon(Icons.workspace_premium, color: Color(0xFFFFB23E), size: 14,
                                shadows: [Shadow(color: Colors.black87, blurRadius: 5)]),
                          ),
                        Text(
                          '$rank',
                          style: TextStyle(
                            color: rank! <= 3 ? _rankColors[rank! - 1] : Colors.white70,
                            fontSize: 18,
                            fontWeight: FontWeight.w900,
                            fontStyle: FontStyle.italic,
                            shadows: const [Shadow(color: Colors.black87, blurRadius: 5)],
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          SizedBox(
            height: 30,
            child: Text(
              (drama['title'] as String?) ?? '',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: Colors.white, fontSize: 11.5, height: 1.25),
            ),
          ),
        ],
      ),
    );
  }
}
