interface FacebookAuthResponse {
  accessToken?: string;
}

interface FacebookLoginResponse {
  status: "connected" | "not_authorized" | "unknown";
  authResponse?: FacebookAuthResponse;
}

interface FacebookSdk {
  init(options: { appId: string; cookie?: boolean; xfbml?: boolean; version: string }): void;
  login(
    callback: (response: FacebookLoginResponse) => void,
    options?: { scope?: string },
  ): void;
}

declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

const SRC = "https://connect.facebook.net/en_US/sdk.js";
const VERSION = "v21.0";
let loading: Promise<void> | null = null;
let initializedFor: string | null = null;

function loadScript(): Promise<void> {
  if (window.FB) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const element = document.createElement("script");
    element.src = SRC;
    element.async = true;
    element.defer = true;
    element.crossOrigin = "anonymous";
    element.onload = () => resolve();
    element.onerror = () => {
      loading = null;
      reject(new Error("facebook script failed"));
    };
    document.head.appendChild(element);
    window.setTimeout(() => {
      if (!window.FB) {
        loading = null;
        reject(new Error("facebook script timed out"));
      }
    }, 12000);
  });
  return loading;
}

/**
 * Load the SDK and point it at this app.
 *
 * Called while the sheet is idle so the click handler can open the dialog
 * synchronously — `FB.login` after an `await` is treated as a non-gesture and
 * gets blocked by popup filters.
 */
export async function prepareFacebookLogin(appId: string): Promise<void> {
  await loadScript();
  const sdk = window.FB;
  if (!sdk) throw new Error("facebook unavailable");
  if (initializedFor !== appId) {
    sdk.init({ appId, cookie: true, xfbml: false, version: VERSION });
    initializedFor = appId;
  }
}

/** Resolves the access token, or null when the user closed the dialog. */
export function facebookAccessToken(): Promise<string | null> {
  const sdk = window.FB;
  if (!sdk) return Promise.reject(new Error("facebook unavailable"));
  return new Promise<string | null>((resolve, reject) => {
    try {
      sdk.login(
        (response) => {
          if (response.status === "connected" && response.authResponse?.accessToken) {
            resolve(response.authResponse.accessToken);
            return;
          }
          if (response.status === "not_authorized") {
            reject(new Error("facebook permission denied"));
            return;
          }
          resolve(null);
        },
        { scope: "email,public_profile" },
      );
    } catch (error) {
      reject(error);
    }
  });
}
