import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  authenticateEmail,
  getAuthProviders,
  loginWithFacebook,
  loginWithGoogle,
  sendEmailCode,
  type AuthProviders,
} from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { IconClose } from "../icons";
import { useAuth } from "./AuthProvider";
import { facebookAccessToken, prepareFacebookLogin } from "./facebook";
import { renderGoogleButton } from "./google";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CODE_LENGTH = 6;
const RESEND_SECONDS = 60;

function maskEmail(value: string): string {
  const [name, domain] = value.split("@");
  if (!domain || !name) return value;
  return `${name.slice(0, 1)}${"*".repeat(Math.min(3, Math.max(1, name.length - 1)))}@${domain}`;
}

function messageFor(error: unknown, locale: Locale): string {
  if (error instanceof ApiError) {
    switch (error.message) {
      case "invalid code":
        return t(locale, "err_invalid_code");
      case "code expired":
        return t(locale, "err_code_expired");
      case "too many codes":
        return t(locale, "err_rate_limited");
      case "email delivery failed":
        return t(locale, "err_mail_failed");
      case "network":
        return t(locale, "err_network");
    }
    // Every rejection the Facebook verifier produces starts with "facebook";
    // to the viewer it just means the button is not usable right now.
    if (error.message.includes("facebook")) return t(locale, "err_facebook");
    if (error.status === 0) return t(locale, "err_network");
  }
  return t(locale, "err_generic");
}

/** Mounted only while open, so every visit starts from a clean form. */
export function LoginSheet({ locale }: { locale: Locale }) {
  const { loginOpen, closeLogin, completeLogin } = useAuth();
  if (!loginOpen) return null;
  return <LoginForm locale={locale} onClose={closeLogin} onToken={completeLogin} />;
}

function LoginForm({
  locale,
  onClose,
  onToken,
}: {
  locale: Locale;
  onClose: () => void;
  onToken: (token: string) => Promise<void>;
}) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [digits, setDigits] = useState<string[]>(() => Array.from({ length: CODE_LENGTH }, () => ""));
  const [busy, setBusy] = useState<"idle" | "sending" | "verifying" | "google" | "facebook">("idle");
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [googleFailed, setGoogleFailed] = useState(false);
  const [facebookFailed, setFacebookFailed] = useState(false);
  const googleHost = useRef<HTMLDivElement | null>(null);
  const boxes = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    let alive = true;
    getAuthProviders()
      .then((data) => {
        if (alive) setProviders(data);
      })
      .catch(() => {
        if (alive) {
          setProviders({
            email: { enabled: true },
            google: { enabled: false, client_id: null },
            facebook: { enabled: false, app_id: null },
          });
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const googleReady = Boolean(providers?.google.enabled && providers.google.client_id && !googleFailed);

  const facebookAppId = providers?.facebook?.enabled ? providers.facebook.app_id : null;
  const facebookVisible = Boolean(facebookAppId && !facebookFailed);
  const [facebookSdkReady, setFacebookSdkReady] = useState(false);

  // The SDK is loaded and initialised while the sheet sits idle: `FB.login`
  // has to run inside the click itself or the popup is blocked.
  useEffect(() => {
    if (!facebookAppId) return;
    let alive = true;
    prepareFacebookLogin(facebookAppId)
      .then(() => {
        if (alive) setFacebookSdkReady(true);
      })
      .catch(() => {
        if (alive) setFacebookFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [facebookAppId]);

  const signInWithFacebook = () => {
    setBusy("facebook");
    setError(null);
    facebookAccessToken()
      .then((accessToken) => {
        if (!accessToken) return undefined; // Dialog dismissed.
        return loginWithFacebook(accessToken).then((session) => onToken(session.token));
      })
      .catch((err) => {
        setError(err instanceof ApiError ? messageFor(err, locale) : t(locale, "err_facebook"));
      })
      .finally(() => setBusy("idle"));
  };

  useEffect(() => {
    if (!googleReady || step !== "email" || !googleHost.current || !providers?.google.client_id) return;
    let alive = true;
    renderGoogleButton(googleHost.current, providers.google.client_id, (credential) => {
      setBusy("google");
      setError(null);
      loginWithGoogle(credential)
        .then((session) => onToken(session.token))
        .catch((err) => setError(messageFor(err, locale)))
        .finally(() => setBusy("idle"));
    }).catch(() => {
      if (alive) setGoogleFailed(true);
    });
    return () => {
      alive = false;
    };
  }, [googleReady, step, providers, locale, onToken]);

  const focusBox = (index: number) => boxes.current[index]?.focus();

  const setDigit = (index: number, raw: string) => {
    const clean = raw.replace(/\D/g, "");
    if (clean.length === 0) {
      setDigits((prev) => prev.map((value, i) => (i === index ? "" : value)));
      return;
    }
    setDigits((prev) => {
      const next = [...prev];
      for (let offset = 0; offset < clean.length && index + offset < CODE_LENGTH; offset += 1) {
        next[index + offset] = clean[offset];
      }
      return next;
    });
    focusBox(Math.min(index + clean.length, CODE_LENGTH - 1));
  };

  const sendCode = () => {
    const value = email.trim().toLowerCase();
    if (!EMAIL_RE.test(value)) {
      setError(t(locale, "err_email_invalid"));
      return;
    }
    setBusy("sending");
    setError(null);
    sendEmailCode(value)
      .then(() => {
        setEmail(value);
        setDigits(Array.from({ length: CODE_LENGTH }, () => ""));
        setStep("code");
        setCooldown(RESEND_SECONDS);
        window.setTimeout(() => focusBox(0), 60);
      })
      .catch((err) => setError(messageFor(err, locale)))
      .finally(() => setBusy("idle"));
  };

  const verify = () => {
    const code = digits.join("");
    if (code.length !== CODE_LENGTH) {
      setError(t(locale, "err_invalid_code"));
      return;
    }
    setBusy("verifying");
    setError(null);
    authenticateEmail(email, code)
      .then((session) => onToken(session.token))
      .catch((err) => setError(messageFor(err, locale)))
      .finally(() => setBusy("idle"));
  };

  const resend = () => {
    if (cooldown > 0) return;
    setBusy("sending");
    setError(null);
    sendEmailCode(email)
      .then(() => setCooldown(RESEND_SECONDS))
      .catch((err) => setError(messageFor(err, locale)))
      .finally(() => setBusy("idle"));
  };

  const disabled = busy !== "idle";

  return (
    <>
      <button className="sheet-mask auth-mask" onClick={onClose} aria-label={t(locale, "close")} />
      <div className="sheet auth-sheet" role="dialog" aria-modal="true" aria-label={t(locale, "sign_in")} data-testid="login-sheet">
        <div className="sheet-handle" />
        <header className="auth-head">
          <h3>{step === "email" ? t(locale, "sign_in") : t(locale, "enter_code")}</h3>
          <button type="button" className="close-btn" onClick={onClose} aria-label={t(locale, "close")}>
            <IconClose size={18} />
          </button>
        </header>

        <div className="auth-body">
          {step === "email" ? (
            <>
              <p className="auth-hint">{t(locale, "sign_in_hint")}</p>
              <label className="auth-label" htmlFor="auth-email">{t(locale, "email_label")}</label>
              <input
                id="auth-email"
                className="auth-input"
                type="email"
                inputMode="email"
                autoComplete="email"
                placeholder={t(locale, "email_placeholder")}
                value={email}
                disabled={disabled}
                onChange={(event) => setEmail(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") sendCode();
                }}
              />
              <button
                type="button"
                className="auth-primary"
                onClick={sendCode}
                disabled={disabled || !providers?.email.enabled}
              >
                {busy === "sending" ? t(locale, "sending") : t(locale, "send_code")}
              </button>

              {(googleReady || facebookVisible) && (
                <>
                  <div className="auth-divider"><span>{t(locale, "or")}</span></div>
                  <div className="auth-oauth">
                    {facebookVisible && (
                      <button
                        type="button"
                        className="auth-fb"
                        onClick={signInWithFacebook}
                        disabled={disabled || !facebookSdkReady}
                        data-testid="facebook-button"
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                          <path
                            d="M13.5 21v-7h2.4l.4-2.8h-2.8V9.4c0-.8.2-1.4 1.4-1.4h1.5V5.5c-.3 0-1.2-.1-2.2-.1-2.2 0-3.7 1.3-3.7 3.8v2H8.1V14h2.4v7h3z"
                            fill="currentColor"
                          />
                        </svg>
                        {busy === "facebook" ? t(locale, "verifying") : t(locale, "continue_facebook")}
                      </button>
                    )}
                    {googleReady && (
                      <div className="auth-google" ref={googleHost} data-testid="google-slot" />
                    )}
                  </div>
                  {busy === "google" && <p className="auth-hint center-text">{t(locale, "verifying")}</p>}
                </>
              )}
            </>
          ) : (
            <>
              <p className="auth-hint">{t(locale, "code_sent_to", { email: maskEmail(email) })}</p>
              <div className="auth-code">
                {digits.map((digit, index) => (
                  <input
                    key={index}
                    ref={(node) => {
                      boxes.current[index] = node;
                    }}
                    className="auth-box"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1}
                    aria-label={`${t(locale, "enter_code")} ${index + 1}`}
                    value={digit}
                    disabled={disabled}
                    onChange={(event) => setDigit(index, event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Backspace" && digits[index] === "" && index > 0) {
                        focusBox(index - 1);
                        setDigit(index - 1, "");
                      }
                      if (event.key === "Enter") verify();
                    }}
                  />
                ))}
              </div>
              <div className="auth-row">
                {cooldown > 0 ? (
                  <span className="auth-muted">{t(locale, "resend_in", { seconds: cooldown })}</span>
                ) : (
                  <button type="button" className="auth-link" onClick={resend} disabled={disabled}>
                    {t(locale, "resend")}
                  </button>
                )}
                <button type="button" className="auth-link" onClick={() => { setStep("email"); setError(null); }}>
                  {t(locale, "change_email")}
                </button>
              </div>
              <button type="button" className="auth-primary" onClick={verify} disabled={disabled || digits.join("").length !== CODE_LENGTH}>
                {busy === "verifying" ? t(locale, "verifying") : t(locale, "verify_sign_in")}
              </button>
              <p className="auth-muted center-text auth-foot">{t(locale, "code_expires")}</p>
            </>
          )}

          {error && <p className="auth-error" role="alert">{error}</p>}
          <p className="auth-terms">{t(locale, "terms")}</p>
        </div>
      </div>
    </>
  );
}
