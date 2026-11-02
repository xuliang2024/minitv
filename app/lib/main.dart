import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'copy.dart';
import 'screens/detail.dart';
import 'screens/foryou.dart';
import 'screens/home.dart';
import 'screens/login_sheet.dart';
import 'screens/me.dart';
import 'screens/rewards.dart';
import 'screens/search.dart';
import 'session.dart';
import 'ui.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await initDeviceId();
  } catch (_) {
    // A broken plugin build must not keep the app on the splash screen; the
    // guest device id falls back to a value generated on first use.
  }
  runApp(MiniTvApp(session: Session()));
}

class MiniTvApp extends StatefulWidget {
  const MiniTvApp({super.key, this.session});
  final Session? session;
  @override
  State<MiniTvApp> createState() => _MiniTvAppState();
}

class _MiniTvAppState extends State<MiniTvApp> {
  late final Session session = widget.session ?? Session();
  String language = 'en';

  @override
  void initState() {
    super.initState();
    unawaited(Session.loadLocale().then((value) {
      if (mounted) setState(() => language = value);
    }));
    if (session.booting) unawaited(session.restore());
  }

  void setLanguage(String value) {
    setState(() => language = value);
    unawaited(Session.saveLocale(value));
  }

  @override
  Widget build(BuildContext context) {
    return SessionScope(
      session: session,
      child: MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: ThemeData(scaffoldBackgroundColor: bg, brightness: Brightness.dark),
        home: HomeShell(language: language, onLanguage: setLanguage),
      ),
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

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  int tab = const int.fromEnvironment('DEBUG_TAB', defaultValue: 0);
  // Debug-only entry so a build can open a drama's play page directly.
  int? dramaId = () {
    const raw = int.fromEnvironment('DEBUG_DRAMA', defaultValue: 0);
    return raw == 0 ? null : raw;
  }();
  String? page;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Returning from Stripe Checkout (external browser): the webhook has
    // landed by now, so re-read the profile before the viewer looks at it.
    if (state == AppLifecycleState.resumed) {
      unawaited(SessionScope.of(context).refresh());
    }
  }

  void openDrama(int id) => setState(() => dramaId = id);

  void openRewards() => setState(() {
        dramaId = null;
        page = null;
        tab = 2;
      });

  @override
  Widget build(BuildContext context) {
    final session = SessionScope.of(context);
    final copy = Copy(widget.language);

    Widget body;
    if (session.booting) {
      body = const Center(child: CircularProgressIndicator(color: red));
    } else if (dramaId != null) {
      // The drama's own play page: every episode, opened at the resume point.
      body = DetailScreen(
        dramaId: dramaId!,
        language: widget.language,
        onBack: () => setState(() => dramaId = null),
        onOpenRewards: openRewards,
      );
    } else if (page == 'search') {
      body = SearchScreen(language: widget.language, onOpen: openDrama, onBack: () => setState(() => page = null));
    } else {
      body = switch (tab) {
        0 => HomeScreen(language: widget.language, onOpen: openDrama),
        1 => ForYouScreen(
            language: widget.language,
            // Finishing (or tapping 选集 on) a feed slot opens that drama's play page.
            onOpenDrama: openDrama,
          ),
        2 => RewardsScreen(language: widget.language, onExplore: (target) => setState(() => tab = target == 'home' ? 0 : 1)),
        _ => MeScreen(language: widget.language, onLanguage: widget.onLanguage, onOpen: openDrama),
      };
    }

    final playing = dramaId != null;
    return Scaffold(
      // The play page is full-bleed: the video runs under the status bar and
      // its own overlay carries the back button, so no app bar there.
      extendBodyBehindAppBar: playing,
      appBar: playing
          ? null
          : AppBar(
              backgroundColor: bg,
              foregroundColor: Colors.white,
              title: Text(tab == 0 ? 'MiniTV' : copy.t(['nav.home', 'nav.foryou', 'nav.rewards', 'nav.me'][tab])),
              actions: [
                TextButton(
                  onPressed: () => widget.onLanguage(widget.language == 'zh' ? 'en' : 'zh'),
                  child: Text(
                    widget.language == 'zh' ? 'EN' : '中',
                    style: const TextStyle(color: Colors.white70, fontSize: 13),
                  ),
                ),
                IconButton(icon: const Icon(Icons.search), onPressed: () => setState(() => page = 'search')),
              ],
            ),
      body: Stack(
        fit: StackFit.expand,
        children: [
          Positioned.fill(child: body),
          if (session.loginOpen) Positioned.fill(child: LoginSheet(session: session, language: widget.language)),
        ],
      ),
      // The drama play page is full-bleed (design draft P8): no tab bar.
      bottomNavigationBar: playing
          ? null
          : NavigationBar(
              backgroundColor: sheet,
              indicatorColor: red,
              selectedIndex: tab,
              onDestinationSelected: (index) => setState(() {
                tab = index;
                dramaId = null;
                page = null;
              }),
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
