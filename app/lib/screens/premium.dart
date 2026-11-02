import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api.dart';
import '../copy.dart';
import '../session.dart';
import '../toast.dart';
import '../ui.dart';

const _monthNamesEn = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// `2026-10-20T08:00:00Z` → "Oct 20, 2026" / "2026年10月20日".
String formatDay(String? iso, String language) {
  if (iso == null || iso.isEmpty) return '—';
  final date = DateTime.tryParse(iso);
  if (date == null) return '—';
  final local = date.toLocal();
  if (language == 'zh') return '${local.year}年${local.month}月${local.day}日';
  return '${_monthNamesEn[local.month - 1]} ${local.day}, ${local.year}';
}

/// Bilingual `{field}_zh` / `{field}_en` picker used by plans and orders.
String localizedField(Map<String, dynamic> row, String field, String language) {
  final zh = (row['${field}_zh'] as String?) ?? '';
  final en = (row['${field}_en'] as String?) ?? '';
  return language == 'zh' && zh.isNotEmpty ? zh : en;
}

/// `2026-10-20T08:00:00Z` → "10/20 16:00" style short timestamp.
String formatWhen(String iso, String language) {
  final date = DateTime.tryParse(iso);
  if (date == null) return '';
  final local = date.toLocal();
  final hh = local.hour.toString().padLeft(2, '0');
  final mm = local.minute.toString().padLeft(2, '0');
  if (language == 'zh') return '${local.month}月${local.day}日 $hh:$mm';
  return '${_monthNamesEn[local.month - 1]} ${local.day}, $hh:$mm';
}

/// Subscription / pass upsell and account controls — the App twin of H5
/// `ui/PremiumSheet.tsx`. Prices come from `/v1/plans`; checkout jumps to
/// Stripe in the system browser, and the shell refreshes `/v1/me` on resume.
class PremiumSheet extends StatefulWidget {
  const PremiumSheet({super.key, required this.session, required this.language, required this.onClose});

  final Session session;
  final String language;
  final VoidCallback onClose;

  @override
  State<PremiumSheet> createState() => _PremiumSheetState();
}

class _PremiumSheetState extends State<PremiumSheet> {
  List<Map<String, dynamic>> plans = [];
  String mode = 'one_time'; // subscription | one_time (server decides for new purchases)
  String state = 'loading'; // loading | ready | error
  bool showPlans = false;
  String? busy;
  String error = '';

  Copy get copy => Copy(widget.language);

  Map<String, dynamic>? get membership => widget.session.membership;
  bool get active => membership?['active'] == true;
  bool get isPass => membership?['billing_mode'] == 'one_time';
  bool get showActive => active && !showPlans;

  @override
  void initState() {
    super.initState();
    widget.session.api.getJson('/v1/plans').then((data) {
      if (!mounted) return;
      setState(() {
        plans = ((data['items'] as List?) ?? []).cast<Map<String, dynamic>>();
        mode = (data['billing_mode'] as String?) ?? 'subscription';
        state = 'ready';
      });
    }).catchError((_) {
      if (mounted) setState(() => state = 'error');
    });
  }

  Future<void> subscribe(String code) async {
    setState(() {
      busy = code;
      error = '';
    });
    try {
      final result = await widget.session.api.postJson('/v1/billing/checkout', {'plan_code': code});
      final url = Uri.parse(result['url'] as String);
      await launchUrl(url, mode: LaunchMode.externalApplication);
      // The profile refresh happens when the app is resumed (HomeShell).
    } on ApiException catch (e) {
      setState(() {
        error = e.status == 401
            ? copy.t('premium_need_sign_in')
            : e.message.contains('0.50')
                ? copy.t('premium_min_price')
                : copy.t('premium_failed');
      });
    } finally {
      if (mounted) setState(() => busy = null);
    }
  }

  Future<void> stopRenewing() async {
    setState(() {
      busy = 'cancel';
      error = '';
    });
    try {
      await widget.session.api.postJson('/v1/billing/cancel', {});
      await widget.session.refresh();
      if (!mounted) return;
      toast(context, copy.t('premium_cancel_done'));
      widget.onClose();
    } on ApiException {
      setState(() {
        busy = null;
        error = copy.t('premium_cancel_failed');
      });
    }
  }

  String unitOf(Map<String, dynamic> plan) =>
      copy.t(mode == 'one_time' ? 'premium_span_${plan['code']}' : 'premium_unit_${plan['code']}');

  @override
  Widget build(BuildContext context) {
    final title = showActive
        ? (isPass ? copy.t('premium_title_pass') : copy.t('premium_title_active'))
        : copy.t('premium_title');
    return AppSheet(
      title: title,
      onClose: widget.onClose,
      heightFactor: 0.78,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: showActive ? _activeView() : _plansView(),
      ),
    );
  }

  Widget _activeView() {
    final m = membership!;
    final canceling = m['canceling'] == true;
    final until = formatDay(m['until'] as String?, widget.language);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: const Color(0xFF241A1D),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: canceling ? Colors.white24 : gold.withValues(alpha: 0.4)),
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
                      copy.t('premium_plan_${m['plan_code'] ?? 'monthly'}'),
                      style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      copy.t(canceling ? 'premium_ends_on' : 'premium_active_until', {'date': until}),
                      style: const TextStyle(color: Colors.white54, fontSize: 12),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(color: gold.withValues(alpha: 0.16), borderRadius: BorderRadius.circular(8)),
                child: Text(
                  copy.t(isPass ? 'premium_pass_badge' : 'premium_badge_active'),
                  style: const TextStyle(color: gold, fontSize: 11, fontWeight: FontWeight.w600),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        for (final key in ['premium_benefit_all', 'premium_benefit_noads', 'premium_benefit_hd'])
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              children: [
                const Icon(Icons.check_circle_outline, color: gold, size: 15),
                const SizedBox(width: 8),
                Text(copy.t(key), style: const TextStyle(color: Colors.white70, fontSize: 13)),
              ],
            ),
          ),
        const SizedBox(height: 14),
        if (mode == 'one_time' && !isPass)
          OutlinedButton(
            onPressed: () => setState(() => showPlans = true),
            child: Text(copy.t('premium_buy_pass_cta')),
          )
        else if (isPass) ...[
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
            onPressed: () => setState(() => showPlans = true),
            child: Text(copy.t('premium_extend_cta')),
          ),
          const SizedBox(height: 8),
          Text(copy.t('premium_extend_note'), style: const TextStyle(color: Colors.white38, fontSize: 11)),
        ] else if (canceling)
          Text(copy.t('premium_canceled_note'), style: const TextStyle(color: Colors.white38, fontSize: 12))
        else ...[
          TextButton(
            onPressed: busy == null ? stopRenewing : null,
            child: Text(
              busy == 'cancel' ? copy.t('premium_canceling') : copy.t('premium_cancel_cta'),
              style: const TextStyle(color: Colors.white54, fontSize: 13),
            ),
          ),
          Text(copy.t('premium_cancel_hint'), style: const TextStyle(color: Colors.white38, fontSize: 11)),
        ],
        if (error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(error, style: const TextStyle(color: Color(0xFFFF6B6E), fontSize: 12)),
          ),
      ],
    );
  }

  Widget _plansView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          copy.t(mode == 'one_time' ? 'premium_lede_onetime' : 'premium_lede'),
          style: const TextStyle(color: Colors.white54, fontSize: 13),
        ),
        const SizedBox(height: 14),
        if (state == 'loading') const Center(child: CircularProgressIndicator(color: red)),
        if (state == 'error') Center(child: Text(copy.t('err_generic'), style: const TextStyle(color: Colors.white54))),
        for (final plan in plans) _planCard(plan),
        if (error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(error, textAlign: TextAlign.center, style: const TextStyle(color: Color(0xFFFF6B6E), fontSize: 12)),
          ),
        const SizedBox(height: 6),
        Text(
          copy.t(mode == 'one_time' ? 'premium_note_onetime' : 'premium_note'),
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white38, fontSize: 11),
        ),
      ],
    );
  }

  Widget _planCard(Map<String, dynamic> plan) {
    final benefits = (plan['benefits'] as Map?)?.cast<String, dynamic>() ?? const {};
    final best = benefits['best'] == true;
    final items = ((benefits['items'] as List?) ?? []).cast<String>();
    final code = plan['code'] as String? ?? '';
    final name = localizedField(plan, 'name', widget.language);
    final price = ((plan['price_cents'] as num?) ?? 0) / 100;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF241A1D),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: best ? gold : Colors.white12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (best)
            Align(
              alignment: Alignment.centerLeft,
              child: Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(color: gold, borderRadius: BorderRadius.circular(8)),
                child: Text(copy.t('premium_popular'), style: const TextStyle(color: Colors.black, fontSize: 10, fontWeight: FontWeight.w800)),
              ),
            ),
          Row(
            children: [
              Expanded(child: Text(name, style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700))),
              Text.rich(
                TextSpan(
                  children: [
                    TextSpan(
                      text: '\$${price.toStringAsFixed(2)}',
                      style: const TextStyle(color: gold, fontSize: 17, fontWeight: FontWeight.w800),
                    ),
                    TextSpan(text: ' /${unitOf(plan)}', style: const TextStyle(color: Colors.white54, fontSize: 11)),
                  ],
                ),
              ),
            ],
          ),
          if (items.isNotEmpty) ...[
            const SizedBox(height: 8),
            for (final item in items)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text('· $item', style: const TextStyle(color: Colors.white54, fontSize: 12)),
              ),
          ],
          const SizedBox(height: 10),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 12)),
            onPressed: busy == null ? () => subscribe(code) : null,
            child: Text(
              busy == code
                  ? copy.t('premium_redirecting')
                  : copy.t(mode == 'one_time' ? 'premium_buy_cta' : 'premium_cta'),
            ),
          ),
        ],
      ),
    );
  }
}

/// Purchase history (`GET /v1/me/orders`), the App twin of H5 `OrdersSheet`.
class OrdersSheet extends StatelessWidget {
  const OrdersSheet({super.key, required this.session, required this.language, required this.onClose});

  final Session session;
  final String language;
  final VoidCallback onClose;

  static const _statusKey = {
    'paid': 'orders_paid',
    'created': 'orders_pending',
    'failed': 'orders_failed',
    'refunded': 'orders_refunded',
  };

  @override
  Widget build(BuildContext context) {
    final copy = Copy(language);
    return AppSheet(
      title: copy.t('orders_title'),
      onClose: onClose,
      heightFactor: 0.62,
      child: FutureBuilder<Map<String, dynamic>>(
        future: session.api.getJson('/v1/me/orders?limit=20&offset=0'),
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Center(child: Text(copy.t('err_generic'), style: const TextStyle(color: Colors.white54)));
          }
          if (!snapshot.hasData) return const Center(child: CircularProgressIndicator(color: red));
          final items = ((snapshot.data!['items'] as List?) ?? []).cast<Map<String, dynamic>>();
          if (items.isEmpty) {
            return Center(child: Text(copy.t('orders_empty'), style: const TextStyle(color: Colors.white54)));
          }
          return ListView.builder(
            shrinkWrap: true,
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
            itemCount: items.length,
            itemBuilder: (_, i) {
              final order = items[i];
              final status = order['status'] as String? ?? 'created';
              final name = localizedField(order, 'plan_name', language);
              final amount = ((order['amount_cents'] as num?) ?? 0) / 100;
              final when = formatWhen((order['paid_at'] ?? order['created_at']) as String? ?? '', language);
              final isPass = order['billing_mode'] == 'one_time';
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(name, style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600)),
                          const SizedBox(height: 2),
                          Text(
                            '$when · ${copy.t(isPass ? 'orders_pass' : 'orders_sub')}',
                            style: const TextStyle(color: Colors.white54, fontSize: 11),
                          ),
                        ],
                      ),
                    ),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text('\$${amount.toStringAsFixed(2)}', style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700)),
                        Text(
                          copy.t(_statusKey[status] ?? 'orders_pending'),
                          style: TextStyle(
                            color: status == 'paid' ? gold : Colors.white54,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              );
            },
          );
        },
      ),
    );
  }
}
