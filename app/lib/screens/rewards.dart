import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../copy.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';

const _checkinKey = 'minitv_app_checkin';

String _localDay() {
  final now = DateTime.now();
  return '${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}';
}

/// Server task names are Chinese; the task centre is bilingual, mapped by code.
const _taskCopy = {
  'like_5': ('rw_task_like', 'rw_task_like_desc'),
  'favorite_5': ('rw_task_favorite', 'rw_task_favorite_desc'),
  'watch_8': ('rw_task_watch', 'rw_task_watch_desc'),
};

/// Daily task centre: balance, check-in streak and per-task progress with
/// claim buttons — the App twin of H5 `RewardsPage.tsx`. Signed-out visitors
/// still see the cards (`/v1/tasks`), only the numbers are personal.
class RewardsScreen extends StatefulWidget {
  const RewardsScreen({super.key, required this.language, required this.onExplore});

  final String language;

  /// "Go do it" buttons jump to the tab where the metric can be completed.
  final void Function(String target) onExplore; // 'home' | 'foryou'

  @override
  State<RewardsScreen> createState() => _RewardsScreenState();
}

class _RewardsScreenState extends State<RewardsScreen> {
  Map<String, dynamic>? data;
  String state = 'idle'; // idle | loading | ready | error
  String? busy;
  bool checkedToday = false;

  Copy get copy => Copy(widget.language);

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((prefs) {
      if (mounted) setState(() => checkedToday = prefs.getString(_checkinKey) == _localDay());
    });
    // SessionScope lookups are only legal after initState, so defer the load.
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  Future<void> load() async {
    final session = SessionScope.maybeOf(context);
    if (state != 'ready') setState(() => state = 'loading');
    try {
      data = session != null && session.isSignedIn
          ? await session.api.getJson('/v1/me/tasks')
          : await MiniApi().getJson('/v1/tasks');
      state = 'ready';
    } catch (_) {
      state = 'error';
    }
    if (mounted) setState(() {});
  }

  Future<void> _markChecked() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_checkinKey, _localDay());
    if (mounted) setState(() => checkedToday = true);
  }

  Future<void> checkin(Session session) async {
    setState(() => busy = 'checkin');
    try {
      final result = await session.api.postJson('/v1/me/checkin', {});
      await _markChecked();
      if (!mounted) return;
      toast(context,
          result['replayed'] == true ? copy.t('me_checkin_already') : copy.t('rw_claim_ok', {'amount': result['amount']}));
      await load();
      unawaited(session.refresh());
    } on ApiException {
      if (mounted) toast(context, copy.t('rw_claim_fail'));
    } finally {
      if (mounted) setState(() => busy = null);
    }
  }

  Future<void> claim(Session session, String code) async {
    setState(() => busy = code);
    try {
      final result = await session.api.postJson('/v1/me/tasks/$code/claim', {});
      if (!mounted) return;
      toast(
        context,
        result['replayed'] == true ? copy.t('rw_claimed') : copy.t('rw_claim_ok', {'amount': result['amount']}),
      );
      await load();
      unawaited(session.refresh());
    } on ApiException catch (e) {
      if (!mounted) return;
      toast(context, copy.t(e.status == 409 ? 'rw_not_done' : 'rw_claim_fail'));
    } finally {
      if (mounted) setState(() => busy = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = SessionScope.maybeOf(context);
    if (session == null || session.booting) return const Center(child: CircularProgressIndicator(color: red));
    if (state == 'error') return RetryBody(label: copy.t('retry'), onRetry: load);
    final payload = data;
    if (payload == null) return const Center(child: CircularProgressIndicator(color: red));

    final checkinInfo = (payload['checkin'] as Map?)?.cast<String, dynamic>() ?? const {};
    final checked = checkinInfo['checked_today'] == true || checkedToday;
    final streak = (checkinInfo['streak'] as int?) ?? 0;
    final amount = (checkinInfo['amount'] as int?) ?? 0;
    final balance = (payload['balance'] as int?) ?? 0;
    final items = ((payload['items'] as List?) ?? []).cast<Map<String, dynamic>>();

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        // Hero: title + balance.
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(copy.t('rw_title'), style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 4),
                  Text(copy.t('rw_sub'), style: const TextStyle(color: Colors.white54, fontSize: 12)),
                ],
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
              decoration: BoxDecoration(color: const Color(0xFF2A1E21), borderRadius: BorderRadius.circular(18)),
              child: Row(
                children: [
                  const Icon(Icons.monetization_on, color: gold, size: 15),
                  const SizedBox(width: 4),
                  Text('$balance', style: const TextStyle(color: gold, fontSize: 15, fontWeight: FontWeight.w800)),
                  Text(' ${copy.t('coins')}', style: const TextStyle(color: Colors.white54, fontSize: 11)),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        // Check-in strip.
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFF1C1315),
            borderRadius: BorderRadius.circular(12),
            border: checked ? Border.all(color: gold.withValues(alpha: 0.3)) : null,
          ),
          child: Row(
            children: [
              Icon(Icons.event_available, color: checked ? gold : Colors.white70, size: 22),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      copy.t('rw_checkin_streak', {'n': streak}),
                      style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w700),
                    ),
                    Text(
                      copy.t(checked ? 'rw_checkin_done' : 'rw_checkin_ready', {'amount': amount}),
                      style: const TextStyle(color: Colors.white54, fontSize: 11.5),
                    ),
                  ],
                ),
              ),
              _actionButton(
                label: copy.t(checked ? 'me_checkin_done' : 'rw_checkin_btn'),
                filled: !checked,
                enabled: !checked && busy != 'checkin',
                onTap: () => session.requireAuth(() => checkin(session)),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        for (final task in items) _taskCard(session, task),
      ],
    );
  }

  Widget _taskCard(Session session, Map<String, dynamic> task) {
    final code = task['code'] as String? ?? '';
    final progress = (task['progress'] as int?) ?? 0;
    final target = (task['target'] as int?) ?? 1;
    final claimed = task['claimed'] == true;
    final done = progress >= target;
    final pct = (progress / target).clamp(0.0, 1.0);
    final metric = task['metric'] as String? ?? '';
    final copyKeys = _taskCopy[code];
    final name = copyKeys != null ? copy.t(copyKeys.$1) : (task['name'] as String? ?? '');
    final desc = copyKeys != null ? copy.t(copyKeys.$2) : (task['description'] as String? ?? '');

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFF1C1315),
        borderRadius: BorderRadius.circular(12),
        border: claimed ? Border.all(color: Colors.white12) : null,
      ),
      child: Row(
        children: [
          Icon(
            metric == 'like'
                ? Icons.favorite_border
                : metric == 'favorite'
                    ? Icons.star_border
                    : Icons.play_circle_outline,
            color: claimed ? Colors.white38 : gold,
            size: 22,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(name, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w700)),
                    ),
                    Text(
                      '+${task['amount'] ?? 0}',
                      style: const TextStyle(color: gold, fontSize: 12, fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(desc, style: const TextStyle(color: Colors.white54, fontSize: 11.5)),
                const SizedBox(height: 6),
                ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(
                    value: pct,
                    minHeight: 5,
                    backgroundColor: Colors.white10,
                    valueColor: AlwaysStoppedAnimation(claimed ? Colors.white24 : gold),
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  claimed ? copy.t('rw_claimed') : '$progress/$target',
                  style: const TextStyle(color: Colors.white38, fontSize: 10.5),
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          _actionButton(
            label: copy.t(claimed ? 'rw_claimed' : done ? 'rw_claim' : 'rw_go'),
            filled: done && !claimed,
            enabled: !claimed && busy != code,
            onTap: () {
              if (claimed) return;
              if (done) {
                session.requireAuth(() => claim(session, code));
              } else {
                session.requireAuth(() => widget.onExplore(metric == 'complete' ? 'foryou' : 'home'));
              }
            },
          ),
        ],
      ),
    );
  }

  Widget _actionButton({
    required String label,
    required bool filled,
    required bool enabled,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
        decoration: BoxDecoration(
          color: !enabled
              ? const Color(0x22FFFFFF)
              : filled
                  ? gold
                  : Colors.transparent,
          borderRadius: BorderRadius.circular(14),
          border: filled || !enabled ? null : Border.all(color: gold),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: !enabled
                ? Colors.white38
                : filled
                    ? Colors.black
                    : gold,
            fontSize: 12,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }
}
