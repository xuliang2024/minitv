import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/main.dart';

void main() {
  testWidgets('home shell shows four navigation labels', (tester) async {
    await tester.pumpWidget(const MiniTvApp());
    expect(find.text('Home'), findsWidgets);
    expect(find.text('For You'), findsOneWidget);
    expect(find.text('Rewards'), findsOneWidget);
    expect(find.text('Me'), findsOneWidget);
  });

  testWidgets('chinese locale switches navigation labels', (tester) async {
    await tester.pumpWidget(const MiniTvApp());
    await tester.tap(find.text('中文'));
    await tester.pump();
    expect(find.text('首页'), findsWidgets);
    expect(find.text('推荐'), findsOneWidget);
    expect(find.text('奖励'), findsOneWidget);
  });
}
