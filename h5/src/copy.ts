const en: Record<string, string> = {
  "nav.home": "Home",
  "nav.foryou": "For You",
  "nav.rewards": "Rewards",
  "nav.me": "Me",
  search: "Search dramas",
  retry: "Could not load. Retry.",
  empty: "No more content",
  locked: "Unlock this episode",
  select: "Episodes",
  episode_chip: "EP {number}",
  episode_short: "EP {number}",
  all_episodes: "All {count} episodes",
  all_episodes_bar: "Episodes · {count} eps",
  synopsis: "Synopsis",
  episodes_tab: "Episodes",
  expand: "more",
  collapse: "less",
  ai_disclaimer: "AI generated content",
  more: "More",
  exclusive: "MiniTV Original",
  hot: "HOT",
  coming: "Coming soon",
};

const zh: Record<string, string> = {
  "nav.home": "首页",
  "nav.foryou": "推荐",
  "nav.rewards": "奖励",
  "nav.me": "我",
  search: "搜索短剧",
  retry: "加载失败，请重试",
  empty: "没有更多了",
  locked: "解锁本集",
  select: "选集",
  episode_chip: "第 {number} 集",
  episode_short: "第 {number} 集",
  all_episodes: "全部 {count} 集",
  all_episodes_bar: "选集 · 全 {count} 集",
  synopsis: "简介",
  episodes_tab: "选集",
  expand: "展开",
  collapse: "收起",
  ai_disclaimer: "作者声明：内容由 AI 生成",
  more: "更多",
  exclusive: "MiniTV 独播",
  hot: "热播",
  coming: "即将开放",
};

export type Locale = "en" | "zh";

export function t(locale: Locale, key: string, vars: Record<string, string | number> = {}): string {
  const table = locale === "zh" ? zh : en;
  let text = table[key] ?? en[key] ?? key;
  for (const [name, value] of Object.entries(vars)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}

const KEY = "minitv_h5_locale";

export function loadLocale(): Locale {
  return localStorage.getItem(KEY) === "zh" ? "zh" : "en";
}

export function saveLocale(locale: Locale) {
  localStorage.setItem(KEY, locale);
}
