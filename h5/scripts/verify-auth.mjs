import { createRequire } from "node:module";

const require = createRequire("/opt/homebrew/lib/node_modules/");
const { chromium } = require("playwright");

const APP_URL = "http://127.0.0.1:5175/";
const OUT = "/tmp/h5auth";
const TOKEN = "test-token-42";
const EMAIL = "viewer@example.com";

const me = {
  id: 42,
  public_id: "1000000042",
  email: EMAIL,
  display_name: "viewer",
  avatar_url: "",
  bio: "",
  coins: 80,
  following_count: 8,
  follower_count: 2,
  received_likes: 16,
  reminder_opt_in: false,
};

const drama = {
  id: 7,
  title: "Mock Drama",
  synopsis: "A drama used by the auth acceptance script.",
  cover_url: null,
  tags: ["test"],
  free_episode_count: 0,
  coin_price_per_episode: 60,
  play_count: 999,
  like_count: 0,
  comment_count: 0,
  liked: false,
  favorited: false,
};

const playPayload = {
  drama,
  items: [
    {
      id: 70,
      episode_number: 1,
      title: "EP1",
      duration_seconds: 90,
      free: false,
      locked: true,
      progress_seconds: 0,
      unlock: { coin_price: 60 },
    },
  ],
  resume: { episode_id: 70, position_seconds: 0 },
  entitlement: { member: false, unlocked_episode_ids: [] },
};

const homePayload = {
  groups: [
    {
      id: 1,
      name: "New Arrivals",
      position: 0,
      style: "poster3",
      badge: "",
      dramas: [{ id: 7, title: "Mock Drama", cover_url: null, position: 0 }],
    },
  ],
  banners: [],
};

function json(route, status, body) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function buildRoutes(calls, options) {
  return async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const payload = request.postData() ? JSON.parse(request.postData()) : null;
    const authorization = request.headers()["authorization"] || "";

    if (path === "/v1/config") return json(route, 200, { tabs: [], copies: {} });
    if (path === "/v1/home/groups") return json(route, 200, homePayload);
    if (path === "/v1/dramas/7/play") return json(route, 200, playPayload);
    if (path === "/v1/dramas/8/play") return json(route, 200, playPayload);
    if (path === "/v1/feed") {
      return json(
        route,
        200,
        options.feed ?? {
          session_id: "test-session",
          page_size: 5,
          items: [],
          next_cursor: null,
          has_more: false,
          preload: { fetch_when_remaining: 2 },
        },
      );
    }
    if (path === "/v1/me/favorites") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, options.favorites ?? { total: 0, items: [] });
    }
    if (path === "/v1/me/history") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, { items: [] });
    }
    if (path === "/v1/me/lists/like") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, { total: 0, items: [] });
    }
    if (path === "/v1/me/ledger") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, { items: [] });
    }
    if (path === "/v1/me/messages") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, { unread: 0, items: [] });
    }
    if (path === "/v1/me/messages/read") return json(route, 200, { unread: 0 });
    if (path === "/v1/me/checkin") return json(route, 200, { streak: 1, amount: 10, replayed: false, balance: 90 });
    if (path === "/v1/auth/providers") {
      return json(route, 200, {
        email: { enabled: true },
        google: options.google
          ? { enabled: true, client_id: "test-client.apps.googleusercontent.com" }
          : { enabled: false, client_id: null },
      });
    }
    if (path === "/v1/auth/email/code" && method === "POST") {
      calls.code.push(payload);
      return json(route, 200, { sent: true });
    }
    if (path === "/v1/auth/email/authenticate" && method === "POST") {
      calls.verify.push(payload);
      if (payload.code === "000000") return json(route, 400, { detail: "invalid code" });
      return json(route, 200, { token: TOKEN, user_id: 42, public_id: "1000000042", created: false });
    }
    if (path === "/v1/auth/google" && method === "POST") {
      calls.google.push(payload);
      return json(route, 200, { token: TOKEN, user_id: 42, public_id: "1000000042", created: false });
    }
    if (path === "/v1/me") {
      if (authorization !== `Bearer ${TOKEN}`) return json(route, 401, { detail: "login required" });
      return json(route, 200, me);
    }
    if (path === "/v1/reactions" && method === "POST") {
      calls.reactions.push(payload);
      if (options.reactionStatus) return json(route, options.reactionStatus, { detail: "boom" });
      return json(route, 200, { active: !payload.remove });
    }
    if (path === "/v1/playback") return json(route, 200, { allowed: false, reason: "locked" });
    return json(route, 404, { detail: `not mocked: ${method} ${path}` });
  };
}

const results = [];
function check(ok, label) {
  results.push({ ok, label });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
}

const browser = await chromium.launch({ channel: "chrome" });

async function newPage(options = {}) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  if (options.googleStub) {
    await context.addInitScript(() => {
      window.google = {
        accounts: {
          id: {
            initialize: (config) => {
              window.__googleConfig = config;
            },
            renderButton: (host) => {
              const button = document.createElement("button");
              button.type = "button";
              button.textContent = "Continue with Google";
              button.addEventListener("click", () => {
                window.__googleConfig.callback({ credential: "fake-google-credential" });
              });
              host.appendChild(button);
            },
          },
        },
      };
    });
  }
  const page = await context.newPage();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text().slice(0, 200));
  });
  page.on("pageerror", (error) => errors.push(String(error).slice(0, 200)));
  return { context, page, errors };
}

// ---- 1. email sign-in, replay, persistence, sign-out ----
{
  const calls = { code: [], verify: [], reactions: [], google: [] };
  const { context, page, errors } = await newPage();
  await page.route("**/v1/**", buildRoutes(calls, { google: false }));

  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".card", { timeout: 15000 });
  await page.locator(".card").first().click();
  await page.waitForSelector('.rail-btn[aria-label="Like"]', { timeout: 10000 });

  const likeLabel = (await page.locator('.rail-btn[aria-label="Like"] .rail-label').innerText()).trim();
  check(likeLabel === "0", "like counter renders like_count, not play_count");

  await page.locator('.rail-btn[aria-label="Like"]').click();
  await page.waitForSelector('[data-testid="login-sheet"]', { timeout: 5000 });
  check(true, "guest like opens the sign-in sheet");
  check(calls.reactions.length === 0, "no reaction is sent while signed out");
  check((await page.locator('[data-testid="google-slot"]').count()) === 0, "google button hidden when providers says disabled");
  await page.waitForTimeout(350);
  await page.screenshot({ path: `${OUT}-sheet.png` });

  await page.locator("#auth-email").fill("not-an-email");
  await page.getByRole("button", { name: "Send code" }).click();
  check((await page.locator(".auth-error").innerText()).includes("valid email"), "invalid email is rejected before any request");
  check(calls.code.length === 0, "no code request for an invalid email");

  await page.locator("#auth-email").fill(EMAIL);
  await page.getByRole("button", { name: "Send code" }).click();
  await page.waitForSelector(".auth-code", { timeout: 8000 });
  check(calls.code.length === 1 && calls.code[0].purpose === "login", "code request asks for the login purpose");
  check(
    (await page.locator(".auth-hint").first().innerText()).includes("v***@example.com"),
    "sent-to line masks the email",
  );
  await page.screenshot({ path: `${OUT}-code.png` });

  await page.locator(".auth-box").first().pressSequentially("000000");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await page.waitForSelector(".auth-error", { timeout: 8000 });
  check((await page.locator(".auth-error").innerText()).includes("not right"), "wrong code surfaces the server error");
  check(calls.verify.length === 1 && calls.verify[0].code === "000000", "wrong code reached the API once");

  await page.getByRole("button", { name: "Change email" }).click();
  await page.waitForSelector("#auth-email", { timeout: 5000 });
  await page.getByRole("button", { name: "Send code" }).click();
  await page.waitForSelector(".auth-code", { timeout: 8000 });
  await page.locator(".auth-box").first().pressSequentially("123456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await page.waitForSelector('[data-testid="login-sheet"]', { state: "detached", timeout: 8000 });

  const stored = await page.evaluate(() => localStorage.getItem("minitv_h5_token"));
  check(stored === TOKEN, "session token is persisted");
  await page.waitForTimeout(400);
  check(
    calls.reactions.length === 1 &&
      calls.reactions[0].kind === "like" &&
      calls.reactions[0].remove === false,
    "the blocked like is replayed right after sign-in",
  );
  check(
    (await page.locator('.rail-btn[aria-label="Like"]').getAttribute("class")).includes("on"),
    "like button turns on after the replay",
  );
  check(
    (await page.locator('.rail-btn[aria-label="Like"] .rail-label').innerText()).trim() === "1",
    "like counter increments after the replay",
  );
  await page.screenshot({ path: `${OUT}-play-signed-in.png` });

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector('[data-tab="me"]', { timeout: 10000 });
  await page.locator('[data-tab="me"]').click();
  await page.waitForSelector(".me-hero h2", { timeout: 8000 });
  check((await page.locator(".me-hero h2").innerText()).trim() === "viewer", "reload keeps the session and renders the profile");
  check((await page.locator('[data-testid="me-coins"]').innerText()).trim() === "80", "profile shows the coin balance");
  await page.screenshot({ path: `${OUT}-me.png` });

  await page.getByRole("button", { name: "Settings" }).click();
  await page.waitForSelector('[data-testid="settings-sheet"]', { timeout: 5000 });
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForSelector(".me-cta", { timeout: 5000 });
  check((await page.evaluate(() => localStorage.getItem("minitv_h5_token"))) === null, "sign out clears the token");
  check((await page.locator(".me-hero h2").innerText()).includes("Sign in"), "sign out returns the guest state");

  const unexpected = errors.filter((text) => !/status of 400/.test(text));
  check(unexpected.length === 0, `no unexpected console errors (${unexpected.join(" | ") || "none"})`);
  await context.close();
}

// ---- 2. google sign-in ----
{
  const calls = { code: [], verify: [], reactions: [], google: [] };
  const { context, page, errors } = await newPage({ googleStub: true });
  await page.route("**/v1/**", buildRoutes(calls, { google: true }));

  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".card", { timeout: 15000 });
  await page.locator(".card").first().click();
  await page.waitForSelector('.rail-btn[aria-label="Like"]', { timeout: 10000 });
  await page.locator('.rail-btn[aria-label="Like"]').click();
  await page.waitForSelector('[data-testid="login-sheet"]', { timeout: 5000 });

  await page.waitForSelector('[data-testid="google-slot"] button', { timeout: 8000 });
  check(true, "google button renders when a client id is published");
  await page.locator('[data-testid="google-slot"] button').click();
  await page.waitForSelector('[data-testid="login-sheet"]', { state: "detached", timeout: 8000 });
  check(
    calls.google.length === 1 && calls.google[0].id_token === "fake-google-credential",
    "google credential is exchanged for a session",
  );
  await page.waitForTimeout(400);
  check(calls.reactions.length === 1, "google sign-in also replays the blocked like");
  check(errors.length === 0, `google flow has no console errors (${errors.join(" | ") || "none"})`);
  await context.close();
}

// ---- 3. a dead stored token falls back to guest ----
{
  const calls = { code: [], verify: [], reactions: [], google: [] };
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  await context.addInitScript(() => {
    localStorage.setItem("minitv_h5_token", "expired-token");
  });
  const page = await context.newPage();
  await page.route("**/v1/**", buildRoutes(calls, { google: false }));

  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-tab="me"]', { timeout: 15000 });
  await page.locator('[data-tab="me"]').click();
  await page.waitForSelector(".me-cta", { timeout: 8000 });
  check(
    (await page.evaluate(() => localStorage.getItem("minitv_h5_token"))) === null,
    "an expired stored token is cleared on boot",
  );
  check((await page.locator(".me-hero h2").innerText()).includes("Sign in"), "expired session lands on the guest state");
  await context.close();
}

// ---- 4. feed likes + favorites list ----
{
  const calls = { code: [], verify: [], reactions: [], google: [] };
  const { context, page, errors } = await newPage();
  await context.addInitScript((token) => localStorage.setItem("minitv_h5_token", token), TOKEN);
  const feedDrama = { ...drama, like_count: 3, liked: true };
  await page.route("**/v1/**", buildRoutes(calls, {
    google: false,
    feed: {
      session_id: "test-session",
      page_size: 5,
      has_more: false,
      next_cursor: null,
      preload: { fetch_when_remaining: 2 },
      items: [{
        item_id: "d7:e70",
        drama: feedDrama,
        episode: { id: 70, episode_number: 1, title: "EP1", duration_seconds: 90, free: false },
        resume_seconds: 0,
        entry_reason: "test",
        score: 1,
      }],
    },
    favorites: {
      total: 2,
      items: [
        { id: 7, title: "Mock Drama", cover_url: null, synopsis: "", play_count: 9 },
        { id: 8, title: "Second Drama", cover_url: null, synopsis: "", play_count: 4 },
      ],
    },
  }));

  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-tab="foryou"]', { timeout: 15000 });
  await page.locator('[data-tab="foryou"]').click();
  await page.waitForSelector('.rail-btn[aria-label="Like"]', { timeout: 10000 });
  const likeBtn = page.locator('.rail-btn[aria-label="Like"]');
  check((await likeBtn.locator(".rail-label").innerText()).trim() === "3", "feed shows the server like count");
  check((await likeBtn.getAttribute("class")).includes("on"), "feed restores the viewer's like state");
  await likeBtn.click();
  await page.waitForTimeout(350);
  const lastReaction = calls.reactions[calls.reactions.length - 1];
  check(
    Boolean(lastReaction) && lastReaction.target_type === "drama" && lastReaction.target_id === 7 && lastReaction.remove === true,
    "feed like toggles the drama reaction",
  );
  check((await likeBtn.locator(".rail-label").innerText()).trim() === "2", "feed like count decrements optimistically");
  await page.screenshot({ path: `${OUT}-foryou.png` });

  await page.locator('[data-tab="me"]').click();
  await page.waitForSelector('[data-testid="me-favorites"]', { timeout: 8000 });
  await page.waitForFunction(
    () => document.querySelector('[data-testid="me-favorites"]')?.textContent?.includes("2"),
    null,
    { timeout: 5000 },
  );
  check((await page.locator('[data-testid="me-favorites"]').innerText()).includes("2"), "me page shows the favorite total");
  await page.locator('[data-testid="me-favorites"]').click();
  await page.waitForSelector(".fav-grid .card", { timeout: 8000 });
  check((await page.locator(".fav-grid .card").count()) === 2, "favorites page lists saved dramas");
  await page.screenshot({ path: `${OUT}-favorites.png` });
  await page.locator(".fav-grid .card").first().click();
  await page.waitForSelector('.rail-btn[aria-label="Like"]', { timeout: 10000 });
  check(true, "tapping a favorite opens the drama");
  check(errors.length === 0, `social flow has no console errors (${errors.join(" | ") || "none"})`);
  await context.close();
}

// ---- 5. a failed reaction reverts and tells the viewer ----
{
  const calls = { code: [], verify: [], reactions: [], google: [] };
  const { context, page } = await newPage();
  await context.addInitScript((token) => localStorage.setItem("minitv_h5_token", token), TOKEN);
  await page.route("**/v1/**", buildRoutes(calls, { google: false, reactionStatus: 500 }));
  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".card", { timeout: 15000 });
  await page.locator(".card").first().click();
  await page.waitForSelector('.rail-btn[aria-label="Favorite"]', { timeout: 10000 });
  await page.locator('.rail-btn[aria-label="Favorite"]').click();
  await page.waitForSelector('[data-testid="toast"]', { timeout: 8000 });
  check((await page.locator('[data-testid="toast"]').innerText()).includes("Could not save"), "failed reaction shows a toast");
  check(
    !(await page.locator('.rail-btn[aria-label="Favorite"]').getAttribute("class")).includes("on"),
    "failed favorite rolls back",
  );
  await context.close();
}

await browser.close();

const failed = results.filter((item) => !item.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length > 0) process.exit(1);
