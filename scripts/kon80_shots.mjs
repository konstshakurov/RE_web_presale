// KON-80: headless-скриншоты обоих вариантов (обе темы, 1280/1024, состояния).
// Запуск из корня репозитория: node scripts/kon80_shots.mjs
import { chromium } from "../qa/e2e/node_modules/playwright-core/index.mjs";

const base = "http://127.0.0.1:8777/";
const out = "design/KON-80/";
const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const shots = [];
for (const v of ["a", "b"]) {
  for (const theme of ["dark", "light"]) {
    for (const w of [1280, 1024]) {
      shots.push({ variant: v, theme, w, file: `variant_${v}_${theme}_${w}.png` });
    }
  }
}
for (const s of shots) {
  const page = await browser.newPage({ viewport: { width: s.w, height: 1100 } });
  const hash = s.w === 1024 ? "#w1024" : "";
  await page.goto(`${base}variant_${s.variant}.html${hash}`);
  if (s.theme === "light") {
    await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  }
  await page.waitForTimeout(250);
  await page.screenshot({ path: out + s.file, fullPage: true });
  await page.close();
  console.log("ok", s.file);
}
await browser.close();
