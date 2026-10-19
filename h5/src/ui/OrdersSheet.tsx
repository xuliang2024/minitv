import { useEffect, useState } from "react";
import { getOrders, type OrderItem } from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { Sheet } from "./Sheet";

const STATUS_KEY: Record<string, string> = {
  paid: "orders_paid",
  created: "orders_pending",
  failed: "orders_failed",
  refunded: "orders_refunded",
};

function formatWhen(iso: string, locale: Locale): string {
  const date = new Date(iso);
  return date.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Purchase history: every Checkout the user started, with its final state. */
export function OrdersSheet({ locale, onClose }: { locale: Locale; onClose: () => void }) {
  const [items, setItems] = useState<OrderItem[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    getOrders()
      .then((payload) => {
        if (alive) setItems(payload.items);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Sheet title={t(locale, "orders_title")} onClose={onClose} testId="orders-sheet">
      {error ? (
        <div className="me-empty small">
          <p>{t(locale, "err_generic")}</p>
        </div>
      ) : items === null ? (
        <div className="me-sheet-loading">…</div>
      ) : items.length === 0 ? (
        <div className="me-empty small">
          <p>{t(locale, "orders_empty")}</p>
        </div>
      ) : (
        <ul className="orders-list">
          {items.map((order) => (
            <li key={order.order_no} className="orders-row">
              <div className="orders-main">
                <b>{locale === "zh" ? order.plan_name_zh || order.plan_name_en : order.plan_name_en}</b>
                <i>
                  {formatWhen(order.paid_at ?? order.created_at, locale)} ·{" "}
                  {order.billing_mode === "one_time" ? t(locale, "orders_pass") : t(locale, "orders_sub")}
                </i>
              </div>
              <div className="orders-side">
                <b>${(order.amount_cents / 100).toFixed(2)}</b>
                <span className={`orders-status ${order.status}`}>
                  {t(locale, STATUS_KEY[order.status] ?? "orders_pending")}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="premium-note">{t(locale, "orders_note")}</p>
    </Sheet>
  );
}
