import { useEffect, useState } from "react";

let push: ((message: string) => void) | null = null;

/** Fire-and-forget message; the host lives once at the app root. */
export function toast(message: string) {
  push?.(message);
}

export function ToastHost() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    push = setMessage;
    return () => {
      push = null;
    };
  }, []);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 2600);
    return () => window.clearTimeout(timer);
  }, [message]);

  if (!message) return null;
  return (
    <div className="toast" role="status" data-testid="toast">
      {message}
    </div>
  );
}
