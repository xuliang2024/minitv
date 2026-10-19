import { useEffect, useState } from "react";
import { ApiError, unlockEpisode, type UnlockResult } from "../api";
import { t, type Locale } from "../copy";
import type { PlayEpisode } from "../types";
import { Sheet } from "./Sheet";

type Failure = { kind: "insufficient"; short: number } | { kind: "failed" };

/**
 * One-shot confirmation for a coin unlock.
 *
 * The request is the only place that can decide `already_unlocked` or
 * `subscription`, so every outcome is handed back to the page instead of being
 * guessed here. Retrying reuses the caller's idempotency key, which is what
 * makes a timeout safe to repeat.
 */
export function UnlockSheet({
  locale,
  episode,
  price,
  balance,
  idempotencyKey,
  onClose,
  onDone,
  onNeedCoins,
  onInsufficient,
}: {
  locale: Locale;
  episode: PlayEpisode;
  price: number;
  balance: number;
  idempotencyKey: string;
  onClose: () => void;
  onDone: (result: UnlockResult) => void;
  onNeedCoins: () => void;
  onInsufficient?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  // The server's `have` is authoritative: a 402 can arrive because the balance
  // moved in another tab, so the sheet re-bases on it instead of showing stale coins.
  const [liveBalance, setLiveBalance] = useState(balance);

  useEffect(() => setLiveBalance(balance), [balance]);

  const after = liveBalance - price;
  const insufficient = failure?.kind === "insufficient" || after < 0;

  const confirm = async () => {
    if (busy || insufficient) return;
    setBusy(true);
    setFailure(null);
    try {
      onDone(await unlockEpisode(episode.id, idempotencyKey));
    } catch (error) {
      if (error instanceof ApiError && error.status === 402) {
        const detail = (error.data as { detail?: { need?: number; have?: number } } | null)?.detail;
        const need = detail?.need ?? price;
        const have = detail?.have ?? liveBalance;
        setLiveBalance(have);
        setFailure({ kind: "insufficient", short: Math.max(0, need - have) });
        onInsufficient?.();
      } else if (error instanceof ApiError && error.status === 401) {
        // The session died: close and let the auth layer re-open the login sheet.
        onClose();
      } else {
        setFailure({ kind: "failed" });
      }
    } finally {
      setBusy(false);
    }
  };

  const label = busy
    ? t(locale, "unlock_confirming")
    : failure?.kind === "failed"
      ? t(locale, "unlock_retry")
      : t(locale, "unlock_confirm", { price });

  return (
    <Sheet
      title={t(locale, "unlock_sheet_title", { number: episode.episode_number })}
      onClose={onClose}
      className="unlock-sheet"
      testId="unlock-sheet"
    >
      <div className="unlock-rows">
        <div className="unlock-row">
          <span>{t(locale, "unlock_row_price")}</span>
          <b className="gold">{t(locale, "unlock_price", { price })}</b>
        </div>
        <div className="unlock-row">
          <span>{t(locale, "unlock_row_balance")}</span>
          <b>{t(locale, "unlock_price", { price: liveBalance })}</b>
        </div>
        <div className="unlock-row">
          <span>{t(locale, "unlock_row_after")}</span>
          <b className={after < 0 ? "red" : "gold"}>
            {t(locale, "unlock_price", { price: Math.max(0, after) })}
          </b>
        </div>
      </div>

      {failure && (
        <p className="unlock-error" role="status">
          {failure.kind === "insufficient"
            ? t(locale, "unlock_insufficient", { short: failure.short })
            : t(locale, "unlock_failed")}
        </p>
      )}

      <p className="unlock-note">{t(locale, "unlock_note")}</p>

      {insufficient ? (
        <button
          type="button"
          className="unlock-primary"
          onClick={onNeedCoins}
          data-testid="unlock-need-coins"
        >
          {t(locale, "unlock_need_coins")}
        </button>
      ) : (
        <button
          type="button"
          className="unlock-primary"
          onClick={confirm}
          disabled={busy}
          data-testid="unlock-confirm"
        >
          {label}
        </button>
      )}
      <button type="button" className="unlock-secondary" onClick={onClose} disabled={busy}>
        {t(locale, "close")}
      </button>
    </Sheet>
  );
}
