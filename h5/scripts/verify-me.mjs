/**
 * Me page acceptance: profile header, quick actions, library tabs and sheets.
 * Every /v1 call is mocked so the check is about layout + wiring, not the API.
 */
import { createRequire } from "node:module";

const require = createRequire("/opt/homebrew/lib/node_modules/");
const { chromium } = require("playwright");

const APP_URL = "http://localhost:5175/";
const OUT = "/tmp/h5me";
const TOKEN = "test-token-42";

const covers = {
  a: "https://minitv-dev-img.b-cdn.net/covers/77/cover.jpg",
  b: "https://minitv-dev-img.b-cdn.net/covers/114/cover.jpg",
  c: "https://minitv-dev-img.b-cdn.net/covers/45/cover.jpg",
  d: "https://minitv-dev-img.b-cdn.net/covers/78/cover.jpg",
  e: "https://minitv-dev-img.b-cdn.net/covers/110/cover.jpg",
  f: "https://minitv-dev-img.b-cdn.net/covers/9/cover.jpg",
};

const me = {
  id: 21,
  public_id: "6168731156",
  email: "test@example.com",
  display_name: "mygodis",
  avatar_url: "",
  bio: "",
  coins: 80,
  following_count: 8,
  follower_count: 2,
  received_likes: 16,
  reminder_opt_in: false,
};

const history = {
  items: [
    { episode_id: 5172, position_seconds: 96, episode_number: 3, duration_seconds: 180, updated_at: "2026-10-19T07:10:00+08:00", drama: { id: 77, title: "Forbidden Affair With My Husband", cover_url: covers.a, episode_count: 74 } },
    { episode_id: 7978, position_seconds: 12, episode_number: 1, duration_seconds: 180, updated_at: "2026-10-19T06:40:00+08:00", drama: { id: 114, title: "My Bodyguard Husband Is the Boss", cover_url: covers.b, episode_count: 62 } },
    { episode_id: 2925, position_seconds: 80, episode_number: 7, duration_seconds: 180, updated_at: "2026-10-18T22:12:00+08:00", drama: { id: 45, title: "Got Myself A Priceless Husband", cover_url: covers.c, episode_count: 55 } },
    { episode_id: 5241, position_seconds: 30, episode_number: 2, duration_seconds: 180, updated_at: "2026-10-18T20:02:00+08:00", drama: { id: 78, title: "Love Trap with My Dashing Knight", cover_url: covers.d, episode_count: 70 } },
    { episode_id: 7490, position_seconds: 44, episode_number: 4, duration_seconds: 180, updated_at: "2026-10-17T19:01:00+08:00", drama: { id: 110, title: "Mating of the Twilight Goddess", cover_url: covers.e, episode_count: 64 } },
    { episode_id: 116, position_seconds: 8, episode_number: 1, duration_seconds: 180, updated_at: "2026-10-16T18:20:00+08:00", drama: { id: 9, title: "The Grand Fortune Teller", cover_url: covers.f, episode_count: 58 } },
  ],
};

const favorites = {
  total: 2,
  items: [
    { id: 114, title: "My Bodyguard Husband Is the Boss", cover_url: covers.b, synopsis: "", play_count: 220, episode_count: 62 },
    { id: 77, title: "Forbidden Affair With My Husband", cover_url: covers.a, synopsis: "", play_count: 180, episode_count: 74 },
  ],
};

const likes = {
  total: 3,
  items: [
    { target_type: "drama", target_id: 77, drama: { id: 77, title: "Forbidden Affair With My Husband", cover_url: covers.a, episode_count: 74 } },
    { target_type: "drama", target_id: 114, drama: { id: 114, title: "My Bodyguard Husband Is the Boss", cover_url: covers.b, episode_count: 62 } },
    { target_type: "episode", target_id: 5172 },
  ],
};

const messages = {
  unread: 1,
  items: [
    { id: 3, kind: "system", title: "New episodes are in", body: "My Bodyguard Husband Is the Boss just added 3 episodes.", read: false },
    { id: 2, kind: "system", title: "Welcome to MiniTV", body: "Your watch history and favorites sync across devices.", read: true },
  ],
};

const ledger = {
  items: [
    { amount: 10, balance_after: 80, biz_type: "checkin", created_at: "2026-10-19T07:02:00+08:00" },
    { amount: 30, balance_after: 70, biz_type: "signup", created_at: "2026-10-18T11:20:00+08:00" },
    { amount: 60, balance_after: 40, biz_type: "purchase", created_at: "2026-10-18T11:18:00+08:00" },
    { amount: -20, balance_after: 20, biz_type: "unlock", created_at: "2026-10-18T11:10:00+08:00" },
  ],
};

const homePayload = {
  groups: [
    {
      id: 1,
      name: "New Arrivals",
      position: 0,
      style: "poster3",
      badge: "",
      dramas: [{ id: 77, title: "Forbidden Affair With My Husband", cover_url: covers.a, position: 0 }],
    },
  ],
  banners: [],
};

function json(route, status, body) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const calls = { patch: [], read: 0, checkin: 0 };

async function buildRoutes(route) {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();
  const authorization = request.headers()["authorization"] || "";
  const authed = authorization === `Bearer ${TOKEN}`;

  if (path === "/v1/config") return json(route, 200, { tabs: [], copies: {} });
  if (path === "/v1/home/groups") return json(route, 200, homePayload);
  if (path === "/v1/feed") {
    return json(route, 200, { session_id: "s", page_size: 5, items: [], next_cursor: null, has_more: false, preload: { fetch_when_remaining: 2 } });
  }
  if (path === "/v1/auth/providers") return json(route, 200, { email: { enabled: true }, google: { enabled: false, client_id: null } });
  if (path === "/v1/me" && method === "GET") return authed ? json(route, 200, me) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me" && method === "PATCH") {
    if (!authed) return json(route, 401, { detail: "login required" });
    const body = JSON.parse(request.postData() || "{}");
    calls.patch.push(body);
    if (body.display_name) me.display_name = body.display_name;
    if (typeof body.bio === "string") me.bio = body.bio;
    return json(route, 200, { ok: true });
  }
  if (path === "/v1/me/history") return authed ? json(route, 200, history) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me/favorites") return authed ? json(route, 200, favorites) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me/lists/like") return authed ? json(route, 200, likes) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me/messages" && method === "GET") return authed ? json(route, 200, messages) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me/messages/read" && method === "POST") {
    calls.read += 1;
    messages.unread = 0;
    return json(route, 200, { unread: 0 });
  }
  if (path === "/v1/me/ledger") return authed ? json(route, 200, ledger) : json(route, 401, { detail: "login required" });
  if (path === "/v1/me/checkin" && method === "POST") {
    calls.checkin += 1;
    me.coins += 10;
    return json(route, 200, { streak: 3, amount: 10, replayed: false, balance: me.coins });
  }
  return json(route, 404, { detail: `not mocked: ${method} ${path}` });
}

const results = [];
function check(ok, label) {
  results.push({ ok, label });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
}

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});
await context.addInitScript((token) => localStorage.setItem("minitv_h5_token", token), TOKEN);

const page = await context.newPage();
const errors = [];
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text().slice(0, 200));
});
page.on("pageerror", (error) => errors.push(String(error).slice(0, 200)));

await page.route("**/v1/**", buildRoutes);
await page.goto(APP_URL, { waitUntil: "networkidle" });
await page.waitForSelector('[data-tab="me"]', { timeout: 15000 });
await page.locator('[data-tab="me"]').click();
await page.waitForSelector(".me-page", { timeout: 8000 });
await page.waitForFunction(() => document.querySelectorAll(".me-grid .card").length >= 6, null, { timeout: 8000 });
await page.waitForTimeout(900);
await page.screenshot({ path: `${OUT}-page.png` });

check((await page.locator(".me-hero h2").innerText()).trim() === "mygodis", "profile name renders");
check((await page.locator(".me-uid").innerText()).includes("6168731156"), "MiniTV id renders");
check((await page.locator('[data-testid="me-coins"]').innerText()).trim() === "80", "coin balance renders");
check((await page.locator(".me-tab.on").innerText()).trim() === "History", "history tab is the default");
check((await page.locator(".me-grid .card").count()) === 6, "history grid de-duplicates to one card per drama");
check((await page.locator(".me-tile-badge").first().innerText()).includes("EP 3"), "history card shows the last episode");
check((await page.locator(".me-dot").count()) === 1, "unread badge shows on the bell");

await page.locator('[data-testid="me-tab-favorites"]').click();
await page.waitForSelector(".fav-grid .card", { timeout: 8000 });
check((await page.locator(".fav-grid .card").count()) === 2, "favorites tab lists saved dramas");
await page.screenshot({ path: `${OUT}-favorites.png` });

await page.locator('[data-testid="me-tab-likes"]').click();
await page.waitForFunction(() => document.querySelectorAll(".me-tile").length === 2, null, { timeout: 8000 });
check((await page.locator(".me-tile").count()) === 2, "likes tab drops episode-level reactions");
await page.screenshot({ path: `${OUT}-likes.png` });

await page.locator(".me-quick-item").nth(2).click();
await page.waitForSelector('[data-testid="ledger-sheet"]', { timeout: 5000 });
await page.waitForSelector(".me-ledger li", { timeout: 5000 });
check((await page.locator(".me-ledger li").count()) === 4, "ledger lists coin entries");
check((await page.locator(".me-balance").innerText()).includes("80"), "ledger shows the balance");
await page.screenshot({ path: `${OUT}-ledger.png` });
await page.locator(".me-sheet .close-btn").click();
await page.waitForSelector('[data-testid="ledger-sheet"]', { state: "detached", timeout: 5000 });

await page.locator('[data-testid="me-messages"]').click();
await page.waitForSelector('[data-testid="messages-sheet"]', { timeout: 5000 });
await page.waitForSelector(".me-inbox li", { timeout: 5000 });
await page.waitForTimeout(400);
check((await page.locator(".me-inbox li").count()) === 2, "messages sheet lists the inbox");
check(calls.read === 1, "opening messages marks them read");
await page.screenshot({ path: `${OUT}-messages.png` });
await page.locator(".me-sheet .close-btn").click();
await page.waitForSelector('[data-testid="messages-sheet"]', { state: "detached", timeout: 5000 });
await page.waitForTimeout(200);
check((await page.locator(".me-dot").count()) === 0, "unread badge clears after reading");

await page.locator(".me-edit").click();
await page.waitForSelector('[data-testid="edit-profile-sheet"]', { timeout: 5000 });
await page.locator('[data-testid="edit-profile-sheet"] input').fill("追剧的橘子");
await page.locator('[data-testid="edit-profile-sheet"] textarea').fill("每天两集，周末通宵");
await page.locator('[data-testid="edit-profile-sheet"]').getByRole("button", { name: "Save" }).click();
await page.waitForSelector('[data-testid="edit-profile-sheet"]', { state: "detached", timeout: 5000 });
check(calls.patch.length === 1 && calls.patch[0].display_name === "追剧的橘子", "profile save patches the name");
check((await page.locator(".me-hero h2").innerText()).trim() === "追剧的橘子", "saved name shows immediately");
await page.screenshot({ path: `${OUT}-edited.png` });

await page.locator('[data-testid="me-checkin"]').click();
await page.waitForTimeout(500);
check(calls.checkin === 1, "check-in hits the wallet");
check((await page.locator('[data-testid="me-coins"]').innerText()).trim() === "90", "check-in refreshes the balance");
check((await page.locator('[data-testid="me-checkin"]').innerText()).includes("Checked in"), "check-in switches to the done state");

await page.getByRole("button", { name: "Settings" }).click();
await page.waitForSelector('[data-testid="settings-sheet"]', { timeout: 5000 });
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}-settings.png` });
await page.getByRole("button", { name: "中文" }).click();
await page.waitForTimeout(300);
check((await page.locator(".me-tab.on").innerText()).trim() === "点赞", "language switch re-renders the page");
await page.screenshot({ path: `${OUT}-settings-zh.png` });

check(errors.length === 0, `no console errors (${errors.join(" | ") || "none"})`);

await browser.close();
const failed = results.filter((item) => !item.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length > 0) process.exit(1);
