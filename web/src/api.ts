/** Typed fetchers for the public MiniTV API. */

export type Drama = { id: number; title: string; cover_url: string; play_count: number };

export type Plan = {
  code: string;
  name_en: string;
  price_cents: number;
  benefits?: { items?: string[]; best?: boolean };
};

export type Marketing = {
  stats: { label: string; value: string }[];
  testimonials: { author: string; body: string }[];
  stores: Record<string, string>;
  trending: Drama[];
  plans: Plan[];
};

/** One previewable drama: published, with at least one free published episode. */
export type PreviewDrama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  play_count: number;
  free_episode_count: number;
  coin_price_per_episode: number;
  /** Published episodes visible on the web. */
  episode_count: number;
  /** Published episodes inside the free window. */
  free_playable_count: number;
  tags: string[];
};

export type PreviewCategory = {
  id: number;
  name: string;
  main: boolean;
  /** Previewable dramas in this category, before the per-category cap. */
  total: number;
  dramas: PreviewDrama[];
};

export type PreviewCatalog = { limit: number; categories: PreviewCategory[] };

export type PreviewEpisode = {
  id: number;
  episode_number: number;
  title: string;
  duration_seconds: number;
  free: boolean;
};

export type PreviewDramaDetail = {
  drama: PreviewDrama;
  items: PreviewEpisode[];
  /** Episodes that exist (any status) - more than `items` means the app has more. */
  total_episode_count: number;
};

export type PlaybackAuth = {
  allowed: boolean;
  url?: string;
  expires_at?: number;
  reason?: string;
  episode_id?: number;
  episode_number?: number;
  duration_seconds?: number;
  /**
   * Server-side hint of the renditions an episode is transcoded to. The
   * quality menu itself is driven by the real HLS manifest (hls.levels),
   * which is the only list that matches what can actually be played.
   */
  renditions?: string[];
};

/** Production builds set VITE_API_BASE; local Vite dev leaves it empty and uses the proxy. */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(apiUrl(url));
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

export function fetchMarketing(): Promise<Marketing> {
  return getJson<Marketing>("/v1/marketing");
}

export function fetchPreviewCatalog(): Promise<PreviewCatalog> {
  return getJson<PreviewCatalog>("/v1/preview");
}

export function fetchPreviewDrama(dramaId: number): Promise<PreviewDramaDetail> {
  return getJson<PreviewDramaDetail>(`/v1/preview/dramas/${dramaId}`);
}

/**
 * Authorize playback for one episode. Only free episodes come back with a
 * signed URL for guests; locked episodes answer `allowed: false`. The website
 * never asks for locked episodes - the lock is enforced again server-side.
 */
export async function authorizePlayback(dramaId: number, episodeId: number): Promise<PlaybackAuth> {
  const res = await fetch(apiUrl("/v1/playback"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ drama_id: dramaId, episode_id: episodeId }),
  });
  if (res.status === 404) return { allowed: false, reason: "not_available" };
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as PlaybackAuth;
}
