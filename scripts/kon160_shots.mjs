// KON-160: headless-скриншоты вариантов тезисов (обе темы, 1280/1024).
// Запуск из корня репозитория: node scripts/kon160_shots.mjs
import { chromium } from "../qa/e2e/node_modules/playwright-core/index.mjs";

const base = "http://127.0.0.1:8777/";
const out = "design/KON-160_theses/";
const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
for (const v of ["a", "b"]) {
  for (const theme of ["dark", "light"]) {
    for (const w of [1280, 1024]) {
      const page = await browser.newPage({ viewport: { width: w, height: 900 } });
      const hash = w === 1024 ? "#w1024" : "";
      await page.goto(`${base}variant_${v}.html${hash}`);
      if (theme === "light") {
        await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
      }
      await page.waitForTimeout(250);
      await page.screenshot({ path: `${out}variant_${v}_${theme}_${w}.png`, fullPage: true });
      await page.close();
      console.log("ok", `variant_${v}_${theme}_${w}.png`);
    }
  }
}
// дополнительный кадр B: раскрытый источник (клик на первый тезис)
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${base}variant_b.html`);
  await page.waitForTimeout(200);
  await page.click("#list li:first-child");
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${out}variant_b_open_dark_1280.png`, fullPage: true });
  await page.close();
  console.log("ok", "variant_b_open_dark_1280.png");
}
await browser.close();
