import 'dart:async';

import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart' show GoogleSignInException, GoogleSignInExceptionCode;

import '../api.dart';
import '../auth/google.dart';
import '../copy.dart';
import '../session.dart';
import '../ui.dart';

const _codeLength = 6;
const _resendSeconds = 60;

final _emailRe = RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$');

/// Email-code sign-in, mirroring H5's `LoginSheet`: `/v1/auth/email/code` then
/// `/v1/auth/email/authenticate` (signs in or registers on the spot).
///
/// Google sign-in runs through the native `google_sign_in` plugin, which mints
/// an ID token the backend verifies at `/v1/auth/google`. The button only shows
/// when `/v1/auth/providers` says Google is configured, so a deployment without
/// OAuth clients never renders a dead control.
class LoginSheet extends StatefulWidget {
  const LoginSheet({super.key, required this.session, required this.language});

  final Session session;
  final String language;

  @override
  State<LoginSheet> createState() => _LoginSheetState();
}

class _LoginSheetState extends State<LoginSheet> {
  final email = TextEditingController();
  final code = TextEditingController();
  String step = 'email'; // email | code
  String? error;
  String busy = ''; // sending | verifying
  int cooldown = 0;
  Timer? timer;
  Map<String, dynamic>? providers;

  Copy get copy => Copy(widget.language);

  /// The browser client id served by `/v1/auth/providers`. Android passes it as
  /// `serverClientId` so its token is addressed to the audience the backend
  /// already trusts; iOS keeps its own audience and only needs this to ask for
  /// an offline code. Null means the deployment has no OAuth client at all.
  String? get googleServerClientId {
    final google = providers?['google'];
    if (google is! Map || google['enabled'] != true) return null;
    final id = google['client_id'];
    return id is String && id.isNotEmpty ? id : null;
  }

  bool get googleReady => googleServerClientId != null && GoogleAuth.supported;

  @override
  void initState() {
    super.initState();
    MiniApi().getJson('/v1/auth/providers').then((data) {
      if (mounted) setState(() => providers = data);
    }).catchError((_) {});
  }

  @override
  void dispose() {
    timer?.cancel();
    email.dispose();
    code.dispose();
    super.dispose();
  }

  String messageFor(ApiException error) {
    switch (error.message) {
      case 'invalid code':
        return copy.t('err_invalid_code');
      case 'code expired':
        return copy.t('err_code_expired');
      case 'too many codes':
        return copy.t('err_rate_limited');
      case 'email delivery failed':
        return copy.t('err_mail_failed');
      default:
        return error.status == 0 ? copy.t('err_network') : copy.t('err_generic');
    }
  }

  Future<void> sendCode() async {
    final value = email.text.trim().toLowerCase();
    if (!_emailRe.hasMatch(value)) {
      setState(() => error = copy.t('err_email_invalid'));
      return;
    }
    setState(() {
      busy = 'sending';
      error = null;
    });
    try {
      await MiniApi().postJson('/v1/auth/email/code', {'email': value, 'purpose': 'login'});
      setState(() {
        email.text = value;
        code.clear();
        step = 'code';
        cooldown = _resendSeconds;
      });
      timer?.cancel();
      timer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (!mounted) return;
        setState(() {
          cooldown -= 1;
          if (cooldown <= 0) timer?.cancel();
        });
      });
    } on ApiException catch (e) {
      setState(() => error = messageFor(e));
    } finally {
      if (mounted) setState(() => busy = '');
    }
  }

  /// Native Google sign-in: the plugin mints an ID token, the backend verifies
  /// it at `/v1/auth/google` (signature + audience, then merges by verified
  /// email) and hands back the same session token email sign-in returns.
  Future<void> signInWithGoogle() async {
    final serverClientId = googleServerClientId;
    if (serverClientId == null) return;
    setState(() {
      busy = 'google';
      error = null;
    });
    try {
      final idToken = await GoogleAuth.idToken(serverClientId: serverClientId);
      if (idToken == null) return; // Dismissed the Google sheet.
      final result = await MiniApi().postJson('/v1/auth/google', {'id_token': idToken});
      await widget.session.completeLogin(result['token'] as String);
    } on GoogleSignInException catch (e) {
      if (e.code != GoogleSignInExceptionCode.canceled) {
        setState(() => error = copy.t('err_google'));
      }
    } on ApiException catch (e) {
      setState(() => error = messageFor(e));
    } catch (_) {
      setState(() => error = copy.t('err_google'));
    } finally {
      if (mounted) setState(() => busy = '');
    }
  }

  Future<void> verify() async {
    if (code.text.trim().length != _codeLength) {
      setState(() => error = copy.t('err_invalid_code'));
      return;
    }
    setState(() {
      busy = 'verifying';
      error = null;
    });
    try {
      final result = await MiniApi().postJson('/v1/auth/email/authenticate', {
        'email': email.text.trim().toLowerCase(),
        'code': code.text.trim(),
      });
      await widget.session.completeLogin(result['token'] as String);
    } on ApiException catch (e) {
      setState(() => error = messageFor(e));
    } finally {
      if (mounted) setState(() => busy = '');
    }
  }

  @override
  Widget build(BuildContext context) {
    final emailEnabled = providers?['email'] is Map ? (providers!['email']['enabled'] as bool? ?? true) : true;
    return AppSheet(
      title: step == 'email' ? copy.t('sign_in') : copy.t('enter_code'),
      onClose: widget.session.closeLogin,
      heightFactor: 0.55,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: step == 'email' ? _emailStep(emailEnabled) : _codeStep(),
      ),
    );
  }

  Widget _emailStep(bool emailEnabled) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(copy.t('sign_in_hint'), style: const TextStyle(color: Colors.white54, fontSize: 13)),
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(copy.t('email_label'), style: const TextStyle(color: Colors.white70, fontSize: 13)),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: email,
          keyboardType: TextInputType.emailAddress,
          autofillHints: const [AutofillHints.email],
          style: const TextStyle(color: Colors.white),
          decoration: _inputDecoration(copy.t('email_placeholder')),
          onSubmitted: (_) => sendCode(),
        ),
        const SizedBox(height: 14),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
          onPressed: busy.isEmpty && emailEnabled ? sendCode : null,
          child: Text(busy == 'sending' ? copy.t('sending') : copy.t('send_code')),
        ),
        if (error != null) _errorText(),
        if (googleReady) ..._googleBlock(),
        const SizedBox(height: 12),
        Text(
          copy.t('terms'),
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white38, fontSize: 11),
        ),
      ],
    );
  }

  Widget _codeStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          copy.t('code_sent_to', {'email': _mask(email.text)}),
          style: const TextStyle(color: Colors.white54, fontSize: 13),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: code,
          autofocus: true,
          keyboardType: TextInputType.number,
          maxLength: _codeLength,
          autofillHints: const [AutofillHints.oneTimeCode],
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white, fontSize: 24, letterSpacing: 10, fontWeight: FontWeight.w700),
          decoration: _inputDecoration('· · · · · ·').copyWith(counterText: ''),
          onSubmitted: (_) => verify(),
        ),
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            cooldown > 0
                ? Text(copy.t('resend_in', {'seconds': cooldown}), style: const TextStyle(color: Colors.white38, fontSize: 12))
                : TextButton(
                    onPressed: busy.isEmpty ? sendCode : null,
                    child: Text(copy.t('resend'), style: const TextStyle(color: gold, fontSize: 13)),
                  ),
            TextButton(
              onPressed: () => setState(() {
                step = 'email';
                error = null;
              }),
              child: Text(copy.t('change_email'), style: const TextStyle(color: Colors.white54, fontSize: 13)),
            ),
          ],
        ),
        const SizedBox(height: 8),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: red, padding: const EdgeInsets.symmetric(vertical: 13)),
          onPressed: busy.isEmpty ? verify : null,
          child: Text(busy == 'verifying' ? copy.t('verifying') : copy.t('verify_sign_in')),
        ),
        if (error != null) _errorText(),
        const SizedBox(height: 8),
        Text(
          copy.t('code_expires'),
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white38, fontSize: 11),
        ),
      ],
    );
  }

  /// Divider plus the Google button, rendered only when the backend has an
  /// OAuth client configured.
  List<Widget> _googleBlock() {
    return [
      const SizedBox(height: 16),
      Row(
        children: [
          const Expanded(child: Divider(color: Colors.white12, height: 1)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(copy.t('or'), style: const TextStyle(color: Colors.white38, fontSize: 11)),
          ),
          const Expanded(child: Divider(color: Colors.white12, height: 1)),
        ],
      ),
      const SizedBox(height: 12),
      OutlinedButton(
        style: OutlinedButton.styleFrom(
          foregroundColor: Colors.white,
          side: const BorderSide(color: Colors.white24),
          padding: const EdgeInsets.symmetric(vertical: 12),
        ),
        onPressed: busy.isEmpty ? signInWithGoogle : null,
        child: Text(
          busy == 'google' ? copy.t('verifying') : copy.t('continue_google'),
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
        ),
      ),
    ];
  }

  Widget _errorText() {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Text(error!, textAlign: TextAlign.center, style: const TextStyle(color: Color(0xFFFF6B6E), fontSize: 12)),
    );
  }

  InputDecoration _inputDecoration(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: Colors.white38),
        filled: true,
        fillColor: const Color(0xFF241A1D),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
      );

  String _mask(String value) {
    final parts = value.split('@');
    if (parts.length != 2 || parts[0].isEmpty) return value;
    final name = parts[0];
    return '${name.substring(0, 1)}${'*' * (name.length > 1 ? (name.length - 1).clamp(1, 3) : 1)}@${parts[1]}';
  }
}
