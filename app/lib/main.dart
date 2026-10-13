import 'package:flutter/material.dart';
import 'copy.dart';
import 'screens/account.dart';
import 'screens/channel.dart';
import 'screens/detail.dart';
import 'screens/player_screen.dart';
import 'screens/search.dart';
import 'ui.dart';

void main() {
  runApp(const MiniTvApp());
}

class MiniTvApp extends StatefulWidget {
  const MiniTvApp({super.key});
  @override
  State<MiniTvApp> createState() => _MiniTvAppState();
}

class _MiniTvAppState extends State<MiniTvApp> {
  String language = 'en';
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
      home: HomeShell(language: language, onLanguage: (value) => setState(() => language = value)),
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.language, required this.onLanguage});
  final String language;
  final ValueChanged<String> onLanguage;
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int tab = 0;
  int? dramaId;
  String? token;
  String? page;

  @override
  Widget build(BuildContext context) {
    final copy = Copy(widget.language);
    Widget body;
    if (page == 'player') {
      body = PlayerScreen(dramaId: dramaId ?? 0, episodeId: 0, token: token);
    } else if (dramaId != null) {
      body = DetailScreen(dramaId: dramaId!, onPlay: (_, _) => setState(() => page = 'player'));
    } else if (page == 'search') {
      body = SearchScreen(onOpen: (id) => setState(() => dramaId = id));
    } else if (page == 'login') {
      body = LoginScreen(onEmail: () => setState(() => page = 'register'));
    } else if (page == 'register') {
      body = RegisterScreen(onDone: (value) => setState(() { token = value; page = null; tab = 3; }));
    } else if (page != null) {
      body = SimplePage(title: copy.t(page!));
    } else {
      body = switch (tab) {
        0 => ChannelScreen(titleKey: 'nav.home', onOpen: (id) => setState(() => dramaId = id)),
        1 => PlayerScreen(dramaId: 0, episodeId: 0, token: token),
        2 => RewardsScreen(token: token),
        _ => MeScreen(onOpen: (route) => setState(() => page = route == 'coins' ? 'login' : route)),
      };
    }
    return Scaffold(
      appBar: AppBar(
        backgroundColor: bg,
        foregroundColor: Colors.white,
        title: Text(tab == 0 ? 'MiniTV' : copy.t(['nav.home', 'nav.foryou', 'nav.rewards', 'nav.me'][tab])),
        leading: dramaId != null || page != null ? IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => setState(() { dramaId = null; page = null; })) : IconButton(icon: const Icon(Icons.search), onPressed: () => setState(() => page = 'search')),
        actions: [
          TextButton(onPressed: () => widget.onLanguage(widget.language == 'zh' ? 'en' : 'zh'), child: Text(widget.language == 'zh' ? 'EN' : '中文', style: const TextStyle(color: gold))),
        ],
      ),
      body: body,
      bottomNavigationBar: NavigationBar(
        backgroundColor: sheet,
        indicatorColor: red,
        selectedIndex: tab,
        onDestinationSelected: (index) => setState(() { tab = index; dramaId = null; page = null; }),
        destinations: [
          NavigationDestination(icon: const Icon(Icons.home_outlined), label: copy.t('nav.home')),
          NavigationDestination(icon: const Icon(Icons.play_circle_outline), label: copy.t('nav.foryou')),
          NavigationDestination(icon: const Icon(Icons.card_giftcard), label: copy.t('nav.rewards')),
          NavigationDestination(icon: const Icon(Icons.person_outline), label: copy.t('nav.me')),
        ],
      ),
    );
  }

}
