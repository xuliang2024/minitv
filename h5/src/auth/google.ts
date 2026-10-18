interface GoogleCredentialResponse {
  credential?: string;
}

interface GoogleIdApi {
  initialize(config: {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
    ux_mode?: "popup" | "redirect";
    auto_select?: boolean;
  }): void;
  renderButton(element: HTMLElement, options: Record<string, unknown>): void;
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleIdApi } };
  }
}

const SRC = "https://accounts.google.com/gsi/client";
let loading: Promise<void> | null = null;

export function loadGoogleScript(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (loading) return loading;

  loading = new Promise<void>((resolve, reject) => {
    const element = document.createElement("script");
    element.src = SRC;
    element.async = true;
    element.defer = true;
    element.onload = () => resolve();
    element.onerror = () => {
      loading = null;
      reject(new Error("google script failed"));
    };
    document.head.appendChild(element);
    window.setTimeout(() => {
      if (!window.google?.accounts?.id) {
        loading = null;
        reject(new Error("google script timed out"));
      }
    }, 12000);
  });
  return loading;
}

/** Render the official button; the credential goes straight to our API. */
export async function renderGoogleButton(
  host: HTMLElement,
  clientId: string,
  onCredential: (credential: string) => void,
): Promise<void> {
  await loadGoogleScript();
  const api = window.google?.accounts?.id;
  if (!api) throw new Error("google unavailable");
  api.initialize({
    client_id: clientId,
    ux_mode: "popup",
    auto_select: false,
    callback: (response) => {
      if (response.credential) onCredential(response.credential);
    },
  });
  host.innerHTML = "";
  api.renderButton(host, {
    theme: "filled_white",
    size: "large",
    shape: "pill",
    text: "continue_with",
    logo_alignment: "left",
    width: 268,
  });
}
