import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:minitv/main.dart';
import 'package:minitv/session.dart';
import 'package:minitv/ui.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('home shell shows four navigation labels', (tester) async {
    final session = Session();
    await session.restore();
    await tester.pumpWidget(MiniTvApp(session: session));
    await tester.pump();
    expect(find.text('Home'), findsWidgets);
    expect(find.text('For You'), findsOneWidget);
    expect(find.text('Rewards'), findsOneWidget);
    expect(find.text('Me'), findsOneWidget);
  });

  testWidgets('chinese locale switches navigation labels', (tester) async {
    final session = Session();
    await session.restore();
    await tester.pumpWidget(SessionScope(
      session: session,
      child: MaterialApp(
        theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
        home: HomeShell(language: 'zh', onLanguage: (_) {}),
      ),
    ));
    await tester.pump();
    expect(find.text('首页'), findsWidgets);
    expect(find.text('推荐'), findsOneWidget);
    expect(find.text('奖励'), findsOneWidget);
  });
}
