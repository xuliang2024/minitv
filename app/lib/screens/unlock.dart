import 'package:flutter/material.dart';

import '../api.dart';
import '../copy.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';

/// The paywalled episode placeholder with the three exits H5 shows:
/// unlock with coins (primary), go Premium (secondary), earn coins when short.
class LockedEpisodeView extends StatelessWidget {
  const LockedEpisodeView({
    super.key,
    required this.episode,
    required this.session,
    required this.language,
    required this.onUnlock,
    required this.onMember,
    required this.onNeedCoins,
  });

  final Map<String, dynamic> episode;

  /// Null outside the app shell (widget tests): behaves as a signed-out viewer.
  final Session? session;
  final String language;
  final VoidCallback onUnlock;
  final VoidCallback onMember;
  final VoidCallback onNeedCoins;

  @override
  Widget build(BuildContext context) {
    final copy = Copy(language);
    final price = ((episode['unlock'] as Map?) ?? const {})['coin_price'] as int? ?? 0;
    final balance = session?.coins ?? 0;
    final short = price - balance;
    final signedIn = session?.isSignedIn == true;
    final coinOnly = price > 0; // zero price means membership-only

    return Container(
      color: sheet,
      alignment: Alignment.center,
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.lock_outline, color: gold, size: 34),
          const SizedBox(height: 10),
          Text(
            copy.t('unlock_title', {'number': episode['episode_number']}),
            style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(copy.t('unlock_sub'), style: const TextStyle(color: Colors.white54, fontSize: 12), textAlign: TextAlign.center),
          if (coinOnly) ...[
            const SizedBox(height: 10),
            Text.rich(
              TextSpan(children: [
                TextSpan(
                  text: copy.t('unlock_price', {'price': price}),
                  style: const TextStyle(color: gold, fontSize: 16, fontWeight: FontWeight.w800),
                ),
                TextSpan(text: ' ${copy.t('unlock_per_episode')}', style: const TextStyle(color: Colors.white38, fontSize: 12)),
              ]),
            ),
          ],
          if (signedIn) ...[
            const SizedBox(height: 4),
            Text(
              copy.t('unlock_balance', {'balance': balance}),
              style: const TextStyle(color: Colors.white54, fontSize: 12),
            ),
          ],
          const SizedBox(height: 16),
          if (coinOnly)
            if (!signedIn)
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 12)),
                onPressed: onUnlock,
                child: Text(copy.t('unlock_login_cta')),
              )
            else if (short > 0)
              OutlinedButton(
                style: OutlinedButton.styleFrom(side: const BorderSide(color: gold)),
                onPressed: onNeedCoins,
                child: Text(copy.t('unlock_short_cta', {'short': short}), style: const TextStyle(color: gold)),
              )
            else
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 12)),
                onPressed: onUnlock,
                child: Text(copy.t('unlock_cta', {'price': price})),
              ),
          const SizedBox(height: 10),
          TextButton(
            onPressed: onMember,
            child: Text(
              copy.t(coinOnly ? 'unlock_member_cta' : 'premium_strip_cta'),
              style: const TextStyle(color: Colors.white70, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

/// One-shot confirmation for a coin unlock — the App twin of H5
/// `ui/UnlockSheet.tsx`.
///
/// Retrying reuses the caller's idempotency key, which is what makes a
/// timeout safe to repeat. The server's `have` is authoritative: a 402
/// re-bases the sheet instead of showing stale coins.
class UnlockSheet extends StatefulWidget {
  const UnlockSheet({
    super.key,
    required this.session,
    required this.language,
    required this.episode,
    required this.price,
    required this.idempotencyKey,
    required this.onClose,
    required this.onDone,
    required this.onNeedCoins,
  });

  final Session session;
  final String language;
  final Map<String, dynamic> episode;
  final int price;
  final String idempotencyKey;
  final VoidCallback onClose;
  final void Function(Map<String, dynamic> result) onDone;
  final VoidCallback onNeedCoins;

  @override
  State<UnlockSheet> createState() => _UnlockSheetState();
}

class _UnlockSheetState extends State<UnlockSheet> {
  bool busy = false;
  String? failure; // insufficient | failed
  int short = 0;
  late int liveBalance = widget.session.coins;

  Copy get copy => Copy(widget.language);

  int get after => liveBalance - widget.price;
  bool get insufficient => failure == 'insufficient' || after < 0;

  Future<void> confirm() async {
    if (busy || insufficient) return;
    setState(() {
      busy = true;
      failure = null;
    });
    try {
      final result = await widget.session.api.postJson(
        '/v1/unlock',
        {'episode_id': widget.episode['id']},
        headers: {'Idempotency-Key': widget.idempotencyKey},
      );
      widget.onDone(result);
    } on ApiException catch (error) {
      if (error.status == 402) {
        final detail = (error.data as Map?)?['detail'];
        final need = (detail is Map ? detail['need'] as int? : null) ?? widget.price;
        final have = (detail is Map ? detail['have'] as int? : null) ?? liveBalance;
        setState(() {
          liveBalance = have;
          failure = 'insufficient';
          short = (need - have).clamp(0, need);
        });
      } else if (error.status == 401) {
        // The session died: close; the auth layer re-opens the login sheet.
        widget.onClose();
      } else {
        setState(() => failure = 'failed');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = busy
        ? copy.t('unlock_confirming')
        : failure == 'failed'
            ? copy.t('unlock_retry')
            : copy.t('unlock_confirm', {'price': widget.price});

    return AppSheet(
      title: copy.t('unlock_sheet_title', {'number': widget.episode['episode_number']}),
      onClose: widget.onClose,
      heightFactor: 0.52,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _row(copy.t('unlock_row_price'), copy.t('unlock_price', {'price': widget.price}), gold),
            _row(copy.t('unlock_row_balance'), copy.t('unlock_price', {'price': liveBalance}), Colors.white),
            _row(copy.t('unlock_row_after'), copy.t('unlock_price', {'price': after < 0 ? 0 : after}),
                after < 0 ? const Color(0xFFFF6B6E) : gold),
            if (failure != null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(
                  failure == 'insufficient' ? copy.t('unlock_insufficient', {'short': short}) : copy.t('unlock_failed'),
                  style: const TextStyle(color: Color(0xFFFF6B6E), fontSize: 12),
                ),
              ),
            const SizedBox(height: 10),
            Text(copy.t('unlock_note'), style: const TextStyle(color: Colors.white38, fontSize: 11)),
            const SizedBox(height: 14),
            if (insufficient)
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: gold, padding: const EdgeInsets.symmetric(vertical: 13)),
                onPressed: widget.onNeedCoins,
                child: Text(copy.t('unlock_need_coins'), style: const TextStyle(color: Colors.black)),
              )
            else
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
                onPressed: busy ? null : confirm,
                child: Text(label),
              ),
            TextButton(
              onPressed: busy ? null : widget.onClose,
              child: Text(copy.t('close'), style: const TextStyle(color: Colors.white54)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _row(String label, String value, Color color) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(
        children: [
          Text(label, style: const TextStyle(color: Colors.white54, fontSize: 13)),
          const Spacer(),
          Text(value, style: TextStyle(color: color, fontSize: 14, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}

/// Toast the unlock outcome the same way H5 does.
void toastUnlockResult(BuildContext context, String language, Map<String, dynamic> result) {
  final copy = Copy(language);
  final status = result['status'] as String? ?? '';
  if (status == 'subscription') {
    toast(context, copy.t('unlock_member_active'));
  } else if (status == 'already_unlocked') {
    toast(context, copy.t('unlock_already'));
  } else {
    toast(context, copy.t('unlock_success', {'balance': result['balance_after'] ?? 0}));
  }
}
