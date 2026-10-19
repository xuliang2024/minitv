/**
 * Rewards tab acceptance: daily task list, progress, claim and check-in.
 * Every /v1 call is mocked, so the check is about wiring and state, not the API.
 */
import { createRequire } from "node:module";

const require = createRequire("/opt/homebrew/lib/node_modules/");
const { chromium } = require("playwright");

const APP_URL = "http://localhost:5175/";
const OUT = "/tmp/h5rewards";
const TOKEN = "test-token-42";

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

const homePayload = {
  groups: [
    {
      id: 1,
      name: "New Arrivals",
      position: 0,
      style: "poster3",
      badge: "",
      dramas: [{ id: 77, title: "Forbidden Affair With My Husband", cover_url: "", position: 0 }],
    },
  ],
  banners: [],
};

let tasks = {
  today: "2026-10-19",
  balance: 80,
  checkin: { checked_today: false, streak: 3, amount: 50 },
  items: [
    {
      code: "like_5",
      name: "点赞 5 部剧",
      description: "当天给 5 部不同的剧点赞",
      metric: "like",
      target: 5,
      progress: 3,
      amount: 20,
      claimed: false,
    },
    {
      code: "favorite_5",
      name: "收藏 5 部剧",
      description: "当天收藏 5 部不同的剧",
      metric: "favorite",
      target: 5,
      progress: 5,
      amount: 30,
      claimed: false,
    },
    {
      code: "watch_8",
      name: "看完 8 集",
      description: "当天看完 8 集不同的剧集",
      metric: "complete",
      target: 8,
      progress: 8,
      amount: 50,
      claimed: true,
    },
  ],
};

const publicTasks = {
  today: "2026-10-19",
  balance: 0,
  checkin: { checked_today: false, streak: 1, amount: 30 },
  items: tasks.items.map((item) => ({ ...item, progress: 0, claimed: false })),
};

function json(route, status, body) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const calls = { claim: [], checkin: 0, tasks: 0 };

async function buildRoutes(route) {
  const request = route.request();
  const path = new URL(request.url()).pathname;
  const method = request.method();
  const authed = (request.headers()["authorization"] || "") === `Bearer ${TOKEN}`;

  if (path === "/v1/config") return json(route, 200, { tabs: [], copies: {} });
  if (path === "/v1/home/groups") return json(route, 200, homePayload);
  if (path === "/v1/feed") {
    return json(route, 200, {
      session_id: "s",
      page_size: 5,
      items: [],
      next_cursor: null,
      has_more: false,
      preload: { fetch_when_remaining: 2 },
    });
  }
  if (path === "/v1/auth/providers") {
    return json(route, 200, { email: { enabled: true }, google: { enabled: false, client_id: null } });
  }
  if (path === "/v1/me") {
    return authed ? json(route, 200, me) : json(route, 401, { detail: "login required" });
  }
  if (path === "/v1/tasks" && method === "GET") {
    return json(route, 200, publicTasks);
  }
  if (path === "/v1/me/tasks" && method === "GET") {
    calls.tasks += 1;
    return authed ? json(route, 200, tasks) : json(route, 401, { detail: "login required" });
  }
  if (path === "/v1/me/tasks/favorite_5/claim" && method === "POST") {
    calls.claim.push("favorite_5");
    tasks.balance += 30;
    me.coins = tasks.balance;
    tasks.items[1].claimed = true;
    return json(route, 200, { code: "favorite_5", amount: 30, balance: tasks.balance, replayed: false, progress: 5 });
  }
  if (path === "/v1/me/tasks/like_5/claim" && method === "POST") {
    calls.claim.push("like_5");
    return json(route, 409, { detail: "task not completed (3/5)" });
  }
  if (path === "/v1/me/tasks/watch_8/claim" && method === "POST") {
    calls.claim.push("watch_8");
    return json(route, 200, { code: "watch_8", amount: 0, balance: tasks.balance, replayed: true, progress: 8 });
  }
  if (path === "/v1/me/checkin" && method === "POST") {
    calls.checkin += 1;
    tasks.balance += 50;
    me.coins = tasks.balance;
    tasks.checkin.checked_today = true;
    return json(route, 200, { streak: 3, amount: 50, replayed: false, balance: tasks.balance });
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
await context.addInitScript((token) => {
  localStorage.setItem("minitv_h5_token", token);
  localStorage.setItem("minitv_h5_locale", "zh");
}, TOKEN);

const page = await context.newPage();
const errors = [];
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text().slice(0, 200));
});
page.on("pageerror", (error) => errors.push(String(error).slice(0, 200)));

await page.route("**/v1/**", buildRoutes);
await page.goto(APP_URL, { waitUntil: "networkidle" });
await page.waitForSelector('[data-tab="rewards"]', { timeout: 15000 });
await page.locator('[data-tab="rewards"]').click();
await page.waitForSelector(".rw-page .rw-task", { timeout: 8000 });
await page.waitForTimeout(600);
await page.screenshot({ path: `${OUT}-page.png` });

check((await page.locator(".rw-hero h2").innerText()).trim() === "每日任务", "task centre title renders");
check((await page.locator('[data-testid="rw-balance"]').innerText()).includes("80"), "coin balance renders");
check((await page.locator(".rw-task").count()) === 3, "three daily tasks render");
check((await page.locator('[data-testid="rw-progress-like_5"]').innerText()).trim() === "3/5", "like progress is 3/5");
check((await page.locator('[data-testid="rw-action-like_5"]').innerText()).trim() === "去完成", "unfinished task offers a shortcut");
check((await page.locator('[data-testid="rw-action-favorite_5"]').innerText()).trim() === "领取", "completed task offers a claim");
check((await page.locator('[data-testid="rw-action-watch_8"]').innerText()).trim() === "已领取", "claimed task is read-only");
check(
  (await page.locator(".rw-task").nth(1).locator(".rw-reward").innerText()).includes("+30"),
  "reward amount comes from the server",
);

await page.locator('[data-testid="rw-action-like_5"]').click();
await page.waitForTimeout(400);
check((await page.locator('[data-tab="home"].on').count()) === 1, "shortcut jumps to the home tab");

await page.locator('[data-tab="rewards"]').click();
await page.waitForSelector('[data-testid="rw-action-favorite_5"]', { timeout: 5000 });
await page.locator('[data-testid="rw-action-favorite_5"]').click();
await page.waitForTimeout(700);
check(calls.claim.includes("favorite_5"), "claim hits the API");
check((await page.locator('[data-testid="rw-balance"]').innerText()).includes("110"), "balance refreshes after claim");
check(
  (await page.locator('[data-testid="rw-action-favorite_5"]').innerText()).trim() === "已领取",
  "claimed task flips to the done state",
);
check((await page.locator('[data-testid="toast"]').innerText()).includes("+30"), "claim toast shows the amount");
await page.screenshot({ path: `${OUT}-claimed.png` });

await page.locator('[data-testid="rw-checkin"]').click();
await page.waitForTimeout(700);
check(calls.checkin === 1, "check-in hits the wallet");
check(
  (await page.locator('[data-testid="rw-checkin"]').innerText()).trim() === "已签到",
  "check-in card flips to done",
);
check((await page.locator('[data-testid="rw-balance"]').innerText()).includes("160"), "check-in refreshes the balance");
await page.screenshot({ path: `${OUT}-checkin.png` });

check(errors.length === 0, `no console errors (${errors.join(" | ") || "none"})`);

const guest = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const guestPage = await guest.newPage();
await guestPage.addInitScript(() => localStorage.setItem("minitv_h5_locale", "zh"));
await guestPage.route("**/v1/**", buildRoutes);
await guestPage.goto(APP_URL, { waitUntil: "networkidle" });
await guestPage.waitForSelector('[data-tab="rewards"]', { timeout: 15000 });
await guestPage.locator('[data-tab="rewards"]').click();
await guestPage.waitForSelector(".rw-task", { timeout: 8000 });
check((await guestPage.locator(".rw-task").count()) === 3, "guest sees the task list");
check(
  (await guestPage.locator('[data-testid="rw-progress-like_5"]').innerText()).trim() === "0/5",
  "guest progress starts at zero",
);
check((await guestPage.locator('[data-testid="rw-balance"]').innerText()).includes("0"), "guest balance is zero");
await guestPage.screenshot({ path: `${OUT}-guest.png` });

await guestPage.locator('[data-testid="rw-checkin"]').click();
await guestPage.waitForSelector('[data-testid="login-sheet"]', { timeout: 5000 });
await guestPage.waitForTimeout(500);
check((await guestPage.locator('[data-testid="login-sheet"]').count()) === 1, "guest action opens the login sheet");
await guestPage.screenshot({ path: `${OUT}-guest-login.png` });

await guestPage.locator(".auth-mask").click();
await guestPage.waitForSelector('[data-testid="login-sheet"]', { state: "detached", timeout: 5000 });
await guestPage.locator('[data-testid="rw-action-like_5"]').click();
await guestPage.waitForSelector('[data-testid="login-sheet"]', { timeout: 5000 });
check(
  (await guestPage.locator('[data-testid="login-sheet"]').count()) === 1,
  "guest shortcut also asks for a login",
);
await guest.close();

await browser.close();
const failed = results.filter((item) => !item.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length > 0) process.exit(1);
