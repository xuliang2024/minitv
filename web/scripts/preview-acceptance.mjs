/**
 * Web free-preview acceptance (试播入口验收) — run with `npm run accept:preview`.
 *
 * Drives the local MiniTV website with headless Chrome and checks:
 *   - homepage entry points (nav + hero + teaser section)
 *   - preview catalog: per-category cap of 7, "View all in the app" links
 *   - free episode playback via /v1/playback + hls.js
 *   - manual quality menu: real manifest renditions, level switch and Auto
 *   - locked episodes: download overlay, and never a playback authorization
 *   - auto-advance from the last free episode stops at the lock
 *   - "View all" / locked CTAs land on the app download section
 *   - mobile layout has no horizontal overflow
 *
 * Requirements: local dev servers (web :5174, api :18110) and `puppeteer-core`
 * (a web devDependency). Override defaults with CHROME_PATH / WEB_URL /
 * API_URL / SHOTS_DIR env vars. Screenshots and the JSON report land in
 * docs/acceptance/walkthrough/.
 */
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = process.env.WEB_URL ?? "http://127.0.0.1:5174";
const API = process.env.API_URL ?? "http://127.0.0.1:18110";
const SHOTS =
  process.env.SHOTS_DIR ?? path.resolve(HERE, "..", "..", "docs", "acceptance", "walkthrough");

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(page, fn, { timeout = 15000, label = "condition", args = [] } = {}) {
  const start = Date.now();
  for (;;) {
    if (await page.evaluate(fn, ...args).catch(() => false)) return true;
    if (Date.now() - start > timeout) throw new Error(`timeout waiting for ${label}`);
    await sleep(200);
  }
}

const catalog = await (await fetch(`${API}/v1/preview`)).json();
const allRow = catalog.categories[0];
// Pick a previewable drama that actually has locked (paid) episodes so the
// download-the-app path can be exercised.
const target = allRow.dramas.find((d) => d.episode_count > d.free_playable_count) ?? allRow.dramas[0];
const detail = await (await fetch(`${API}/v1/preview/dramas/${target.id}`)).json();
const freeEp = detail.items.find((e) => e.free);
const lockedEp = detail.items.find((e) => !e.free);
console.log(`target drama: #${target.id} ${target.title} (free=${target.free_playable_count}, episodes=${target.episode_count}, locked ep=${lockedEp?.episode_number})`);

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check", "--window-size=1600,1000"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });

const playbackCalls = [];
page.on("request", (req) => {
  if (req.method() === "POST" && new URL(req.url()).pathname === "/v1/playback") {
    try {
      playbackCalls.push(JSON.parse(req.postData() || "{}").episode_id);
    } catch {
      playbackCalls.push(-1);
    }
  }
});

try {
  // ---------- 1. Homepage entry ----------
  await page.goto(`${BASE}/#home`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".hero-actions");
  const heroEntry = await page.$eval('.hero-actions a[href="#preview"]', (el) => el.textContent.trim());
  check("home: hero has the trial entry", heroEntry.toLowerCase().includes("watch free"), `“${heroEntry}”`);
  const navEntry = await page.$eval('.site-nav a[href="#preview"]', (el) => el.textContent.trim());
  check("home: nav has Free Preview", navEntry === "Free Preview", navEntry);
  await waitFor(page, () => Boolean(document.querySelector(".preview-badge")), { label: "home preview teaser cards" });
  check("home: preview teaser section rendered", true);
  await page.screenshot({ path: `${SHOTS}/preview-01-home-entry.png` });

  const teaserSection = await page.evaluateHandle(() => {
    const heading = [...document.querySelectorAll("section h2")].find((el) =>
      el.textContent.includes("Watch free episodes online"),
    );
    return heading ? heading.closest("section") : null;
  });
  const teaserEl = teaserSection.asElement();
  check("home: teaser section highlights the free preview", Boolean(teaserEl));
  if (teaserEl) {
    await teaserEl.screenshot({ path: `${SHOTS}/preview-07-home-teaser.png` });
    await page.evaluate(() => window.scrollTo({ top: 0 }));
    await sleep(400);
  }

  // ---------- 2. Preview catalog ----------
  await page.click('.hero-actions a[href="#preview"]');
  await page.waitForSelector(".preview-page .preview-row");
  await waitFor(page, () => document.querySelectorAll(".preview-row").length >= 10, { label: "preview rows" });

  const rows = await page.$$eval(".preview-row", (rowEls) =>
    rowEls.map((row) => ({
      title: row.querySelector("h2")?.textContent.trim() ?? "",
      cards: row.querySelectorAll(".poster.preview-card").length,
      viewAll: row.querySelector('.text-link[href="#download"]')?.textContent.trim() ?? "",
    })),
  );
  check("preview: multiple category rows", rows.length >= 10, `${rows.length} rows`);
  check("preview: every category shows at most 7 shows", rows.every((row) => row.cards <= 7), rows.map((r) => r.cards).join(","));
  check(
    "preview: every row links View all to the app",
    rows.every((row) => row.viewAll.includes("View all")),
  );
  const capped = allRow.total > 7;
  check(
    "preview: All shows capped at 7 of " + allRow.total,
    rows[0].cards === Math.min(7, allRow.total),
    `row0 cards=${rows[0].cards}`,
  );
  await page.screenshot({ path: `${SHOTS}/preview-02-catalog.png` });
  // one row close-up
  const secondRow = await page.$(".preview-row:nth-of-type(3)");
  if (secondRow) await secondRow.screenshot({ path: `${SHOTS}/preview-03-category-row.png` });

  // ---------- 3. Player: free episode streams ----------
  const cardSelector = `a[href="#watch/${target.id}"]`;
  await page.evaluate((sel) => document.querySelector(sel)?.scrollIntoView({ block: "center" }), cardSelector);
  await sleep(300);
  await page.click(cardSelector);
  await page.waitForSelector(".watch-page .watch-side .ep-grid");
  const title = await page.$eval(".watch-title", (el) => el.textContent.trim());
  check("player: opened target drama", title === target.title, title);

  const epButtons = await page.$$eval(".ep-btn", (els) =>
    els.map((el) => ({ label: el.textContent.trim(), locked: el.classList.contains("is-locked"), free: el.classList.contains("is-free") })),
  );
  check("player: episode grid lists published episodes", epButtons.length === detail.items.length, `${epButtons.length} buttons`);
  check(
    "player: locked episodes rendered with lock state",
    epButtons.filter((b) => b.locked).length === detail.items.filter((e) => !e.free).length,
    `${epButtons.filter((b) => b.locked).length} locked`,
  );

  await page.click(`.ep-btn.is-free`);
  await waitFor(page, () => {
    const v = document.querySelector("video");
    return Boolean(v) && v.currentTime > 0.5 && v.readyState >= 2;
  }, { timeout: 40000, label: "free episode playback" });
  const playbackState = await page.$eval("video", (v) => ({ currentTime: v.currentTime, readyState: v.readyState, paused: v.paused }));
  check("player: free episode is playing", !playbackState.paused && playbackState.currentTime > 0.5, JSON.stringify(playbackState));
  check("player: free episode authorized exactly once so far", playbackCalls.filter((id) => id === freeEp.id).length === 1, JSON.stringify(playbackCalls));
  await sleep(1200);
  await page.screenshot({ path: `${SHOTS}/preview-04-player-playing.png` });

  // ---------- 3b. Manual quality menu ----------
  await page.waitForSelector(".watch-quality-btn", { timeout: 30000 });
  await page.click(".watch-quality-btn");
  await page.waitForSelector(".watch-quality-menu");
  const qualityOptions = await page.$$eval(".watch-quality-item", (els) =>
    els.map((el) => ({
      label: el.textContent.trim(),
      level: el.dataset.level ?? "",
      shortSide: Number(el.dataset.shortSide ?? 0),
    })),
  );
  const autoOption = qualityOptions.find((option) => option.level === "-1");
  const levelOptions = qualityOptions
    .filter((option) => option.level !== "-1")
    .sort((a, b) => a.shortSide - b.shortSide);
  check(
    "player: quality menu lists Auto plus the real manifest renditions",
    Boolean(autoOption) && levelOptions.length >= 2 && levelOptions.every((option) => option.shortSide > 0),
    qualityOptions.map((option) => option.label).join(" | "),
  );
  await page.screenshot({ path: `${SHOTS}/preview-11-quality-menu.png` });

  const switchQuality = async (option) => {
    const menuOpen = await page.$eval(".watch-quality-menu", () => true).catch(() => false);
    if (!menuOpen) await page.click(".watch-quality-btn");
    await page.waitForSelector(".watch-quality-menu");
    await page.evaluate((level) => {
      document.querySelector(`.watch-quality-item[data-level="${level}"]`).click();
    }, option.level);
    try {
      await waitFor(
        page,
        (px) => {
          const v = document.querySelector("video");
          return Boolean(v) && (v.videoWidth === px || v.videoHeight === px);
        },
        { timeout: 40000, label: `video decodes at ${option.shortSide}px`, args: [option.shortSide] },
      );
      return true;
    } catch {
      return false;
    }
  };

  const qualityButton = () => page.$eval(".watch-quality-btn", (el) => el.textContent.trim());
  const highest = levelOptions[levelOptions.length - 1];
  const lowest = levelOptions[0];
  const switchedHigh = await switchQuality(highest);
  const dimsHigh = await page.$eval("video", (v) => `${v.videoWidth}x${v.videoHeight}`);
  check(
    `player: manual ${highest.label} pick bypasses the size cap`,
    switchedHigh && (await qualityButton()).includes(highest.label),
    `video=${dimsHigh} button=${await qualityButton()}`,
  );
  const switchedLow = await switchQuality(lowest);
  const dimsLow = await page.$eval("video", (v) => `${v.videoWidth}x${v.videoHeight}`);
  check(
    `player: manual ${lowest.label} pick switches down`,
    switchedLow && (await qualityButton()).includes(lowest.label),
    `video=${dimsLow} button=${await qualityButton()}`,
  );
  await page.screenshot({ path: `${SHOTS}/preview-12-quality-switched.png` });

  if (autoOption) {
    await switchQuality(autoOption);
  }
  await waitFor(page, () => document.querySelector(".watch-quality-btn")?.textContent.trim().startsWith("Auto"), {
    label: "quality back to Auto",
  });
  check("player: quality menu returns to adaptive Auto", true, await qualityButton());

  // ---------- 4. Locked episode -> download the app ----------
  const callsBefore = playbackCalls.length;
  await page.click(".ep-btn.is-locked");
  await page.waitForSelector(".watch-overlay");
  const overlay = await page.$eval(".watch-overlay", (el) => ({
    text: el.textContent.replace(/\s+/g, " ").trim(),
    downloadHref: el.querySelector('a[href="#download"]')?.textContent.trim() ?? "",
  }));
  check("player: locked episode opens the app overlay", overlay.text.includes("is locked") && overlay.downloadHref.toLowerCase().includes("download"), overlay.text.slice(0, 120));
  check("player: locked episode never requested a playback URL", playbackCalls.length === callsBefore, JSON.stringify(playbackCalls));
  await page.screenshot({ path: `${SHOTS}/preview-05-player-locked.png` });

  // 4b. Auto-advance: playing the last free episode to its end must stop at the
  // locked one and open the same download overlay (never authorizing it).
  const lastFreeEp = detail.items.filter((e) => e.free).pop();
  const lastFreeId = lastFreeEp?.id;
  const previousSrc = await page.$eval("video", (v) => v.src).catch(() => "");
  await page.evaluate(() => {
    const freeButtons = [...document.querySelectorAll(".ep-btn.is-free")];
    freeButtons[freeButtons.length - 1].click();
  });
  await waitFor(
    page,
    (prev) => {
      const v = document.querySelector("video");
      return Boolean(v) && v.src !== prev && v.readyState >= 2 && v.currentTime > 0.2 && !v.paused;
    },
    { timeout: 40000, label: "last free episode playing fresh media", args: [previousSrc] },
  );
  const callsBeforeAdvance = playbackCalls.length;
  check("player: last free episode authorized", playbackCalls[callsBeforeAdvance - 1] === lastFreeId, `${JSON.stringify(playbackCalls)} vs ep#${lastFreeId}`);
  await page.evaluate(() => {
    const v = document.querySelector("video");
    if (v && Number.isFinite(v.duration)) v.currentTime = Math.max(0, v.duration - 0.4);
  });
  await waitFor(page, () => Boolean(document.querySelector(".watch-overlay")), { timeout: 30000, label: "auto-advance to locked episode" });
  const autoAdvanceText = await page.$eval(".watch-overlay h3", (el) => el.textContent.trim());
  check("player: auto-advance stops at the locked episode", autoAdvanceText.includes(`Episode ${lockedEp.episode_number}`), autoAdvanceText);
  check("player: auto-advance never authorized the locked episode", !playbackCalls.includes(lockedEp.id), JSON.stringify(playbackCalls));
  await page.screenshot({ path: `${SHOTS}/preview-08-auto-advance-locked.png` });

  await page.evaluate(() => document.querySelector('.watch-overlay a[href="#download"]').click());
  await waitFor(page, () => location.hash === "#download", { label: "hash #download" });
  await waitFor(page, () => {
    const el = document.getElementById("download");
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.top < window.innerHeight && r.bottom > 0;
  }, { label: "download section in view" });
  check("player: locked CTA lands on the app download section", true);
  await page.screenshot({ path: `${SHOTS}/preview-06-download-gate.png` });

  // ---------- 5. Preview "View all" -> download the app ----------
  await page.goto(`${BASE}/#preview`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".preview-row .text-link");
  await page.click(".preview-row .text-link");
  await waitFor(page, () => location.hash === "#download", { label: "view-all -> #download" });
  await waitFor(page, () => {
    const el = document.getElementById("download");
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.top < window.innerHeight && r.bottom > 0;
  }, { label: "view-all -> download section in view" });
  check("preview: View all in the app lands on the download section", true);

  // ---------- 6. Mobile layout ----------
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  await page.goto(`${BASE}/#preview`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".preview-card");
  const overflowPreview = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check("mobile: preview catalog has no horizontal overflow", overflowPreview <= 2, `overflow=${overflowPreview}px`);
  await page.screenshot({ path: `${SHOTS}/preview-09-mobile-catalog.png` });
  await page.goto(`${BASE}/#watch/15`, { waitUntil: "networkidle2" });
  await page.waitForSelector(".ep-grid");
  const overflowWatch = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check("mobile: player page has no horizontal overflow", overflowWatch <= 2, `overflow=${overflowWatch}px`);
  await page.screenshot({ path: `${SHOTS}/preview-10-mobile-player.png` });
  await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });

  // ---------- 7. API guard for locked episodes ----------
  const guard = await fetch(`${API}/v1/playback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ drama_id: target.id, episode_id: lockedEp?.id ?? -1 }),
  }).then((r) => r.json());
  check("api: locked episode is not authorized for guests", guard.allowed === false && !guard.url && !JSON.stringify(guard).includes("m3u8"), JSON.stringify(guard).slice(0, 120));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\\n${results.length - failed.length}/${results.length} checks passed`);
fs.writeFileSync(path.join(SHOTS, "preview-e2e-results.json"), JSON.stringify(results, null, 2));
process.exit(failed.length ? 1 : 0);
