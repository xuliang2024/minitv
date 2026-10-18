// Verifies the For You overlay: title and Episodes pill share one row, EP chip never wraps.
import { createRequire } from "node:module";

const require = createRequire("/opt/homebrew/lib/node_modules/");
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:5175";
const OUT = process.env.OUT_DIR || "/tmp/h5-meta";

const cases = [
  { width: 360, locale: "en" },
  { width: 390, locale: "en" },
  { width: 360, locale: "zh" },
  { width: 340, locale: "en" },
  { width: 320, locale: "zh" },
];

const browser = await chromium.launch({ channel: "chrome" });
const report = [];

for (const c of cases) {
  const ctx = await browser.newContext({
    viewport: { width: c.width, height: 780 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    userAgent:
      "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
  });
  const page = await ctx.newPage();
  await page.addInitScript((loc) => {
    localStorage.setItem("minitv_h5_locale", loc);
    if (!localStorage.getItem("minitv_h5_device")) {
      localStorage.setItem("minitv_h5_device", "e2e-meta-" + Math.random().toString(36).slice(2, 10));
    }
  }, c.locale);

  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('.tab-item, button[data-tab="foryou"]', { timeout: 15000 });
  await page.click('button[data-tab="foryou"]');
  await page.waitForSelector(".meta-row .select-pill", { timeout: 20000 });
  await page.waitForTimeout(1800);

  const data = await page.evaluate(() => {
    const row = document.querySelector(".meta-row");
    if (!row) return { error: "no .meta-row" };
    const h2 = row.querySelector(".meta h2");
    const pill = row.querySelector(".select-pill");
    const chip = row.querySelector(".ep-chip");
    const tagsEl = row.querySelector(".meta-tags");
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const lh = parseFloat(cs.lineHeight);
      return {
        top: Math.round(r.top),
        bottom: Math.round(r.bottom),
        left: Math.round(r.left),
        right: Math.round(r.right),
        w: Math.round(r.width),
        h: Math.round(r.height),
        text: (el.textContent || "").trim().slice(0, 40),
        lines: lh > 0 ? Math.round(r.height / lh) : 0,
        clipped: el.scrollWidth > el.clientWidth + 1,
        whiteSpace: cs.whiteSpace,
        flexWrap: cs.flexWrap,
      };
    };
    const rowBox = row.getBoundingClientRect();
    return {
      rowRect: { top: Math.round(rowBox.top), right: Math.round(rowBox.right), w: Math.round(rowBox.width) },
      h2: box(h2),
      pill: box(pill),
      chip: box(chip),
      tags: box(tagsEl),
      docScrollW: document.documentElement.scrollWidth,
      winW: window.innerWidth,
      rowOverflow: row.scrollWidth > row.clientWidth + 1,
    };
  });

  const issues = [];
  if (data.error) issues.push(data.error);
  else {
    if (data.docScrollW > data.winW + 1) issues.push(`horizontal overflow ${data.docScrollW}>${data.winW}`);
    if (data.rowOverflow) issues.push("meta-row content overflows");
    if (data.h2 && data.pill) {
      const vCenterH2 = (data.h2.top + data.h2.bottom) / 2;
      const vCenterPill = (data.pill.top + data.pill.bottom) / 2;
      // Pill must overlap the title vertically (same visual row band).
      const overlap = Math.min(data.h2.bottom, data.pill.bottom) - Math.max(data.h2.top, data.pill.top);
      if (overlap <= 0) issues.push(`title and pill not in same row band (overlap=${overlap})`);
      if (data.pill.left < data.h2.right - 1) issues.push("pill overlaps the title text area");
    }
    if (data.chip) {
      if (data.chip.lines > 1) issues.push(`ep-chip wrapped into ${data.chip.lines} lines`);
      if (data.chip.clipped) issues.push("ep-chip text clipped");
      if (data.chip.whiteSpace !== "nowrap") issues.push(`ep-chip white-space=${data.chip.whiteSpace}`);
      if (data.pill && data.pill.clipped) issues.push("Episodes pill text clipped");
    }
    if (data.h2 && data.h2.lines > 2) issues.push(`title clamped to >2 lines (${data.h2.lines})`);
  }

  const file = `${OUT}-${c.locale}-${c.width}.png`;
  await page.screenshot({ path: file });
  report.push({ ...c, issues, data, file });
  await ctx.close();
}

await browser.close();
console.log(JSON.stringify(report, null, 2));
const bad = report.filter((r) => r.issues.length);
console.log(bad.length ? `\nFAIL: ${bad.length}/${report.length}` : `\nOK: ${report.length}/${report.length}`);
process.exit(bad.length ? 1 : 0);
