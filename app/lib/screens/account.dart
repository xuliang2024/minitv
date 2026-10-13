import 'package:flutter/material.dart';
import '../api.dart';
import '../copy.dart';
import '../ui.dart';

class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key, required this.onEmail});
  final VoidCallback onEmail;
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(children: [
        const Text('MiniTV', style: TextStyle(color: red, fontSize: 32, fontWeight: FontWeight.w800)),
        const Spacer(),
        for (final name in ['Facebook', 'Google', 'TikTok'])
          Padding(padding: const EdgeInsets.only(bottom: 8), child: OutlinedButton(
            onPressed: () {},
            child: Text('$name is not configured', style: const TextStyle(color: Colors.white70)),
          )),
        FilledButton(style: FilledButton.styleFrom(backgroundColor: red), onPressed: onEmail, child: Text(copy.t('login'))),
      ]),
    );
  }
}

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, required this.onDone});
  final ValueChanged<String> onDone;
  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final email = TextEditingController();
  final code = TextEditingController();
  String? message;
  Future<void> send() async {
    try { await MiniApi().postJson('/v1/auth/email/code', {'email': email.text, 'purpose': 'register'}); message = 'sent'; }
    catch (error) { message = '$error'; }
    setState(() {});
  }
  Future<void> register() async {
    try {
      final result = await MiniApi().postJson('/v1/auth/email/register', {'email': email.text, 'code': code.text});
      widget.onDone(result['token'] as String);
    } catch (error) { setState(() => message = '$error'); }
  }
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    return Padding(padding: const EdgeInsets.all(16), child: Column(children: [
      TextField(controller: email, style: const TextStyle(color: Colors.white), decoration: const InputDecoration(hintText: 'Email')),
      TextField(controller: code, style: const TextStyle(color: Colors.white), decoration: const InputDecoration(hintText: 'Code')),
      TextButton(onPressed: send, child: Text(copy.t('code'), style: const TextStyle(color: gold))),
      FilledButton(onPressed: register, child: Text(copy.t('register'))),
      if (message != null) Text(message!, style: const TextStyle(color: Colors.white70)),
    ]));
  }
}

class RewardsScreen extends StatelessWidget {
  const RewardsScreen({super.key, required this.token});
  final String? token;
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    final api = MiniApi(token: token);
    return Padding(padding: const EdgeInsets.all(16), child: Column(children: [
      FilledButton(onPressed: token == null ? null : () { api.postJson('/v1/me/checkin', {}); }, child: Text(copy.t('checkin'))),
      FilledButton(onPressed: token == null ? null : () { api.postJson('/v1/me/ad-reward', {}); }, child: Text(copy.t('ad'))),
    ]));
  }
}

class MeScreen extends StatelessWidget {
  const MeScreen({super.key, required this.onOpen});
  final void Function(String route) onOpen;
  @override
  Widget build(BuildContext context) {
    final copy = Copy(Localizations.localeOf(context).languageCode);
    return ListView(padding: const EdgeInsets.all(16), children: [
      for (final item in ['coins', 'membership', 'messages', 'history', 'favorites', 'likes', 'mylist'])
        ListTile(title: Text(copy.t(item), style: const TextStyle(color: Colors.white)), onTap: () => onOpen(item)),
    ]);
  }
}

class SimplePage extends StatelessWidget {
  const SimplePage({super.key, required this.title});
  final String title;
  @override
  Widget build(BuildContext context) => Center(child: Text(title, style: const TextStyle(color: Colors.white)));
}
