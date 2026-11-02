import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/screens/home.dart';

Map<String, dynamic> _drama(int id, String title) => {
      'id': id,
      'title': title,
      'cover_url': '',
      'position': id,
    };

final _config = jsonEncode({
  'tabs': [
    {'code': 'discover', 'title_en': 'Discover', 'title_zh': '发现', 'position': 0, 'locked': true, 'group_id': null},
    {'code': 'english', 'title_en': 'English', 'title_zh': '英语', 'position': 1, 'locked': false, 'group_id': 2},
    {'code': 'ranking', 'title_en': 'Ranking', 'title_zh': '榜单', 'position': 2, 'locked': false, 'group_id': 4},
  ],
  'copies': {},
});

final _groups = jsonEncode({
  'banners': [
    {'id': 1, 'drama_id': 1, 'slot': 'home', 'title': 'My Bodyguard Husband Is the Boss', 'cover_url': '', 'kicker': 'New premiere', 'headline': 'My Bodyguard Husband Is the Boss'},
    {'id': 2, 'drama_id': 2, 'slot': 'home', 'title': 'In Love With My Ex Uncle', 'cover_url': '', 'kicker': '', 'headline': ''},
  ],
  'groups': [
    {'id': 3, 'name': 'New', 'position': 0, 'style': 'poster3', 'badge': 'rank', 'dramas': [_drama(1, 'Alpha'), _drama(2, 'Beta'), _drama(3, 'Gamma'), _drama(4, 'Delta'), _drama(5, 'Epsilon'), _drama(6, 'Zeta')]},
    {'id': 2, 'name': 'Fresh Drops', 'position': 1, 'style': 'grid2', 'badge': 'label', 'dramas': [_drama(7, 'Eta'), _drama(8, 'Theta'), _drama(9, 'Iota'), _drama(10, 'Kappa'), _drama(11, 'Lambda')]},
    {'id': 4, 'name': 'Hot', 'position': 2, 'style': 'rank', 'badge': 'rank', 'dramas': [_drama(12, 'Mu'), _drama(13, 'Nu'), _drama(14, 'Xi'), _drama(15, 'Omicron')]},
  ],
});

class _FakeHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _FakeClient();
}

class _FakeClient extends Fake implements HttpClient {
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    final body = url.path.endsWith('/v1/config') ? _config : _groups;
    return _FakeRequest(body);
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
  Stream<S> transform<S>(StreamTransformer<List<int>, S> streamTransformer) => Stream<List<int>>.value(utf8.encode(body)).transform(streamTransformer);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _FakeHttpOverrides();

  testWidgets('home renders tabs, banner and floors without overflow', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: ThemeData(brightness: Brightness.dark),
      home: Scaffold(body: SafeArea(child: HomeScreen(language: 'en', onOpen: (_) {}))),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 300));
    if (find.text('Discover').evaluate().isEmpty) {
      debugDumpApp();
    }
    expect(find.text('Discover'), findsOneWidget);

    // Scroll through every floor; any RenderFlex overflow throws here.
    final list = find.byType(ListView).at(1);
    for (var i = 0; i < 10; i++) {
      await tester.drag(list, const Offset(0, -350));
      await tester.pump(const Duration(milliseconds: 250));
    }
    expect(find.text('Hot'), findsOneWidget);
    expect(find.text('NO.1'), findsOneWidget); // rank list card row
    expect(find.text('More'), findsWidgets); // floor 更多 buttons

    // 更多 opens the group's full list page.
    await tester.tap(find.text('More').last);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    // The last floor is the rank group "Hot" (Mu/Nu/Xi/Omicron).
    expect(find.text('Omicron'), findsOneWidget);
    await tester.pageBack();
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('Discover'), findsOneWidget);

    // Switch to a non-discover tab (group grid page).
    await tester.tap(find.text('Ranking'));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('Mu'), findsOneWidget);

    // Banner auto-advance timer must not outlive the widget.
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 6));
  });

  testWidgets('golden: discover floors layout', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.5;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: ThemeData(brightness: Brightness.dark),
      home: Scaffold(body: SafeArea(child: HomeScreen(language: 'en', onOpen: (_) {}))),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    await expectLater(find.byType(HomeScreen), matchesGoldenFile('home_top.png'));
    final list = find.byType(ListView).at(1);
    await tester.drag(list, const Offset(0, -700));
    await tester.pump(const Duration(milliseconds: 300));
    await expectLater(find.byType(HomeScreen), matchesGoldenFile('home_mid.png'));
    await tester.drag(list, const Offset(0, -800));
    await tester.pump(const Duration(milliseconds: 300));
    await expectLater(find.byType(HomeScreen), matchesGoldenFile('home_bottom.png'));
  });
}
