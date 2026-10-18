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
  share: "Share",
  favorite: "Favorite",
  playback_settings: "Playback Settings",
  speed: "Speed",
  quality: "Quality",
  auto_quality: "Auto",
  sleep_timer: "Sleep Timer",
  timer_off: "Off",
  close: "Close",
  sign_in: "Sign in",
  sign_in_hint: "Sync likes, favorites and watch history",
  email_label: "Email",
  email_placeholder: "you@example.com",
  send_code: "Send code",
  sending: "Sending…",
  or: "or",
  continue_google: "Continue with Google",
  enter_code: "Enter the code",
  code_sent_to: "Sent to {email}",
  change_email: "Change email",
  verify_sign_in: "Verify and sign in",
  verifying: "Verifying…",
  resend_in: "Resend in {seconds}s",
  resend: "Resend code",
  code_expires: "Code expires in 10 minutes",
  terms: "By continuing you agree to the Terms of Service",
  sign_out: "Sign out",
  coins: "Coins",
  guest_title: "Sign in to MiniTV",
  guest_body: "Likes, favorites and watch history follow your account.",
  sign_in_prompt: "Sign in to like and save dramas",
  err_invalid_code: "That code is not right. Try again.",
  err_code_expired: "That code expired. Send a new one.",
  err_rate_limited: "Too many codes. Try again later.",
  err_mail_failed: "We could not send the email. Try again in a moment.",
  err_google: "Google sign-in is unavailable here. Use email instead.",
  err_email_invalid: "Enter a valid email address.",
  err_network: "Network error. Check your connection.",
  err_generic: "Something went wrong. Try again.",
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
  share: "分享",
  favorite: "收藏",
  playback_settings: "播放设置",
  speed: "倍速",
  quality: "清晰度",
  auto_quality: "自动",
  sleep_timer: "定时关闭",
  timer_off: "关闭",
  close: "关闭",
  sign_in: "登录",
  sign_in_hint: "同步点赞、收藏与观看记录",
  email_label: "邮箱",
  email_placeholder: "you@example.com",
  send_code: "发送验证码",
  sending: "发送中…",
  or: "或",
  continue_google: "使用 Google 账号继续",
  enter_code: "输入验证码",
  code_sent_to: "已发送至 {email}",
  change_email: "更换邮箱",
  verify_sign_in: "验证并登录",
  verifying: "验证中…",
  resend_in: "{seconds} 秒后可重发",
  resend: "重新发送",
  code_expires: "验证码 10 分钟内有效",
  terms: "继续即表示同意服务条款",
  sign_out: "退出登录",
  coins: "金币",
  guest_title: "登录 MiniTV",
  guest_body: "点赞、收藏和观看记录都会跟着你的账号走。",
  sign_in_prompt: "登录后即可点赞和收藏",
  err_invalid_code: "验证码不正确，请重试。",
  err_code_expired: "验证码已过期，请重新获取。",
  err_rate_limited: "获取过于频繁，请稍后再试。",
  err_mail_failed: "邮件发送失败，请稍后重试。",
  err_google: "当前环境无法使用 Google 登录，请改用邮箱。",
  err_email_invalid: "请输入有效的邮箱地址。",
  err_network: "网络异常，请检查连接。",
  err_generic: "出了点问题，请重试。",
};

export type Locale = "en" | "zh";

/** Ops stores floor names in one language; the English UI should not show CJK. */
const FLOOR_EN: Record<string, string> = {
  新上架: "New Arrivals",
  本周推荐: "This Week",
  热播: "Trending Now",
  榜单: "Ranking",
  猜你喜欢: "For You",
  已完结: "Completed",
};

export function floorName(name: string, locale: Locale): string {
  if (locale === "zh") return name;
  return FLOOR_EN[name] ?? name;
}

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
