import { useEffect, useState } from "react";
import { message } from "antd";
import { api, errorText } from "./api";

/**
 * Minimal "load one endpoint into state" hook.
 *
 * `StagePages.tsx` grew its own copy first; new pages import this one instead
 * so a third copy never appears. Reload after a mutation by calling `reload()`.
 */
export function useLoad<T>(
  path: string,
  fallback: T,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T>(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    return api<T>(path)
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch((e) => {
        const text = errorText(e);
        setError(text);
        message.error(text);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    reload();
    // The caller owns `path`; changing it is the only reload trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { data, reload, loading, error };
}

/** ISO-8601 from the API -> local "YYYY-MM-DD HH:mm" for table cells. */
export function formatTime(value?: string | null): string {
  if (!value) return "-";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(
    moment.getHours()
  )}:${pad(moment.getMinutes())}`;
}
