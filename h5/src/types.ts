export interface DramaCard {
  id: number;
  title: string;
  cover_url: string | null;
  position?: number;
  /** Lifetime real plays, bumped by the backend on every playback start. */
  play_count?: number;
}

export interface HomeGroup {
  id: number;
  name: string;
  position: number;
  style: string;
  badge: string;
  dramas: DramaCard[];
}

export interface Banner {
  id: number;
  drama_id: number | null;
  slot: string;
  title: string | null;
  cover_url: string | null;
  kicker: string;
  headline: string;
}

export interface HomePayload {
  groups: HomeGroup[];
  banners: Banner[];
}

export interface ChannelTab {
  code: string;
  title_en: string;
  title_zh: string;
  position: number;
  locked: boolean;
  group_id: number | null;
}

export interface ConfigPayload {
  tabs: ChannelTab[];
  copies: Record<string, string>;
}

export interface FeedDrama {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string | null;
  tags: string[];
  episode_count: number;
  published_episode_count: number;
  free_episode_count: number;
  coin_price_per_episode: number;
  play_count: number;
  like_count: number;
  liked: boolean;
}

export interface DramaLite {
  id: number;
  title: string;
  cover_url: string | null;
  episode_count: number;
}

export interface FavoritesPayload {
  total: number;
  items: Array<{
    id: number;
    title: string;
    cover_url: string | null;
    synopsis: string;
    play_count: number;
    episode_count: number;
  }>;
}

export interface HistoryItem {
  episode_id: number;
  position_seconds: number;
  episode_number: number;
  duration_seconds: number;
  updated_at: string;
  drama: DramaLite;
}

export interface HistoryPayload {
  items: HistoryItem[];
}

export interface LikesPayload {
  total: number;
  items: Array<{ target_type: string; target_id: number; drama?: DramaLite }>;
}

export interface LedgerEntry {
  amount: number;
  balance_after: number;
  biz_type: string;
  created_at: string;
}

export interface LedgerPayload {
  items: LedgerEntry[];
}

export interface InboxItem {
  id: number;
  kind: string;
  title: string;
  body: string;
  read: boolean;
}

export interface MessagesPayload {
  unread: number;
  items: InboxItem[];
}

export interface FeedEpisode {
  id: number;
  episode_number: number;
  title: string;
  duration_seconds: number;
  free: boolean;
}

export interface FeedItem {
  item_id: string;
  drama: FeedDrama;
  episode: FeedEpisode;
  resume_seconds: number;
  entry_reason: string;
  score: number;
}

export interface FeedPage {
  session_id: string;
  page_size: number;
  items: FeedItem[];
  next_cursor: string | null;
  has_more: boolean;
  preload: { fetch_when_remaining: number };
}

export interface PlayEpisode {
  id: number;
  episode_number: number;
  title: string;
  duration_seconds: number;
  free: boolean;
  locked: boolean;
  progress_seconds: number;
  unlock: { coin_price: number } | null;
}

export interface PlayPayload {
  drama: {
    id: number;
    title: string;
    synopsis: string;
    cover_url: string | null;
    tags: string[];
    free_episode_count: number;
    coin_price_per_episode: number;
    play_count: number;
    like_count: number;
    comment_count: number;
    liked: boolean;
    favorited: boolean;
  };
  items: PlayEpisode[];
  resume: { episode_id: number; position_seconds: number } | null;
  entitlement: { member: boolean; unlocked_episode_ids: number[] };
}

export interface PlaybackAuth {
  allowed: boolean;
  reason?: string;
  url?: string;
  expires_at?: number;
  renditions?: string[];
  episode_id?: number;
  unlock?: { coin_price: number; membership_available?: boolean };
}
