/** Tiny hash router: #preview / #watch/<id> are pages, everything else is a
 * homepage section (so app-download CTAs land on the #download card). */

export type Route =
  | { kind: "home"; section?: string }
  | { kind: "preview" }
  | { kind: "watch"; dramaId: number };

export const HOME_SECTIONS = ["home", "shows", "premium", "about", "download"];

export function parseRoute(hash: string = window.location.hash): Route {
  const raw = hash.replace(/^#/, "");
  if (raw.startsWith("watch/")) {
    const dramaId = Number.parseInt(raw.slice("watch/".length), 10);
    return Number.isFinite(dramaId) && dramaId > 0 ? { kind: "watch", dramaId } : { kind: "preview" };
  }
  if (raw === "preview") return { kind: "preview" };
  if (HOME_SECTIONS.includes(raw)) return { kind: "home", section: raw };
  return { kind: "home" };
}

/**
 * Scroll to the section the current hash points at (or the top of the page).
 * In-page anchors keep the site's smooth scrolling; page switches (and
 * post-load re-anchoring) jump instantly so a route never lands mid-scroll.
 */
export function scrollToRoute(route: Route, { instant = false }: { instant?: boolean } = {}) {
  const root = document.documentElement;
  const previous = root.style.scrollBehavior;
  if (instant) root.style.scrollBehavior = "auto";
  try {
    if (route.kind === "home" && route.section && route.section !== "home") {
      const target = document.getElementById(route.section);
      if (target) {
        target.scrollIntoView({ block: "start" });
        return;
      }
    }
    window.scrollTo({ top: 0 });
  } finally {
    if (instant) root.style.scrollBehavior = previous;
  }
}
