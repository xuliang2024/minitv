/**
 * Bunny Optimizer thumbnail helpers.
 *
 * Covers live in the Bunny Storage zone behind the image pull zone. With
 * Optimizer enabled, appending width/quality makes the edge produce (once) and
 * cache a smaller WebP variant instead of shipping the original poster.
 *
 * Only the image pull zone understands these params, so every other host —
 * Google avatars, marketing-site assets — is returned untouched. Signed URLs
 * (subtitles) must never go through here.
 */
const IMAGE_CDN_HOST = "minitv-dev-img.b-cdn.net";

/** Display widths, in CSS px × DPR headroom, matching the layout in styles.css. */
export const IMG = {
  /** Hero carousel: full 480px container, 2× DPR. */
  banner: 960,
  /** Poster grid (3 columns) and "my list": ~144px card, 3× DPR. */
  card: 480,
  /** Landscape grid (2 columns): ~221px card, 3× DPR. */
  landscape: 720,
  /** Rank list thumb: 52×68 box, 3× DPR. */
  rank: 180,
  /** Feed rail avatar: 44×44 box, 3× DPR. */
  avatar: 144,
} as const;

export function thumb(url: string | null | undefined, width: number, quality = 75): string {
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) return url;

  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }

  if (parsed.hostname !== IMAGE_CDN_HOST) return url;
  if (parsed.searchParams.has("width") || parsed.searchParams.has("height")) return url;

  parsed.searchParams.set("width", String(Math.round(width)));
  parsed.searchParams.set("quality", String(quality));
  return parsed.href;
}
