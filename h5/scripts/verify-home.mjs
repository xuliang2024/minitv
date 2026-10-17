import { createRequire } from "node:module";
const require = createRequire("/opt/homebrew/lib/node_modules/");
const { chromium } = require("playwright");

const URL = "http://127.0.0.1:5175/";
const OUT = "/tmp/h5v";

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
});

const errors = [];
const notFound = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text().slice(0, 160));
});
page.on("pageerror", (err) => errors.push(String(err).slice(0, 160)));
page.on("response", (res) => {
  if (res.status() >= 400) notFound.push(`${res.status()} ${res.url().slice(0, 120)}`);
});

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForSelector(".poster img", { timeout: 15000 });

const home = await page.evaluate(() => ({
  hero: document.querySelectorAll(".banner-slide").length,
  heroLoaded: Array.from(document.querySelectorAll(".banner-slide img")).filter((i) => i.naturalWidth > 0).length,
  floors: Array.from(document.querySelectorAll(".floor-head span")).map((el) => el.textContent),
  posters: document.querySelectorAll(".poster, .land").length,
  postersLoaded: Array.from(document.querySelectorAll(".poster img, .land img")).filter((i) => i.naturalWidth > 0).length,
  empty: /No more content|没有更多/.test(document.body.innerText),
  moreButtons: document.querySelectorAll(".floor-head button").length,
}));
console.log("HOME", JSON.stringify(home));

await page.screenshot({ path: `${OUT}-home.png` });
await page.evaluate(() => document.querySelector(".stage")?.scrollBy(0, 620));
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}-home-mid.png` });
await page.evaluate(() => document.querySelector(".stage")?.scrollTo(0, 0));
await page.waitForTimeout(300);

// Chinese locale
await page.locator(".topbar .icon-btn", { hasText: "中" }).click();
await page.waitForTimeout(1200);
const zh = await page.evaluate(() => ({
  channel: Array.from(document.querySelectorAll(".channel button")).map((el) => el.textContent),
  floors: Array.from(document.querySelectorAll(".floor-head span")).map((el) => el.textContent),
  chip: document.querySelector(".chip")?.textContent,
}));
console.log("ZH", JSON.stringify(zh));
await page.screenshot({ path: `${OUT}-home-zh.png` });

// back to English, then open the hero -> play page
await page.locator(".topbar .icon-btn", { hasText: "EN" }).click();
await page.waitForTimeout(800);
await page.locator(".banner-slide").first().click();
await page.waitForTimeout(1200);

const playing = await page.evaluate(async () => {
  const video = document.querySelector("video");
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (video && video.currentTime > 0.4) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  return {
    currentTime: video?.currentTime ?? null,
    duration: video?.duration ?? null,
    paused: video?.paused ?? null,
    width: video?.videoWidth ?? null,
    epBar: document.querySelector(".ep-bar")?.textContent ?? null,
    title: document.querySelector(".meta h2")?.textContent ?? null,
    synLines: document.querySelector(".syn")?.getBoundingClientRect().height ?? null,
  };
});
console.log("PLAY", JSON.stringify(playing));

await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}-play.png` });

// open the episode sheet
await page.locator(".ep-bar").click();
await page.waitForTimeout(600);
const sheet = await page.evaluate(() => ({
  open: !!document.querySelector(".sheet"),
  cells: document.querySelectorAll(".ep-grid button").length,
  current: document.querySelector(".ep-grid button.on")?.textContent ?? null,
}));
console.log("SHEET", JSON.stringify(sheet));
await page.screenshot({ path: `${OUT}-sheet.png` });

console.log("404s", JSON.stringify(notFound.slice(0, 6)));
console.log("errors", JSON.stringify(errors.slice(0, 6)));

await browser.close();
