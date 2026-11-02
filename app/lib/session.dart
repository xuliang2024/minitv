import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

/// Debug-only session pin for screenshots/QA: `--dart-define=DEBUG_TOKEN=…`.
const _debugToken = String.fromEnvironment('DEBUG_TOKEN', defaultValue: '');

/// App-wide session: the saved bearer token plus the `/v1/me` profile.
///
/// Mirrors H5's `AuthProvider`: the token lives in SharedPreferences, the
/// profile is refreshed after any wallet/membership change, a 401 anywhere
/// flips the app back to guest, and `requireAuth` replays the blocked action
/// once the login sheet completes.
class Session extends ChangeNotifier {
  static const _tokenKey = 'minitv_app_token';
  static const _localeKey = 'minitv_app_locale';

  String? token;
  Map<String, dynamic>? user;

  /// True while the saved token is being validated on boot.
  bool booting = true;
  bool loginOpen = false;
  VoidCallback? _pendingAction;

  bool get isSignedIn => token != null && user != null;
  bool get isGuest => !booting && !isSignedIn;
  int get coins => (user?['coins'] as int?) ?? 0;
  Map<String, dynamic>? get membership => (user?['membership'] as Map?)?.cast<String, dynamic>();

  MiniApi get api => MiniApi(token: token);

  Session() {
    MiniApi.onUnauthorized = _handleUnauthorized;
  }

  /// Restore the saved token and validate it against `/v1/me`.
  Future<void> restore() async {
    booting = true;
    if (_debugToken.isNotEmpty) {
      token = _debugToken;
    } else {
      final prefs = await SharedPreferences.getInstance();
      token = prefs.getString(_tokenKey);
    }
    if (token == null || token!.isEmpty) {
      booting = false;
      notifyListeners();
      return;
    }
    try {
      user = await api.getJson('/v1/me');
    } on ApiException catch (error) {
      if (error.status == 401) {
        // Dead token: drop it and fall back to guest.
        await _save(null);
        token = null;
        user = null;
      }
      // A network error keeps the token so the next launch retries.
    }
    booting = false;
    notifyListeners();
  }

  Future<void> _save(String? value) async {
    if (_debugToken.isNotEmpty) return;
    final prefs = await SharedPreferences.getInstance();
    if (value == null) {
      await prefs.remove(_tokenKey);
    } else {
      await prefs.setString(_tokenKey, value);
    }
  }

  /// Re-read `/v1/me` after the profile or wallet changed.
  Future<void> refresh() async {
    if (token == null) return;
    try {
      user = await api.getJson('/v1/me');
      notifyListeners();
    } on ApiException {
      /* a 401 here already went through onUnauthorized */
    }
  }

  /// Store the new session, load the profile, then replay whatever was blocked.
  Future<void> completeLogin(String newToken) async {
    token = newToken;
    await _save(newToken);
    user = await api.getJson('/v1/me');
    loginOpen = false;
    notifyListeners();
    final action = _pendingAction;
    _pendingAction = null;
    action?.call();
  }

  Future<void> logout() async {
    _pendingAction = null;
    loginOpen = false;
    token = null;
    user = null;
    await _save(null);
    notifyListeners();
  }

  void _handleUnauthorized() {
    if (!isSignedIn) return;
    token = null;
    user = null;
    if (_debugToken.isEmpty) _save(null);
    // The tap that needed a session still deserves the login sheet.
    if (_pendingAction != null) loginOpen = true;
    notifyListeners();
  }

  /// Run [action] now when signed in; otherwise ask for a sign-in and replay
  /// the exact action once the session lands (H5 `requireAuth` equivalent).
  void requireAuth(VoidCallback action) {
    if (isSignedIn) {
      action();
      return;
    }
    _pendingAction = action;
    if (!booting) {
      loginOpen = true;
      notifyListeners();
    }
  }

  void openLogin() {
    loginOpen = true;
    notifyListeners();
  }

  void closeLogin() {
    _pendingAction = null;
    loginOpen = false;
    notifyListeners();
  }

  /// Locale persisted across launches.
  static Future<String> loadLocale() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_localeKey) == 'zh' ? 'zh' : 'en';
  }

  static Future<void> saveLocale(String locale) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_localeKey, locale);
  }
}

/// Inherited access so any page can reach the session without prop drilling.
class SessionScope extends InheritedNotifier<Session> {
  const SessionScope({super.key, required Session session, required super.child}) : super(notifier: session);

  /// Null outside the app shell (widget tests) — callers treat that as guest.
  static Session? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<SessionScope>()?.notifier;

  static Session of(BuildContext context) {
    final session = maybeOf(context);
    assert(session != null, 'SessionScope missing above this widget');
    return session!;
  }
}
