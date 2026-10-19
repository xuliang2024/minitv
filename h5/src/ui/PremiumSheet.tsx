import { useEffect, useState } from "react";
import {
  ApiError,
  cancelSubscription,
  getPlans,
  startCheckout,
  type MeProfile,
  type Plan,
} from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { Sheet } from "./Sheet";
import { toast } from "./toast";

type Membership = NonNullable<MeProfile["membership"]>;
type BillingMode = "subscription" | "one_time";

function formatDay(iso: string | null, locale: Locale): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * Subscription / pass upsell and account controls.
 *
 * The backend decides whether new purchases are auto-renewing or one-time
 * (global switch); this sheet only mirrors that. Prices come from /v1/plans.
 */
export function PremiumSheet({
  locale,
  membership,
  onChanged,
  onClose,
}: {
  locale: Locale;
  membership?: Membership;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [mode, setMode] = useState<BillingMode>("one_time");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [showPlans, setShowPlans] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const active = Boolean(membership?.active);
  const isPass = membership?.billing_mode === "one_time";
  const showActive = active && !showPlans;

  useEffect(() => {
    let alive = true;
    getPlans()
      .then((payload) => {
        if (!alive) return;
        setPlans(payload.items);
        setMode(payload.billing_mode ?? "subscription");
        setState("ready");
      })
      .catch(() => {
        if (alive) setState("error");
      });
    return () => {
      alive = false;
    };
  }, []);

  async function subscribe(code: string) {
    setBusy(code);
    setError("");
    try {
      const result = await startCheckout(code);
      window.location.assign(result.url);
    } catch (err) {
      setBusy(null);
      setError(
        err instanceof ApiError && err.status === 401
          ? t(locale, "premium_need_sign_in")
          : t(locale, "premium_failed"),
      );
    }
  }

  async function stopRenewing() {
    setBusy("cancel");
    setError("");
    try {
      await cancelSubscription();
      onChanged();
      toast(t(locale, "premium_cancel_done"));
      onClose();
    } catch {
      setBusy(null);
      setError(t(locale, "premium_cancel_failed"));
    }
  }

  const unitOf = (plan: Plan) =>
    mode === "one_time"
      ? t(locale, `premium_span_${plan.code}`)
      : t(locale, `premium_unit_${plan.code}`);

  return (
    <Sheet
      title={
        showActive
          ? isPass
            ? t(locale, "premium_title_pass")
            : t(locale, "premium_title_active")
          : t(locale, "premium_title")
      }
      onClose={onClose}
      testId="premium-sheet"
    >
      {showActive && membership ? (
        <>
          <div className={membership.canceling ? "premium-status canceling" : "premium-status"}>
            <span className="premium-status-crown">★</span>
            <div className="premium-status-main">
              <b>{t(locale, `premium_plan_${membership.plan_code ?? "monthly"}`)}</b>
              <i>
                {membership.canceling
                  ? t(locale, "premium_ends_on", { date: formatDay(membership.until, locale) })
                  : t(locale, "premium_active_until", { date: formatDay(membership.until, locale) })}
              </i>
            </div>
            <span className="premium-status-tag">
              {isPass ? t(locale, "premium_pass_badge") : t(locale, "premium_badge_active")}
            </span>
          </div>
          <ul className="premium-benefits status">
            <li>{t(locale, "premium_benefit_all")}</li>
            <li>{t(locale, "premium_benefit_noads")}</li>
            <li>{t(locale, "premium_benefit_hd")}</li>
          </ul>
          {mode === "one_time" && !isPass && (
            <button
              type="button"
              className="premium-secondary"
              onClick={() => setShowPlans(true)}
              data-testid="premium-buy-pass"
            >
              {t(locale, "premium_buy_pass_cta")}
            </button>
          )}
          {isPass ? (
            <>
              <button
                type="button"
                className="premium-cta"
                onClick={() => setShowPlans(true)}
                data-testid="premium-extend"
              >
                {t(locale, "premium_extend_cta")}
              </button>
              <p className="premium-note">{t(locale, "premium_extend_note")}</p>
            </>
          ) : membership.canceling ? (
            <p className="premium-note">{t(locale, "premium_canceled_note")}</p>
          ) : (
            <>
              <button
                type="button"
                className="premium-cancel"
                onClick={stopRenewing}
                disabled={busy !== null}
                data-testid="premium-cancel"
              >
                {busy === "cancel" ? t(locale, "premium_canceling") : t(locale, "premium_cancel_cta")}
              </button>
              <p className="premium-note">{t(locale, "premium_cancel_hint")}</p>
            </>
          )}
        </>
      ) : (
        <>
          <p className="premium-lede">
            {mode === "one_time" ? t(locale, "premium_lede_onetime") : t(locale, "premium_lede")}
          </p>
          {state === "loading" && <div className="me-sheet-loading">…</div>}
          {state === "error" && (
            <div className="me-empty small">
              <p>{t(locale, "err_generic")}</p>
            </div>
          )}
          <div className="premium-plans">
            {plans.map((plan) => (
              <div key={plan.code} className={plan.benefits?.best ? "premium-plan best" : "premium-plan"}>
                {plan.benefits?.best && <span className="premium-badge">{t(locale, "premium_popular")}</span>}
                <div className="premium-plan-head">
                  <b>{locale === "zh" ? plan.name_zh || plan.name_en : plan.name_en}</b>
                  <span className="premium-price">
                    ${(plan.price_cents / 100).toFixed(2)}
                    <i>/{unitOf(plan)}</i>
                  </span>
                </div>
                <ul className="premium-benefits">
                  {(plan.benefits?.items ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="premium-cta"
                  disabled={busy !== null}
                  onClick={() => subscribe(plan.code)}
                  data-testid={`premium-buy-${plan.code}`}
                >
                  {busy === plan.code
                    ? t(locale, "premium_redirecting")
                    : mode === "one_time"
                      ? t(locale, "premium_buy_cta")
                      : t(locale, "premium_cta")}
                </button>
              </div>
            ))}
          </div>
          {error && <p className="premium-error">{error}</p>}
          <p className="premium-note">
            {mode === "one_time" ? t(locale, "premium_note_onetime") : t(locale, "premium_note")}
          </p>
        </>
      )}
    </Sheet>
  );
}
