// KON-124: headless-скриншоты прототипа (обе темы, 1280/1024).
// Запуск из корня репозитория: node scripts/kon124_shots.mjs
import { chromium } from "../qa/e2e/node_modules/playwright-core/index.mjs";

const base = "http://127.0.0.1:8777/variant_a.html";
const out = "design/KON-124/";
const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const shots = [
  { theme: "dark", w: 1280, file: "variant_a_dark_1280.png", hash: "" },
  { theme: "light", w: 1280, file: "variant_a_light_1280.png", hash: "" },
  { theme: "dark", w: 1024, file: "variant_a_dark_1024.png", hash: "#w1024" },
  { theme: "light", w: 1024, file: "variant_a_light_1024.png", hash: "#w1024" },
];
for (const s of shots) {
  const page = await browser.newPage({ viewport: { width: s.w, height: 900 } });
  await page.goto(base + s.hash);
  if (s.theme === "light") await page.evaluate(() => document.documentElement.dataset.theme = "light");
  await page.waitForTimeout(300);
  await page.screenshot({ path: out + s.file, fullPage: true });
  await page.close();
  console.log("ok", s.file);
}
await browser.close();
