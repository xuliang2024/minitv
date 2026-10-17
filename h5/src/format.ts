import type { Locale } from "./copy";

export function formatCount(value: number, locale: Locale): string {
  const trim = (n: number) => {
    const text = n.toFixed(1);
    return text.endsWith(".0") ? text.slice(0, -2) : text;
  };
  if (locale === "zh") return value >= 10000 ? `${trim(value / 10000)}万` : String(value);
  if (value >= 1_000_000) return `${trim(value / 1_000_000)}M`;
  if (value >= 1000) return `${trim(value / 1000)}K`;
  return String(value);
}

export const RANK_COLORS = ["#ffb23e", "#b9c0cc", "#8a8f9c"];
