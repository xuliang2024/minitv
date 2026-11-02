import 'dart:io';

import 'package:google_sign_in/google_sign_in.dart';

/// iOS OAuth client created for the `app.minitv.minitv` bundle.
///
/// The same value lives in `ios/Runner/Info.plist` as `GIDClientID`, and its
/// reversed form is the URL scheme Google redirects back to — all three must
/// stay in sync. Android has no client id of its own in the token: it asks
/// Google for a token addressed to the web client via `serverClientId`
/// (`/v1/auth/providers` serves it), which is the audience the backend already
/// trusts for H5.
const googleIosClientId = String.fromEnvironment(
  'GOOGLE_IOS_CLIENT_ID',
  defaultValue: '1035146369409-h2b4d6m0narutc86r93d1ne2pvt21sm7.apps.googleusercontent.com',
);

/// Thin wrapper over `google_sign_in` 7.x, which uses `GoogleSignIn.instance`
/// plus `authenticate()` instead of the 6.x `signIn()` singleton.
class GoogleAuth {
  /// `initialize()` may only run once per process.
  static bool _initialized = false;

  /// Google sign-in needs a Play-services / native SDK build.
  static bool get supported => Platform.isIOS || Platform.isAndroid;

  /// Returns a Google ID token, or null when the user closed the sheet.
  ///
  /// Throws [GoogleSignInException] when Google refuses; callers treat
  /// `canceled` as a no-op and surface anything else as a failure.
  static Future<String?> idToken({required String serverClientId}) async {
    final signIn = GoogleSignIn.instance;
    if (!_initialized) {
      await signIn.initialize(
        // iOS mints its audience from its own client; Android must leave this
        // null so the token is addressed to [serverClientId] instead.
        clientId: Platform.isIOS ? googleIosClientId : null,
        serverClientId: serverClientId,
      );
      _initialized = true;
    }
    final account = await signIn.authenticate();
    return account.authentication.idToken;
  }
}
