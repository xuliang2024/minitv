import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../copy.dart';
import '../img.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';
import 'premium.dart';

const _checkinKey = 'minitv_app_checkin';

String _localDay() {
  final now = DateTime.now();
  return '${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}';
}

/// Me tab: profile, coins, membership, orders, messages and the
/// history / favorites / likes grids — the App twin of H5 `MePage.tsx`.
class MeScreen extends StatefulWidget {
  const MeScreen({
    super.key,
    required this.language,
    required this.onLanguage,
    required this.onOpen,
  });

  final String language;
  final ValueChanged<String> onLanguage;
  final void Function(int dramaId) onOpen;

  @override
  State<MeScreen> createState() => _MeScreenState();
}

class _MeScreenState extends State<MeScreen> {
  String tab = 'history'; // history | favorites | likes
  String? sheet; // edit | ledger | messages | settings | premium | orders

  List<Map<String, dynamic>>? history;
  Map<String, dynamic>? favorites; // {items, total}
  List<Map<String, dynamic>>? likes;
  final states = <String, String>{'history': 'idle', 'favorites': 'idle', 'likes': 'idle'};
  int unread = 0;
  bool checkedToday = false;

  Copy get copy => Copy(widget.language);

  @override
  void initState() {
    super.initState();
    _restoreCheckin();
  }

  Future<void> _restoreCheckin() async {
    final prefs = await SharedPreferences.getInstance();
    if (mounted) setState(() => checkedToday = prefs.getString(_checkinKey) == _localDay());
  }

  Future<void> _markChecked() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_checkinKey, _localDay());
    if (mounted) setState(() => checkedToday = true);
  }

  void _loadFor(Session session) {
    if (!session.isSignedIn) {
      history = null;
      favorites = null;
      likes = null;
      unread = 0;
      states.updateAll((_, _) => 'idle');
      return;
    }
    if (states['history'] == 'idle') _loadHistory(session);
    if (states['favorites'] == 'idle') _loadFavorites(session);
    if (tab == 'likes' && states['likes'] == 'idle') _loadLikes(session);
  }

  Future<void> _loadHistory(Session session) async {
    states['history'] = 'loading';
    try {
      final data = await session.api.getJson('/v1/me/history');
      history = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      states['history'] = 'ready';
    } catch (_) {
      states['history'] = 'error';
    }
    if (mounted) setState(() {});
  }

  Future<void> _loadFavorites(Session session) async {
    states['favorites'] = 'loading';
    try {
      favorites = await session.api.getJson('/v1/me/favorites?limit=50&offset=0');
      states['favorites'] = 'ready';
    } catch (_) {
      states['favorites'] = 'error';
    }
    if (mounted) setState(() {});
  }

  Future<void> _loadLikes(Session session) async {
    states['likes'] = 'loading';
    try {
      final data = await session.api.getJson('/v1/me/lists/like');
      likes = ((data['items'] as List?) ?? [])
          .cast<Map<String, dynamic>>()
          .map((item) => item['drama'])
          .whereType<Map<String, dynamic>>()
          .toList();
      states['likes'] = 'ready';
    } catch (_) {
      states['likes'] = 'error';
    }
    if (mounted) setState(() {});
  }

  Future<void> _loadUnread(Session session) async {
    try {
      final data = await session.api.getJson('/v1/me/messages');
      if (mounted) setState(() => unread = (data['unread'] as int?) ?? 0);
    } catch (_) {/* the badge is optional */}
  }

  Future<void> _checkin(Session session) async {
    if (checkedToday) {
      toast(context, copy.t('me_checkin_already'));
      return;
    }
    try {
      final result = await session.api.postJson('/v1/me/checkin', {});
      await _markChecked();
      if (!mounted) return;
      toast(
        context,
        result['replayed'] == true
            ? copy.t('me_checkin_already')
            : copy.t('me_checkin_ok', {'amount': result['amount']}),
      );
      unawaited(session.refresh());
    } on ApiException {
      toast(context, copy.t('me_checkin_fail'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = SessionScope.maybeOf(context);
    if (session == null || session.booting) return const Center(child: CircularProgressIndicator(color: red));
    final guest = !session.isSignedIn;
    if (!guest) {
      _loadFor(session);
      if (unread == 0) _loadUnread(session);
    }

    final user = session.user ?? const <String, dynamic>{};
    final name = ((user['display_name'] as String?) ?? '').trim().isNotEmpty
        ? (user['display_name'] as String).trim()
        : (user['email'] as String?) ?? '';
    final favoritesTotal = (favorites?['total'] as int?) ?? 0;

    // De-duplicated history: one tile per drama, newest watch first.
    final seen = <int>{};
    final historyDramas = <Map<String, dynamic>>[
      for (final item in history ?? const <Map<String, dynamic>>[])
        if (item['drama'] is Map<String, dynamic> && seen.add(item['drama']['id'] as int)) item,
    ];

    return Stack(
      fit: StackFit.expand,
      children: [
        ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            _hero(session, guest, name),
            const SizedBox(height: 12),
            if (guest)
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
                onPressed: session.openLogin,
                child: Text(copy.t('sign_in')),
              )
            else ...[
              _stats(session),
              const SizedBox(height: 12),
            ],
            _premiumStrip(session, guest),
            const SizedBox(height: 14),
            _quickGrid(session, favoritesTotal, historyDramas.length),
            const SizedBox(height: 10),
            _ordersRow(session),
            const SizedBox(height: 14),
            _tabBar(guest),
            const SizedBox(height: 12),
            if (guest) _guestEmpty(session) else _tabBody(session, historyDramas),
          ],
        ),
        if (sheet != null) Positioned.fill(child: _sheetFor(session)),
      ],
    );
  }

  Widget _hero(Session session, bool guest, String name) {
    final user = session.user ?? const <String, dynamic>{};
    return Row(
      children: [
        UserAvatar(
          url: guest ? null : user['avatar_url'] as String?,
          name: guest ? 'M' : name,
          size: 56,
          radius: 14,
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                guest ? copy.t('guest_title') : name,
                style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 4),
              if (guest)
                Text(copy.t('guest_body'), style: const TextStyle(color: Colors.white54, fontSize: 12))
              else
                GestureDetector(
                  onTap: () async {
                    await Clipboard.setData(ClipboardData(text: '${user['public_id'] ?? ''}'));
                    if (mounted) toast(context, copy.t('me_copied'));
                  },
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        '${copy.t('me_id')} · ${user['public_id'] ?? ''}',
                        style: const TextStyle(color: Colors.white54, fontSize: 12),
                      ),
                      const SizedBox(width: 4),
                      const Icon(Icons.copy, color: Colors.white38, size: 12),
                    ],
                  ),
                ),
            ],
          ),
        ),
        if (unread > 0 || !guest)
          GestureDetector(
            onTap: () => session.requireAuth(() => setState(() => sheet = 'messages')),
            child: Stack(
              clipBehavior: Clip.none,
              children: [
                const Padding(
                  padding: EdgeInsets.all(6),
                  child: Icon(Icons.notifications_none, color: Colors.white70, size: 22),
                ),
                if (unread > 0)
                  Positioned(
                    right: 0,
                    top: 0,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                      decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(8)),
                      child: Text(
                        unread > 9 ? '9+' : '$unread',
                        style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.w700),
                      ),
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _stats(Session session) {
    final user = session.user ?? const <String, dynamic>{};
    return Row(
      children: [
        _stat('${user['following_count'] ?? 0}', copy.t('me_following')),
        _stat('${user['follower_count'] ?? 0}', copy.t('me_followers')),
        _stat('${user['received_likes'] ?? 0}', copy.t('me_likes_received')),
        TextButton(
          onPressed: () => setState(() => sheet = 'edit'),
          child: Text(copy.t('me_edit_profile'), style: const TextStyle(color: gold, fontSize: 12)),
        ),
      ],
    );
  }

  Widget _stat(String value, String label) {
    return Padding(
      padding: const EdgeInsets.only(right: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w800)),
          Text(label, style: const TextStyle(color: Colors.white54, fontSize: 11)),
        ],
      ),
    );
  }

  Widget _premiumStrip(Session session, bool guest) {
    final membership = session.membership;
    final active = membership?['active'] == true;
    final canceling = membership?['canceling'] == true;
    return GestureDetector(
      onTap: () => session.requireAuth(() => setState(() => sheet = 'premium')),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: active
                ? [const Color(0xFF3A2A10), const Color(0xFF241A1D)]
                : [const Color(0xFF2A1E21), const Color(0xFF1C1315)],
          ),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: gold.withValues(alpha: active ? 0.5 : 0.25)),
        ),
        child: Row(
          children: [
            const Text('★', style: TextStyle(color: gold, fontSize: 20)),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    copy.t(active ? 'premium_active' : 'premium_strip_title'),
                    style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700),
                  ),
                  Text(
                    active
                        ? copy.t(canceling ? 'premium_ends_on' : 'premium_active_until',
                            {'date': formatDay(membership?['until'] as String?, widget.language)})
                        : copy.t('premium_strip_sub'),
                    style: const TextStyle(color: Colors.white54, fontSize: 11),
                  ),
                ],
              ),
            ),
            if (!active) Text(copy.t('premium_strip_cta'), style: const TextStyle(color: gold, fontSize: 12, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }

  Widget _quickGrid(Session session, int favoritesTotal, int historyCount) {
    final tiles = [
      (
        icon: Icons.history,
        title: copy.t('me_history'),
        sub: historyCount == 1
            ? copy.t('me_history_sub_one')
            : historyCount > 1
                ? copy.t('me_history_sub_count', {'count': historyCount})
                : copy.t('me_history_sub'),
        onTap: () => _openTab(session, 'history'),
      ),
      (
        icon: Icons.bookmark_border,
        title: copy.t('me_list'),
        sub: copy.t('me_list_sub', {'count': favoritesTotal}),
        onTap: () => _openTab(session, 'favorites'),
      ),
      (
        icon: Icons.monetization_on_outlined,
        title: copy.t('me_coins_title'),
        sub: '${session.coins} ${copy.t('coins')}',
        onTap: () => session.requireAuth(() => setState(() => sheet = 'ledger')),
      ),
      (
        icon: checkedToday ? Icons.event_available : Icons.event_available_outlined,
        title: copy.t('me_checkin'),
        sub: copy.t(checkedToday ? 'me_checkin_done' : 'me_checkin_go'),
        onTap: () => session.requireAuth(() => _checkin(session)),
      ),
    ];
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      childAspectRatio: 2.5,
      children: [
        for (final tile in tiles)
          GestureDetector(
            onTap: tile.onTap,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(12)),
              child: Row(
                children: [
                  Icon(tile.icon, color: tile.title == copy.t('me_coins_title') ? gold : Colors.white70, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(tile.title, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
                        Text(tile.sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white54, fontSize: 10.5)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _ordersRow(Session session) {
    return GestureDetector(
      onTap: () => session.requireAuth(() => setState(() => sheet = 'orders')),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(12)),
        child: Row(
          children: [
            const Icon(Icons.receipt_long, color: Colors.white70, size: 18),
            const SizedBox(width: 10),
            Expanded(child: Text(copy.t('me_orders'), style: const TextStyle(color: Colors.white, fontSize: 13))),
            Text(copy.t('me_orders_hint'), style: const TextStyle(color: Colors.white38, fontSize: 12)),
            const Icon(Icons.chevron_right, color: Colors.white38, size: 18),
          ],
        ),
      ),
    );
  }

  Widget _tabBar(bool guest) {
    return Row(
      children: [
        for (final (key, labelKey) in [('history', 'me_tab_history'), ('favorites', 'me_tab_favorites'), ('likes', 'me_tab_likes')])
          GestureDetector(
            onTap: () => _openTab(SessionScope.of(context), key),
            child: Padding(
              padding: const EdgeInsets.only(right: 20),
              child: Column(
                children: [
                  Text(
                    copy.t(labelKey),
                    style: TextStyle(
                      color: tab == key ? Colors.white : Colors.white54,
                      fontSize: 14,
                      fontWeight: tab == key ? FontWeight.w700 : FontWeight.w400,
                    ),
                  ),
                  const SizedBox(height: 4),
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 150),
                    width: tab == key ? 16 : 0,
                    height: 3,
                    decoration: BoxDecoration(color: red, borderRadius: BorderRadius.circular(2)),
                  ),
                ],
              ),
            ),
          ),
        const Spacer(),
        IconButton(
          onPressed: () => setState(() => sheet = 'settings'),
          icon: const Icon(Icons.settings_outlined, color: Colors.white70, size: 20),
          visualDensity: VisualDensity.compact,
        ),
      ],
    );
  }

  void _openTab(Session session, String target) {
    session.requireAuth(() {
      setState(() => tab = target);
      if (target == 'likes' && states['likes'] == 'idle') _loadLikes(session);
    });
  }

  Widget _guestEmpty(Session session) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 36),
      child: Column(
        children: [
          const Icon(Icons.star_border, color: Colors.white38, size: 26),
          const SizedBox(height: 10),
          Text(copy.t('sign_in_prompt'), style: const TextStyle(color: Colors.white70, fontSize: 14)),
          const SizedBox(height: 14),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: red),
            onPressed: session.openLogin,
            child: Text(copy.t('sign_in')),
          ),
        ],
      ),
    );
  }

  Widget _tabBody(Session session, List<Map<String, dynamic>> historyDramas) {
    if (tab == 'history') {
      return _grid(
        states['history']!,
        historyDramas.map((item) => (
              drama: item['drama'] as Map<String, dynamic>,
              badge: copy.t('episode_short', {'number': item['episode_number']}),
              meta: ((item['drama']['episode_count'] as int?) ?? 0) > 0
                  ? copy.t('me_ep_progress', {'ep': item['episode_number'], 'total': item['drama']['episode_count']})
                  : null,
            )),
        onRetry: () => _loadHistory(session),
        emptyTitle: 'me_history_empty',
        emptyHint: 'me_history_hint',
      );
    }
    if (tab == 'favorites') {
      final items = ((favorites?['items'] as List?) ?? []).cast<Map<String, dynamic>>();
      return _grid(
        states['favorites']!,
        items.map((drama) => (
              drama: drama,
              badge: null,
              meta: ((drama['episode_count'] as int?) ?? 0) > 0 ? copy.t('me_ep_total', {'count': drama['episode_count']}) : null,
            )),
        onRetry: () => _loadFavorites(session),
        emptyTitle: 'favorites_empty',
        emptyHint: 'favorites_hint',
      );
    }
    return _grid(
      states['likes']!,
      (likes ?? const []).map((drama) => (
            drama: drama,
            badge: null,
            meta: ((drama['episode_count'] as int?) ?? 0) > 0 ? copy.t('me_ep_total', {'count': drama['episode_count']}) : null,
          )),
      onRetry: () => _loadLikes(session),
      emptyTitle: 'me_likes_empty',
      emptyHint: 'me_likes_hint',
    );
  }

  Widget _grid(
    String state,
    Iterable<({Map<String, dynamic> drama, String? badge, String? meta})> tiles, {
    required VoidCallback onRetry,
    required String emptyTitle,
    required String emptyHint,
  }) {
    final list = tiles.toList();
    if (state == 'loading' || state == 'idle') {
      return GridView.count(
        crossAxisCount: 3,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 0.52,
        children: [
          for (var i = 0; i < 6; i++)
            Container(decoration: BoxDecoration(color: const Color(0xFF1C1315), borderRadius: BorderRadius.circular(8))),
        ],
      );
    }
    if (state == 'error') {
      return RetryBody(label: copy.t('retry'), onRetry: onRetry);
    }
    if (list.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 28),
        child: Column(
          children: [
            Text(copy.t(emptyTitle), style: const TextStyle(color: Colors.white70, fontSize: 14)),
            const SizedBox(height: 4),
            Text(copy.t(emptyHint), style: const TextStyle(color: Colors.white38, fontSize: 12)),
          ],
        ),
      );
    }
    return GridView.count(
      crossAxisCount: 3,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      childAspectRatio: 0.52,
      children: [
        for (final tile in list)
          GestureDetector(
            onTap: () => widget.onOpen(tile.drama['id'] as int),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      PosterImage(url: tile.drama['cover_url'] as String?, imgWidth: Img.card),
                      if (tile.badge != null)
                        Positioned(
                          left: 4,
                          bottom: 4,
                          child: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                            decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(4)),
                            child: Text(tile.badge!, style: const TextStyle(color: gold, fontSize: 9, fontWeight: FontWeight.w700)),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  tile.drama['title'] as String? ?? '',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 11.5, fontWeight: FontWeight.w600),
                ),
                if (tile.meta != null)
                  Text(tile.meta!, maxLines: 1, style: const TextStyle(color: Colors.white38, fontSize: 10)),
              ],
            ),
          ),
      ],
    );
  }

  Widget _sheetFor(Session session) {
    switch (sheet) {
      case 'premium':
        return PremiumSheet(session: session, language: widget.language, onClose: () => setState(() => sheet = null));
      case 'orders':
        return OrdersSheet(session: session, language: widget.language, onClose: () => setState(() => sheet = null));
      case 'edit':
        return _EditProfileSheet(
          session: session,
          language: widget.language,
          onClose: () => setState(() => sheet = null),
        );
      case 'ledger':
        return _LedgerSheet(session: session, language: widget.language, onClose: () => setState(() => sheet = null));
      case 'messages':
        return _MessagesSheet(
          session: session,
          language: widget.language,
          onClose: () => setState(() => sheet = null),
          onRead: () => setState(() => unread = 0),
        );
      case 'settings':
        return _SettingsSheet(
          session: session,
          language: widget.language,
          onLanguage: widget.onLanguage,
          onClose: () => setState(() => sheet = null),
        );
      default:
        return const SizedBox.shrink();
    }
  }
}

/* ---------------- sheets ---------------- */

class _EditProfileSheet extends StatefulWidget {
  const _EditProfileSheet({required this.session, required this.language, required this.onClose});
  final Session session;
  final String language;
  final VoidCallback onClose;

  @override
  State<_EditProfileSheet> createState() => _EditProfileSheetState();
}

class _EditProfileSheetState extends State<_EditProfileSheet> {
  late final name = TextEditingController(text: widget.session.user?['display_name'] as String? ?? '');
  late final bio = TextEditingController(text: widget.session.user?['bio'] as String? ?? '');
  bool busy = false;
  String? error;

  Copy get copy => Copy(widget.language);

  Future<void> save() async {
    final value = name.text.trim();
    if (value.isEmpty) {
      setState(() => error = copy.t('me_name_empty'));
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.session.api.patchJson('/v1/me', {'display_name': value, 'bio': bio.text.trim()});
      await widget.session.refresh();
      if (!mounted) return;
      toast(context, copy.t('me_saved'));
      widget.onClose();
    } on ApiException {
      setState(() {
        busy = false;
        error = copy.t('err_generic');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final email = widget.session.user?['email'] as String?;
    return AppSheet(
      title: copy.t('me_edit_profile'),
      onClose: widget.onClose,
      heightFactor: 0.55,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _label(copy.t('me_name')),
            _input(name, maxLength: 24),
            const SizedBox(height: 10),
            _label(copy.t('me_bio')),
            _input(bio, maxLength: 80, hint: copy.t('me_bio_placeholder')),
            if (email != null) ...[
              const SizedBox(height: 10),
              _label(copy.t('email_label')),
              Text(email, style: const TextStyle(color: Colors.white54, fontSize: 13)),
            ],
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(error!, style: const TextStyle(color: Color(0xFFFF6B6E), fontSize: 12)),
              ),
            const SizedBox(height: 14),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
              onPressed: busy ? null : save,
              child: Text(busy ? copy.t('me_saving') : copy.t('me_save')),
            ),
          ],
        ),
      ),
    );
  }

  Widget _label(String text) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Text(text, style: const TextStyle(color: Colors.white70, fontSize: 13)),
      );

  Widget _input(TextEditingController controller, {int? maxLength, String? hint}) => TextField(
        controller: controller,
        maxLength: maxLength,
        style: const TextStyle(color: Colors.white),
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: const TextStyle(color: Colors.white38),
          counterText: '',
          filled: true,
          fillColor: const Color(0xFF241A1D),
          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
        ),
      );
}

class _LedgerSheet extends StatelessWidget {
  const _LedgerSheet({required this.session, required this.language, required this.onClose});
  final Session session;
  final String language;
  final VoidCallback onClose;

  static const _labels = {
    'checkin': 'me_ledger_checkin',
    'ad_reward': 'me_ledger_ad',
    'ad': 'me_ledger_ad',
    'unlock': 'me_ledger_unlock',
    'purchase': 'me_ledger_purchase',
    'topup': 'me_ledger_purchase',
    'signup': 'me_ledger_signup',
    'register_reward': 'me_ledger_signup',
    'bind_email': 'me_ledger_bind',
    'bind_facebook': 'me_ledger_bind',
    'adjustment': 'me_ledger_adjust',
    'refund': 'me_ledger_refund',
  };

  @override
  Widget build(BuildContext context) {
    final copy = Copy(language);
    return AppSheet(
      title: copy.t('me_ledger'),
      onClose: onClose,
      heightFactor: 0.66,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: Row(
              children: [
                Text(copy.t('me_balance'), style: const TextStyle(color: Colors.white54, fontSize: 12)),
                const SizedBox(width: 8),
                const Icon(Icons.monetization_on, color: gold, size: 16),
                Text('${session.coins}', style: const TextStyle(color: gold, fontSize: 16, fontWeight: FontWeight.w800)),
              ],
            ),
          ),
          Expanded(
            child: FutureBuilder<Map<String, dynamic>>(
              future: session.api.getJson('/v1/me/ledger'),
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return Center(child: Text(copy.t('err_generic'), style: const TextStyle(color: Colors.white54)));
                }
                if (!snapshot.hasData) return const Center(child: CircularProgressIndicator(color: red));
                final items = ((snapshot.data!['items'] as List?) ?? []).cast<Map<String, dynamic>>();
                if (items.isEmpty) {
                  return Center(child: Text(copy.t('me_ledger_empty'), style: const TextStyle(color: Colors.white54)));
                }
                return ListView.builder(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  itemCount: items.length,
                  itemBuilder: (_, i) {
                    final entry = items[i];
                    final amount = (entry['amount'] as int?) ?? 0;
                    final bizType = entry['biz_type'] as String? ?? '';
                    return Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Row(
                        children: [
                          Icon(Icons.monetization_on, color: amount > 0 ? gold : Colors.white38, size: 17),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  copy.t(_labels[bizType] ?? bizType),
                                  style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                                ),
                                Text(
                                  formatWhen(entry['created_at'] as String? ?? '', language),
                                  style: const TextStyle(color: Colors.white38, fontSize: 11),
                                ),
                              ],
                            ),
                          ),
                          Text(
                            amount > 0 ? '+$amount' : '$amount',
                            style: TextStyle(color: amount > 0 ? gold : Colors.white54, fontSize: 14, fontWeight: FontWeight.w700),
                          ),
                        ],
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _MessagesSheet extends StatefulWidget {
  const _MessagesSheet({required this.session, required this.language, required this.onClose, required this.onRead});
  final Session session;
  final String language;
  final VoidCallback onClose;
  final VoidCallback onRead;

  @override
  State<_MessagesSheet> createState() => _MessagesSheetState();
}

class _MessagesSheetState extends State<_MessagesSheet> {
  List<Map<String, dynamic>>? items;
  bool error = false;

  @override
  void initState() {
    super.initState();
    widget.session.api.getJson('/v1/me/messages').then((data) async {
      if (!mounted) return;
      setState(() => items = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>());
      if (((data['unread'] as int?) ?? 0) > 0) {
        try {
          await widget.session.api.postJson('/v1/me/messages/read', {});
          widget.onRead();
        } catch (_) {/* leaving the badge is better than breaking the sheet */}
      }
    }).catchError((_) {
      if (mounted) setState(() => error = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    final copy = Copy(widget.language);
    final list = items;
    return AppSheet(
      title: copy.t('me_messages'),
      onClose: widget.onClose,
      heightFactor: 0.6,
      child: error
          ? Center(child: Text(copy.t('err_generic'), style: const TextStyle(color: Colors.white54)))
          : list == null
              ? const Center(child: CircularProgressIndicator(color: red))
              : list.isEmpty
                  ? Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(copy.t('me_messages_empty'), style: const TextStyle(color: Colors.white70, fontSize: 14)),
                          const SizedBox(height: 4),
                          Text(copy.t('me_messages_hint'), style: const TextStyle(color: Colors.white38, fontSize: 12)),
                        ],
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                      itemCount: list.length,
                      itemBuilder: (_, i) {
                        final item = list[i];
                        final read = item['read'] == true;
                        return Padding(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Padding(
                                padding: const EdgeInsets.only(top: 6),
                                child: Container(
                                  width: 6,
                                  height: 6,
                                  decoration: BoxDecoration(
                                    color: read ? Colors.transparent : red,
                                    shape: BoxShape.circle,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(item['title'] as String? ?? '',
                                        style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
                                    const SizedBox(height: 2),
                                    Text(item['body'] as String? ?? '',
                                        style: const TextStyle(color: Colors.white54, fontSize: 12)),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
    );
  }
}

class _SettingsSheet extends StatelessWidget {
  const _SettingsSheet({
    required this.session,
    required this.language,
    required this.onLanguage,
    required this.onClose,
  });
  final Session session;
  final String language;
  final ValueChanged<String> onLanguage;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final copy = Copy(language);
    return AppSheet(
      title: copy.t('me_settings'),
      onClose: onClose,
      heightFactor: 0.42,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Text(copy.t('me_language'), style: const TextStyle(color: Colors.white70, fontSize: 13)),
                const SizedBox(width: 12),
                for (final (value, label) in [('en', 'English'), ('zh', '中文')])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: GestureDetector(
                      onTap: () => onLanguage(value),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                        decoration: BoxDecoration(
                          color: language == value ? red : const Color(0x22FFFFFF),
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 12)),
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            if (session.isSignedIn)
              OutlinedButton(
                onPressed: () async {
                  await session.logout();
                  onClose();
                },
                style: OutlinedButton.styleFrom(side: const BorderSide(color: Colors.white24)),
                child: Text(copy.t('sign_out'), style: const TextStyle(color: Colors.white70)),
              ),
            const SizedBox(height: 12),
            Text('MiniTV · App', style: const TextStyle(color: Colors.white24, fontSize: 11)),
          ],
        ),
      ),
    );
  }
}
