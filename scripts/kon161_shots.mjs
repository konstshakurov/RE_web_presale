// KON-161: headless-скриншоты вариантов ROS (обе темы, 1280/1024).
// Запуск из корня репозитория: node scripts/kon161_shots.mjs
import { chromium } from "../qa/e2e/node_modules/playwright-core/index.mjs";

const base = "http://127.0.0.1:8777/";
const out = "design/KON-161_ros/";
const browser = await chromium.launch({
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
for (const v of ["a", "b"]) {
  for (const theme of ["dark", "light"]) {
    for (const w of [1280, 1024]) {
      const page = await browser.newPage({ viewport: { width: w, height: 1000 } });
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
await browser.close();
