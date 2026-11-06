// KON-161 (Pre.QA-15, FR-041/UC-ROS): приёмка окна «ROS специалиста» и вкладки «ROS» в Настройках
// на dev-сервере vite с замоканным API. Запуск: vite на :5199 (cd frontend && npx vite --port 5199),
// затем node qa/e2e/ui_ros.mjs. Секретов нет: API целиком подменён page.route, backend не используется.
// Эталон критериев: design/KON-161_ros/decision.md (approved) + FR-041 (контрольный пример 24,80%/27,32%).
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "ui_ros");
mkdirSync(OUT, { recursive: true });
const BASE = process.env.UI_BASE || "http://localhost:5199";
const STAMP = { scope: "needs", state: "ok", stale: false, paused: false, code: "", message: "", interval_sec: 300 };
const ROS_DEFAULT = { billable_hours: 165, indirect_cost: 53000, profit_tax_rate: 5, vat_rate: 22,
  insurance_before: 15.2, insurance_after: 7.8, fot_limit: 2979000 };
const need = (r) => ({ jira_key: "AN-5919", title: "Аналитик 1С", status: r.status || "Поиск кандидата", epic_key: null,
  rate: { amount: r.amount, currency: r.currency, includes_vat: r.includes_vat, proposer: r.proposer, unit: r.unit },
  jira_created_at: "2026-10-01T08:30:00Z", jira_updated_at: "2026-10-09T09:00:00Z" });

// изменяемый мок-стейт: роль входа и параметры ROS
const state = {
  me: { login: "test.user", display_name: "Тест Пользователь", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" },
  ros: { ...ROS_DEFAULT },
  needRate: { amount: 2200, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "час", status: "Поиск кандидата" },
  puts: [],
  gets: 0,
};

const results = [];
const check = (name, ok, extra = "") => { results.push({ name, ok: !!ok, extra }); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? " :: " + String(extra).slice(0, 160) : "")); };

function setup(page) {
  return page.route((u) => new URL(u).pathname.startsWith("/api/"), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace(/^\/api/, "");
    const json = (b, s = 200) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(b) });
    if (req.method() === "GET") state.gets += 1;
    if (req.method() === "PUT") state.puts.push({ path: p, body: req.postData() ? JSON.parse(req.postData()) : null });
    if (p === "/auth/me") return json(state.me);
    if (p === "/needs/match-counts") return json({ counts: {} });
    if (p === "/sync/needs/groups") return json({ groups: [{ key: "all", id: null, name: "Все статусы", count: 1 }], statuses: [], sync: STAMP });
    if (p === "/sync/needs") return json({ items: [need(state.needRate)], total: 1, limit: 100, offset: 0, sync: STAMP });
    if (p === "/admin/ros/settings") {
      if (req.method() === "PUT") state.ros = { ...state.ros, ...state.puts.at(-1).body };
      return json(state.ros);
    }
    return json({ detail: "не найдено" }, 404);
  });
}

async function openNeeds(page) {
  await page.goto(BASE + "/needs");
  await page.getByRole("button", { name: "Сопоставить AN-5919" }).waitFor();
}

async function openRos(page) {
  await page.getByRole("button", { name: "ROS AN-5919" }).click();
  const dlg = page.getByRole("dialog", { name: "ROS специалиста" });
  await dlg.getByLabel(/Ставка для расчёта/).waitFor();
  return dlg;
}

const browser = await chromium.launch();
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}`;
  const ctx = await browser.newContext({ viewport: { width, height: 860 }, colorScheme: theme, locale: "ru-RU" });
  try {
    // A. Клиентская ставка: read-only, подстановка, контрольный пример FR-041
    {
      state.ros = { ...ROS_DEFAULT };
      state.needRate = { amount: 2200, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "час", status: "Поиск кандидата" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      check(`[${tag}] A: кнопка ROS контурная под «Сопоставить»`, await page.getByRole("button", { name: "ROS AN-5919" }).isVisible());
      const putsBefore = state.puts.length;
      const dlg = await openRos(page);
      check(`[${tag}] A: шапка — ключ и клиент`, /AN-5919/.test(await dlg.locator(".modal__meta").innerText()), await dlg.locator(".modal__meta").innerText());
      const rate = dlg.getByLabel(/Ставка для расчёта/);
      check(`[${tag}] A: ставка read-only при «задана клиентом»`, await rate.isDisabled() || (await rate.getAttribute("readonly")) !== null);
      check(`[${tag}] A: подставлен 2 200`, /2[\u00A0 ]200/.test(await rate.inputValue()), await rate.inputValue());
      await dlg.getByLabel(/ЗП кандидата/).fill("130 000");
      const t0 = Date.now();
      await dlg.getByText("24,80%").waitFor({ timeout: 300 });
      check(`[${tag}] A: пересчёт ≤ 0,3 с`, Date.now() - t0 <= 300);
      check(`[${tag}] A: два значения ROS (до/после)`, await dlg.getByText("24,80%").isVisible() && await dlg.getByText("27,32%").isVisible());
      check(`[${tag}] A: «до лимита» — акцентная карточка главная`, (await dlg.locator(".ros-card--main .ros-card__num").innerText()) === "24,80%");
      check(`[${tag}] A: прибыль 90 041,93 / 99 180,93`, /90.041,93/.test(await dlg.innerText()) && /99.180,93/.test(await dlg.innerText()));
      check(`[${tag}] A: строка перехода — с 23-го месяца`, /с 23-го месяца/.test(await dlg.innerText()));
      check(`[${tag}] A: блока «Детали расчёта» нет`, !(await dlg.getByText(/Детали расчёта/).count()));
      check(`[${tag}] A: приватность — только GET`, state.puts.length === putsBefore, `изменяющих запросов за окно: ${state.puts.length - putsBefore}`);
      await page.screenshot({ path: `${OUT}/ros_client_${tag}.png` });
      await page.close();
    }
    // B. Статус «Кандидат назначен» — кнопка скрыта
    {
      state.needRate = { ...state.needRate, status: "Кандидат назначен" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      check(`[${tag}] B: «Кандидат назначен» — ROS скрыта`, !(await page.getByRole("button", { name: "ROS AN-5919" }).count()));
      state.needRate = { ...state.needRate, status: "Отказ по кандидату" };
      await page.reload();
      await page.getByRole("button", { name: "Сопоставить AN-5919" }).waitFor();
      check(`[${tag}] B: вне матрицы («Отказ по кандидату») — ROS скрыта`, !(await page.getByRole("button", { name: "ROS AN-5919" }).count()));
      state.needRate = { ...state.needRate, status: "Поиск кандидата" };
      await page.close();
    }
    // C. «от производства»: ручной ввод ставки, справочно
    {
      state.needRate = { amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час", status: "Поиск кандидата" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      const dlg = await openRos(page);
      const rate = dlg.getByLabel(/Ставка для расчёта/);
      check(`[${tag}] C: поле активно и пусто`, await rate.isEditable() && (await rate.inputValue()) === "");
      check(`[${tag}] C: значение потребности справочно`, /В потребности: 3[\u00A0 ]000 RUB \/ час/.test(await dlg.innerText()), await dlg.innerText());
      await rate.fill("2 200");
      await dlg.getByLabel(/ЗП кандидата/).fill("130 000");
      check(`[${tag}] C: ручной ввод — тот же контрольный пример`, await dlg.getByText("24,80%").isVisible());
      await page.screenshot({ path: `${OUT}/ros_manual_${tag}.png` });
      await page.close();
    }
    // D. Валюта/единица не руб./час — ручной ввод, значение справочно
    {
      state.needRate = { amount: 50, currency: "USD", includes_vat: null, proposer: "от клиента", unit: "час", status: "Поиск кандидата" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      const dlg = await openRos(page);
      const rate = dlg.getByLabel(/Ставка для расчёта/);
      check(`[${tag}] D: поле пустое для ручного ввода`, await rate.isEditable() && (await rate.inputValue()) === "");
      check(`[${tag}] D: справочно 50 USD / час`, /50 USD \/ час/.test(await dlg.innerText()));
      check(`[${tag}] D: без подсказки приведения`, !(await dlg.locator(".ros__hint").allInnerTexts()).join("|").includes("приведена"));
      await rate.fill("2 200");
      await dlg.getByLabel(/ЗП кандидата/).fill("130 000");
      check(`[${tag}] D: расчёт по введённой ставке`, await dlg.getByText("24,80%").isVisible());
      await page.screenshot({ path: `${OUT}/ros_usd_${tag}.png` });
      await page.close();
    }
    // E. Валидации (UC-ROS 5а/5в)
    {
      state.needRate = { amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час", status: "Поиск кандидата" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      const dlg = await openRos(page);
      await dlg.getByLabel(/ЗП кандидата/).fill("0");
      check(`[${tag}] E: ЗП 0 -> «Введите ЗП числом больше нуля»`, /Введите ЗП числом больше нуля/.test(await dlg.innerText()));
      check(`[${tag}] E: пересчёт заблокирован («—»)`, (await dlg.innerText()).includes("—"));
      await dlg.getByLabel(/Ставка для расчёта/).fill("2000000");
      check(`[${tag}] E: ставка > 1e6 -> «Ставка не может превышать 1 000 000»`, /Ставка не может превышать 1[\u00A0 ]000[\u00A0 ]000/.test(await dlg.innerText()));
      await page.screenshot({ path: `${OUT}/ros_err_${tag}.png` });
      await page.close();
    }
    // F2. Настройки: вкладка ROS (только админ сохраняет) — 7 полей, PUT цельным телом
    {
      state.me = { ...state.me, role: "admin" };
      state.ros = { ...ROS_DEFAULT };
      const page = await ctx.newPage();
      setup(page);
      await page.goto(BASE + "/settings");
      await page.getByRole("tab").first().waitFor();
      const tabs = page.getByRole("tab");
      const titles = await tabs.allInnerTexts();
      check(`[${tag}] F: вкладка ROS после «Лимит файла»`, titles.indexOf("ROS") === titles.indexOf("Лимит файла") + 1, titles.join(" | "));
      await page.getByRole("tab", { name: "ROS" }).click();
      const card = page.locator(".group-card");
      await card.getByLabel(/Billable часов/).waitFor();
      const num = async (label) =>
        Number((await card.getByLabel(label).inputValue()).replace(/[\u00A0 ]/g, "").replace(",", "."));
      const n1 = await num(/Billable/), n2 = await num(/Косвенные/), n3 = await num(/налога/),
            n4 = await num(/Ставка НДС/), n5 = await num(/до лимита/), n6 = await num(/после лимита/),
            n7 = await num(/Лимит ФОТ/);
      check(`[${tag}] F: 7 полей по умолчанию`, n1 === 165 && n2 === 53000 && n3 === 5 &&
            n4 === 22 && n5 === 15.2 && n6 === 7.8 && n7 === 2979000,
            [n1, n2, n3, n4, n5, n6, n7].join(", "));
      await card.getByLabel(/Ставка НДС/).fill("20");
      await card.getByRole("button", { name: "Сохранить" }).click();
      await card.getByText(/Сохранено: значения общие/).waitFor();
      const put = state.puts.at(-1);
      check(`[${tag}] F: PUT цельным телом (7 полей)`, !!put && Object.keys(put.body).length === 7 && put.body.vat_rate === 20, JSON.stringify(put?.body));
      await page.screenshot({ path: `${OUT}/settings_ros_saved_${tag}.png` });
      await page.close();
    }
    // F3. Окно ROS считает с изменёнными параметрами: НДС 20% -> 3 000 без НДС -> 3 600 (x 1,20)
    {
      state.needRate = { amount: 3000, currency: "RUB", includes_vat: false, proposer: "от клиента", unit: "час", status: "Поиск кандидата" };
      const page = await ctx.newPage();
      setup(page);
      await openNeeds(page);
      const dlg = await openRos(page);
      const rate = dlg.getByLabel(/Ставка для расчёта/);
      check(`[${tag}] F3: 3 000 без НДС -> 3 600 (x 1,20)`, /3[\u00A0 ]600/.test(await rate.inputValue()), await rate.inputValue());
      check(`[${tag}] F3: подсказка x 1,20`, /× 1,20/.test(await dlg.locator(".ros__hint").innerText()));
      await dlg.getByLabel(/ЗП кандидата/).fill("130 000");
      // не падаем: значения изменились относительно эталона, проверяем только пересчёт
      await page.screenshot({ path: `${OUT}/ros_vat20_${tag}.png` });
      await page.close();
      state.me = { ...state.me, role: "user" };
    }
  } catch (e) {
    check(`[${tag}] SCRIPT-ОШИБКА`, false, String(e).slice(0, 200));
  } finally {
    await ctx.close();
  }
}
await browser.close();
writeFileSync(resolve(OUT, "results.json"), JSON.stringify(results, null, 2));
const fails = results.filter((r) => !r.ok);
console.log(`\nИТОГО: ${results.length} проверок, не пройдено ${fails.length}`);
for (const f of fails) console.log("  FAIL:", f.name, "::", f.extra);
process.exit(fails.length ? 1 : 0);
