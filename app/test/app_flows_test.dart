import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:minitv/screens/comments.dart';
import 'package:minitv/screens/login_sheet.dart';
import 'package:minitv/screens/premium.dart';
import 'package:minitv/screens/rewards.dart';
import 'package:minitv/screens/unlock.dart';
import 'package:minitv/session.dart';
import 'package:minitv/ui.dart';

/// Canned API so every screen renders against realistic payloads.
const _profile = {
  'id': 1,
  'public_id': '3384865366',
  'email': 'ava.patel@example.com',
  'display_name': 'Ava Patel',
  'avatar_url': '',
  'bio': 'Romance > everything.',
  'coins': 420,
  'following_count': 21,
  'follower_count': 648,
  'received_likes': 1466,
  'membership': {'active': false, 'plan_code': null, 'until': null, 'canceling': false, 'billing_mode': 'one_time'},
};

const _tasks = {
  'today': '2026-10-20',
  'balance': 420,
  'checkin': {'checked_today': false, 'streak': 3, 'amount': 20},
  'items': [
    {'code': 'like_5', 'name': '点赞5部剧', 'description': '今天给5部剧点赞', 'metric': 'like', 'target': 5, 'progress': 3, 'amount': 30, 'claimed': false},
    {'code': 'favorite_5', 'name': '收藏5部剧', 'description': '收藏5部剧', 'metric': 'favorite', 'target': 5, 'progress': 5, 'amount': 30, 'claimed': false},
    {'code': 'watch_8', 'name': '看完8集', 'description': '看完8集', 'metric': 'complete', 'target': 8, 'progress': 8, 'amount': 50, 'claimed': true},
  ],
};

const _plans = {
  'billing_mode': 'subscription',
  'items': [
    {'code': 'weekly', 'name_en': 'MiniTV Weekly', 'name_zh': 'MiniTV 周卡', 'price_cents': 999, 'currency': 'USD', 'version': 2, 'version_id': 3, 'benefits': {'items': ['Every episode unlocked', 'No ads']}},
    {'code': 'monthly', 'name_en': 'MiniTV Monthly', 'name_zh': 'MiniTV 月卡', 'price_cents': 2999, 'currency': 'USD', 'version': 2, 'version_id': 4, 'benefits': {'best': true, 'items': ['Every episode unlocked', 'No ads', '1080p']}},
    {'code': 'yearly', 'name_en': 'MiniTV Yearly', 'name_zh': 'MiniTV 年卡', 'price_cents': 9999, 'currency': 'USD', 'version': 2, 'version_id': 5, 'benefits': {'items': ['Every episode unlocked']}},
  ],
};

const _comments = {
  'total': 2,
  'next_cursor': null,
  'items': [
    {
      'id': 11,
      'body': 'The twist in this episode got me.',
      'created_at': '2026-10-20T01:00:00Z',
      'like_count': 12,
      'liked': false,
      'mine': false,
      'user': {'display_name': 'Liam Hayes', 'avatar_url': ''},
    },
    {
      'id': 10,
      'body': 'Worth every coin.',
      'created_at': '2026-10-19T20:00:00Z',
      'like_count': 3,
      'liked': true,
      'mine': true,
      'user': {'display_name': 'Ava Patel', 'avatar_url': ''},
    },
  ],
};

const _orders = {
  'items': [
    {'order_no': 'MO123', 'plan_code': 'monthly', 'plan_name_en': 'MiniTV Monthly', 'plan_name_zh': 'MiniTV 月卡', 'amount_cents': 2999, 'currency': 'USD', 'billing_mode': 'subscription', 'status': 'paid', 'paid_at': '2026-10-19T12:00:00Z', 'created_at': '2026-10-19T11:59:00Z'},
  ],
  'has_more': false,
};

class _FakeHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _FakeClient();
}

class _FakeClient extends Fake implements HttpClient {
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    final path = url.path;
    final Object body = switch (path) {
      '/v1/auth/providers' => {'email': {'enabled': true}, 'google': {'enabled': false, 'client_id': null}},
      '/v1/me' => _profile,
      '/v1/me/tasks' => _tasks,
      '/v1/tasks' => _tasks,
      '/v1/plans' => _plans,
      '/v1/me/orders' => _orders,
      '/v1/me/history' => {'items': []},
      '/v1/me/favorites' => {'items': [], 'total': 0},
      '/v1/me/lists/like' => {'items': []},
      '/v1/me/messages' => {'items': [], 'unread': 2},
      '/v1/me/ledger' => {'items': []},
      '/v1/episodes/9/comments' => _comments,
      _ => {'items': []},
    };
    return _FakeRequest(jsonEncode(body));
  }

  @override
  void close({bool force = false}) {}
}

class _FakeRequest extends Fake implements HttpClientRequest {
  _FakeRequest(this.body);
  final String body;
  @override
  final headers = _FakeHeaders();
  @override
  void add(List<int> data) {}
  @override
  Future<HttpClientResponse> close() async => _FakeResponse(body);
}

class _FakeHeaders extends Fake implements HttpHeaders {
  @override
  void set(String name, Object value, {bool preserveHeaderCase = false}) {}
}

class _FakeResponse extends Fake implements HttpClientResponse {
  _FakeResponse(this.body);
  final String body;
  @override
  int get statusCode => 200;
  @override
  Stream<S> transform<S>(StreamTransformer<List<int>, S> streamTransformer) =>
      Stream<List<int>>.value(utf8.encode(body)).transform(streamTransformer);
}

Future<Session> _signedInSession() async {
  final session = Session();
  session.token = 'test-token';
  session.user = Map<String, dynamic>.from(_profile);
  session.booting = false;
  return session;
}

Widget _frame(Widget child, {double height = 640}) {
  return MaterialApp(
    theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
    home: Scaffold(
      body: SizedBox(width: 390, height: height, child: Stack(fit: StackFit.expand, children: [Positioned.fill(child: child)])),
    ),
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _FakeHttpOverrides();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('golden: unlock sheet shows price, balance and after', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(_frame(UnlockSheet(
      session: session,
      language: 'en',
      episode: const {'id': 9, 'episode_number': 7},
      price: 50,
      idempotencyKey: 'k-9',
      onClose: () {},
      onDone: (_) {},
      onNeedCoins: () {},
    )));
    await tester.pumpAndSettle();
    expect(find.text('Episode price'), findsOneWidget);
    expect(find.textContaining('420'), findsWidgets);
    expect(find.textContaining('370'), findsOneWidget); // 420 - 50
    await expectLater(find.byType(UnlockSheet), matchesGoldenFile('unlock_sheet.png'));
  });

  testWidgets('golden: locked episode offers coin unlock and premium', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(_frame(LockedEpisodeView(
      episode: const {'id': 9, 'episode_number': 7, 'unlock': {'coin_price': 50}},
      session: session,
      language: 'en',
      onUnlock: () {},
      onMember: () {},
      onNeedCoins: () {},
    )));
    await tester.pumpAndSettle();
    expect(find.text('Unlock EP 7'), findsOneWidget);
    expect(find.textContaining('Balance 420'), findsOneWidget);
    await expectLater(find.byType(LockedEpisodeView), matchesGoldenFile('locked_episode.png'));
  });

  testWidgets('comment sheet lists comments and likes', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(_frame(CommentSheet(
      session: session,
      language: 'en',
      episodeId: 9,
      onClose: () {},
    )));
    await tester.pumpAndSettle();
    expect(find.text('Worth every coin.'), findsOneWidget);
    expect(find.text('You'), findsOneWidget); // mine badge
    await expectLater(find.byType(CommentSheet), matchesGoldenFile('comment_sheet.png'));
  });

  testWidgets('golden: login sheet asks for an email', (tester) async {
    final session = Session();
    session.booting = false;
    await tester.pumpWidget(_frame(LoginSheet(session: session, language: 'en')));
    await tester.pumpAndSettle();
    expect(find.text('Send code'), findsOneWidget);
    await expectLater(find.byType(LoginSheet), matchesGoldenFile('login_sheet.png'));
  });

  testWidgets('golden: premium sheet lists the three plans', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(_frame(PremiumSheet(session: session, language: 'en', onClose: () {})));
    await tester.pumpAndSettle();
    expect(find.text('MiniTV Monthly'), findsOneWidget);
    expect(find.text('Most popular'), findsOneWidget);
    await expectLater(find.byType(PremiumSheet), matchesGoldenFile('premium_sheet.png'));
  });

  testWidgets('golden: orders sheet shows the paid order', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(_frame(OrdersSheet(session: session, language: 'en', onClose: () {})));
    await tester.pumpAndSettle();
    expect(find.text('Paid'), findsOneWidget);
    await expectLater(find.byType(OrdersSheet), matchesGoldenFile('orders_sheet.png'));
  });

  testWidgets('golden: rewards page with tasks and streak', (tester) async {
    final session = await _signedInSession();
    await tester.pumpWidget(SessionScope(
      session: session,
      child: MaterialApp(
        theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
        home: Scaffold(body: SizedBox(width: 390, height: 700, child: RewardsScreen(language: 'en', onExplore: (_) {}))),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Day 3 streak'), findsOneWidget);
    expect(find.text('3/5'), findsOneWidget);
    expect(find.text('Claim'), findsWidgets);
    await expectLater(find.byType(RewardsScreen), matchesGoldenFile('rewards.png'));
  });
}
