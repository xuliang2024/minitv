import { useEffect, useState } from "react";
import { ApiError, getPlans, startCheckout, type Plan } from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { Sheet } from "./Sheet";

/**
 * Subscription upsell. Prices come from /v1/plans (backend owns them), and the
 * actual payment happens on Stripe Checkout — we only forward the browser.
 */
export function PremiumSheet({ locale, onClose }: { locale: Locale; onClose: () => void }) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getPlans()
      .then((payload) => {
        if (!alive) return;
        setPlans(payload.items);
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

  return (
    <Sheet title={t(locale, "premium_title")} onClose={onClose} testId="premium-sheet">
      <p className="premium-lede">{t(locale, "premium_lede")}</p>
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
                <i>/{t(locale, `premium_unit_${plan.code}`)}</i>
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
              {busy === plan.code ? t(locale, "premium_redirecting") : t(locale, "premium_cta")}
            </button>
          </div>
        ))}
      </div>
      {error && <p className="premium-error">{error}</p>}
      <p className="premium-note">{t(locale, "premium_note")}</p>
    </Sheet>
  );
}
