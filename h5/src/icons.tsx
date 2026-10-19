export function IconHome({ active }: { active?: boolean }) {
  if (active) {
    return (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M11.23 2.37a1.2 1.2 0 0 1 1.54 0l7.8 6.06c.4.31.63.78.63 1.28V19.5a2 2 0 0 1-2 2h-3.7a1 1 0 0 1-1-1v-5a.5.5 0 0 0-.5-.5h-3.4a.5.5 0 0 0-.5.5v5a1 1 0 0 1-1 1H4.5a2 2 0 0 1-2-2V9.71c0-.5.23-.97.63-1.28l7.8-6.06a.6.6 0 0 1 .3-.1z" />
      </svg>
    );
  }
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 10.2L12 3l9 7.2V20a1.5 1.5 0 0 1-1.5 1.5H4.5A1.5 1.5 0 0 1 3 20v-9.8z" />
      <path d="M9.5 21.5V12.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v9" />
    </svg>
  );
}

export function IconForYou({ active }: { active?: boolean }) {
  if (active) {
    return (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        {/* 后层叠放卡片 */}
        <path
          d="M19 7.2c1.1.4 1.8 1.4 1.8 2.6v7.7c0 1.9-1.5 3.5-3.5 3.5H8.6c-1.2 0-2.2-.7-2.6-1.8"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          opacity="0.65"
        />
        {/* 前层主播放卡片 */}
        <rect x="3.2" y="3.5" width="14.6" height="15.8" rx="3.4" fill="currentColor" />
        {/* 纯白播放三角 */}
        <polygon points="9 8.5 13.8 11.4 9 14.3" fill="#ffffff" />
      </svg>
    );
  }
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {/* 前层主播放卡片 */}
      <rect x="3.5" y="4" width="13.8" height="15" rx="3" />
      {/* 后层叠放边缘 */}
      <path d="M19 7.5v9a2.5 2.5 0 0 1-2.5 2.5H8.5" />
      {/* 中间播放三角 */}
      <polygon points="9.2 9 13.5 11.5 9.2 14" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconRewards({ active }: { active?: boolean }) {
  if (active) {
    return (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        {/* 顶部丝带结 */}
        <path d="M12 7a2.6 2.6 0 1 0-2.6-2.6c1.6 0 2.6 2.6 2.6 2.6z" fill="currentColor" />
        <path d="M12 7a2.6 2.6 0 1 1 2.6-2.6c-1.6 0-2.6 2.6-2.6 2.6z" fill="currentColor" />
        {/* 礼盒盖 */}
        <rect x="3.2" y="7.5" width="17.6" height="3.8" rx="1.4" fill="currentColor" />
        {/* 礼盒身 */}
        <path d="M4.6 12.2c0-.6.4-1 1-1h12.8c.6 0 1 .4 1 1v6.8a2 2 0 0 1-2 2H6.6a2 2 0 0 1-2-2v-6.8z" fill="currentColor" />
        {/* 垂直高光丝带 */}
        <rect x="10.8" y="7.5" width="2.4" height="13.5" fill="#ffffff" opacity="0.9" />
        {/* 金色/高亮小星光 */}
        <circle cx="18.5" cy="5.5" r="1.5" fill="var(--gold, #F5B942)" />
      </svg>
    );
  }
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {/* 礼盒身 */}
      <rect x="4" y="8.5" width="16" height="12" rx="2" />
      {/* 盒盖横线 */}
      <path d="M3 8.5h18" />
      {/* 中间垂直丝带 */}
      <path d="M12 8.5v12" />
      {/* 顶部蝴蝶结左右两瓣 */}
      <path d="M12 8.5c0-1.8-1.4-3.2-3.2-3.2-1.3 0-2.1 1.2-1.5 2.3.5.9 2.5 1 4.7.9z" />
      <path d="M12 8.5c0-1.8 1.4-3.2 3.2-3.2 1.3 0 2.1 1.2 1.5 2.3-.5.9-2.5 1-4.7.9z" />
    </svg>
  );
}

export function IconMe({ active }: { active?: boolean }) {
  if (active) {
    return (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <circle cx="12" cy="7.2" r="4.2" />
        <path d="M4 19.8c0-3.6 3.6-6.4 8-6.4s8 2.8 8 6.4a1.2 1.2 0 0 1-1.2 1.2H5.2A1.2 1.2 0 0 1 4 19.8z" />
      </svg>
    );
  }
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="7.5" r="4" />
      <path d="M4.5 19.8c0-3.4 3.4-6.2 7.5-6.2s7.5 2.8 7.5 6.2" />
    </svg>
  );
}

export function IconSearch() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" />
    </svg>
  );
}

export function IconPlayCenter({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style={{ transform: "translateX(2px)" }}>
      <path d="M7 4.88a1.2 1.2 0 0 1 1.83-1.02l11.4 6.72a1.2 1.2 0 0 1 0 2.04l-11.4 6.72A1.2 1.2 0 0 1 7 18.32V4.88z" />
    </svg>
  );
}

export function IconHeart({ active, size = 30 }: { active?: boolean; size?: number }) {
  if (active) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="#E53B3E" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 8px rgba(229,59,62,0.65))" }}>
        <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="rgba(0,0,0,0.2)" stroke="#ffffff" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 5px rgba(0,0,0,0.85))" }}>
      <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" />
    </svg>
  );
}

export function IconComment({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="rgba(0,0,0,0.2)" stroke="#ffffff" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 5px rgba(0,0,0,0.85))" }}>
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8.5z" />
      <circle cx="8.5" cy="11.5" r="1.1" fill="#ffffff" stroke="none" />
      <circle cx="12" cy="11.5" r="1.1" fill="#ffffff" stroke="none" />
      <circle cx="15.5" cy="11.5" r="1.1" fill="#ffffff" stroke="none" />
    </svg>
  );
}

export function IconStar({ active, size = 30 }: { active?: boolean; size?: number }) {
  if (active) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="#F5B942" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 8px rgba(245,185,66,0.65))" }}>
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="rgba(0,0,0,0.2)" stroke="#ffffff" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 5px rgba(0,0,0,0.85))" }}>
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

export function IconShare({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ filter: "drop-shadow(0 2px 5px rgba(0,0,0,0.85))" }}>
      <path d="M4 12v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" />
      <polyline points="16 6 12 2 8 6" />
      <line x1="12" y1="2" x2="12" y2="15" />
    </svg>
  );
}

export function IconBack({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

export function IconEpisodes({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="3" y1="18" x2="15" y2="18" />
    </svg>
  );
}

export function IconChevronUp({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="18 15 12 9 6 15" />
    </svg>
  );
}

export function IconChevronRight({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

export function IconFire({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 23c4.97 0 9-3.8 9-8.5 0-3.32-2.12-6.22-3.82-7.85-.62-.59-1.63-.19-1.7.67-.14 1.83-1.03 3.68-2.48 4.68-.2.14-.48.06-.57-.16-.62-1.46-1.57-4.13-1.43-7.84.03-.78-.71-1.37-1.45-1.12C5.9 4.2 3 8.87 3 14.5 3 19.2 7.03 23 12 23z" />
    </svg>
  );
}

export function IconCrown({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M2.5 19h19a1 1 0 0 1 1 1v1.5a.5.5 0 0 1-.5.5h-20a.5.5 0 0 1-.5-.5V20a1 1 0 0 1 1-1zm1.2-14.2a1.2 1.2 0 1 1 2 1.3L3.8 17h16.4l-1.9-10.9a1.2 1.2 0 1 1 2-1.3l2.8 11.2a1 1 0 0 1-.97 1.24H1.87a1 1 0 0 1-.97-1.24l2.8-11.2zm8.3-2.3a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm0 4.2l3 7.8H9l3-7.8z" />
    </svg>
  );
}

export function IconPlaySmall({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style={{ transform: "translateX(1px)" }}>
      <polygon points="6 4 19 12 6 20" />
    </svg>
  );
}

export function IconSparkle({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 0l2.8 9.2L24 12l-9.2 2.8L12 24l-2.8-9.2L0 12l9.2-2.8L12 0z" />
    </svg>
  );
}

export function IconMoreDots({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="12" r="2" />
      <circle cx="12" cy="12" r="2" />
      <circle cx="19" cy="12" r="2" />
    </svg>
  );
}

export function IconSpeed({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15 15" />
    </svg>
  );
}

export function IconQuality({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <line x1="8" y1="20" x2="16" y2="20" />
      <line x1="12" y1="18" x2="12" y2="20" />
    </svg>
  );
}

export function IconClose({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

export function IconCheck({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function IconBell({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M10 21a2.2 2.2 0 0 0 4 0" />
    </svg>
  );
}

export function IconHistory({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3.2 11a9 9 0 1 0 2.4-6.2L3 7.4" />
      <polyline points="3 3 3 7.6 7.6 7.6" />
      <polyline points="12 7.5 12 12 15.5 14" />
    </svg>
  );
}

export function IconBookmark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6.5 3.5h11a1 1 0 0 1 1 1V21l-6.5-3.8L5.5 21V4.5a1 1 0 0 1 1-1z" />
    </svg>
  );
}

export function IconCoin({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.2l1.5 3.1 3.4.5-2.5 2.4.6 3.4-3-1.6-3 1.6.6-3.4-2.5-2.4 3.4-.5z" />
    </svg>
  );
}

export function IconLock({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4.2" y="10.2" width="15.6" height="10.4" rx="2.6" />
      <path d="M8 10.2V7.6a4 4 0 0 1 8 0v2.6" />
      <circle cx="12" cy="15.3" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconCalendarCheck({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <line x1="8" y1="2.8" x2="8" y2="6.6" />
      <line x1="16" y1="2.8" x2="16" y2="6.6" />
      <polyline points="8.8 15.2 11 17.4 15.4 13" />
    </svg>
  );
}

export function IconGear({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H2.9a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1.03-1.56V2.9a2 2 0 1 1 4 0v.1A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9v.1a1.7 1.7 0 0 0 1.56 1.03h.14a2 2 0 1 1 0 4h-.1A1.7 1.7 0 0 0 19.4 15z" />
    </svg>
  );
}

export function IconCopy({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="11" height="11" rx="2.2" />
      <path d="M5 15H4.2A2.2 2.2 0 0 1 2 12.8V4.2A2.2 2.2 0 0 1 4.2 2h8.6A2.2 2.2 0 0 1 15 4.2V5" />
    </svg>
  );
}

export function IconReceipt({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M9 8h6M9 12h6M9 16h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
