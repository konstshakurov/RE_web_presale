// KON-114 (Pre.QA): приёмка UI редактора заметки (KON-115 вариант B) на стенде :8080, API замокан page.route.
// Запуск: node qa/e2e/ui_kon114.mjs (UI_BASE по умолчанию http://127.0.0.1:8080). Секретов нет, Jira не используется.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "ui_kon114");
mkdirSync(OUT, { recursive: true });
const BASE = process.env.UI_BASE || "http://127.0.0.1:8080";
const STAMP = (s) => ({ scope: s, state: "ok", stale: false, paused: false, code: "", message: "", interval_sec: 300 });
const ME = { login: "test.user", display_name: "Тест Пользователь", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };
const LONG = Array.from({ length: 40 }, (_, i) => `Строка ${i + 1}: ограничения по командировкам и проектам банков`).join("\n").slice(0, 2000);
const EMPS = () => [
  { jira_key: "EMP-1", full_name: "Иванова Анна Петровна", status: "Свободен", assignable: true, note: "Не подключать к проектам банков.\nКомандировки - только по согласованию.", note_author: "ivanova.a", note_updated_at: "2026-10-20T09:05:00Z", synced_at: "2026-10-20T10:00:00Z" },
  { jira_key: "EMP-2", full_name: "Петров Пётр", status: "Свободен", assignable: true, note: null, note_author: null, note_updated_at: null, synced_at: "2026-10-20T10:00:00Z" },
  { jira_key: "EMP-3", full_name: "Сидоров Сидор Сидорович", status: "На проекте", assignable: false, note: LONG, note_author: "sidorov.s", note_updated_at: "2026-10-19T15:30:00Z", synced_at: "2026-10-20T10:00:00Z" },
];
const results = [];
const check = (name, ok, extra = "") => { results.push({ name, ok: !!ok, extra }); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? " :: " + extra : "")); };

async function setup(page, replies = []) {
  const puts = [];
  const emps = EMPS();
  await page.route((u) => u.pathname.startsWith("/api/"), async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^\/api/, "");
    const json = (b, s = 200) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(b) });
    if (p === "/auth/me") return json(ME);
    if (p === "/sync/employees") return json({ items: emps, total: emps.length, limit: 500, offset: 0, statuses: [{ status: "Свободен", count: 2 }, { status: "На проекте", count: 1 }], sync: STAMP("employees") });
    if (p === "/sync/needs/groups") return json({ groups: [], statuses: [], sync: STAMP("needs") });
    if (p === "/cv/files") return json({ items: [] });
    if (p === "/cv/limits") return json({ max_file_mb: 10, max_file_bytes: 10485760, upload_kinds: {}, extensions_by_kind: {} });
    if (p === "/resume/technologies") return json([]);
    if (p === "/resume/employees") return json({ items: [], total: 0, limit: 200, offset: 0 });
    if (p === "/llm/providers") return json([]);
    const m = p.match(/^\/cv\/employees\/(EMP-\d)\/note$/);
    if (m && req.method() === "PUT") {
      const body = JSON.parse(req.postData() || "{}");
      puts.push({ key: m[1], body });
      const rp = replies.length > 1 ? replies.shift() : replies[0];
      if (rp?.delay) await new Promise((r) => setTimeout(r, rp.delay));
      if (rp?.status && rp.status !== 200) return json({ detail: rp.detail }, rp.status);
      const note = body.note.trim() || null;
      return json({ note, note_author: "test.user", note_updated_at: "2026-10-21T08:15:00Z" });
    }
    return json({ detail: "не найдено" }, 404);
  });
  return puts;
}
async function open(page, name = /Изменить заметку: Иванова/) {
  await page.goto(BASE + "/resumes");
  const btn = page.getByRole("button", { name });
  await btn.waitFor();
  await btn.click();
  const dlg = page.getByRole("dialog");
  await dlg.waitFor();
  return dlg;
}
const lum = (c) => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const TA = (dlg) => dlg.getByLabel("Заметка", { exact: true });
const fillText = (dlg, t) => TA(dlg).fill(t);

const browser = await chromium.launch();
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}`;
  const ctx = await browser.newContext({ viewport: { width, height: 800 }, colorScheme: theme, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  const shot = (page, n) => page.screenshot({ path: `${OUT}/${n}_${tag}.png` });
  const lastCell = (page, who) => page.locator("tr", { hasText: who }).locator("td").last();
  try {
    // П.1, 3: таблица
    {
      const page = await ctx.newPage();
      await setup(page, [{}]);
      await page.goto(BASE + "/resumes");
      await page.getByRole("button", { name: /Изменить заметку: Иванова/ }).waitFor();
      const btns = page.getByRole("button", { name: /^Изменить заметку: / });
      const n = await btns.count();
      let allEn = n === 3; for (let i = 0; i < n; i++) allEn = allEn && (await btns.nth(i).isEnabled());
      check(`[${tag}] п.1 «Изменить» активна в каждой строке (3)`, allEn, String(n));
      console.log("INFO п.1 видимый текст кнопок:", JSON.stringify(await page.locator("[data-note-key]").allInnerTexts()), "title:", await page.locator("[data-note-key]").first().getAttribute("title"));
      const cell = await lastCell(page, "Иванова").innerText();
      check(`[${tag}] п.3 в ячейке нет автора и даты`, !/ivanova|20\.10|12:05|Изменена/.test(cell), JSON.stringify(cell));
      const cell2 = await lastCell(page, "Петров").innerText();
      check(`[${tag}] п.1 пустая заметка: «нет заметки»`, cell2.includes("нет заметки"), JSON.stringify(cell2));
      await shot(page, "table");
      await page.close();
    }
    // П.2, 4, 5, 6: открытие, подсказка, счётчик
    {
      const page = await ctx.newPage();
      await setup(page, [{}]);
      const dlg = await open(page);
      check(`[${tag}] п.2 role=dialog aria-modal`, (await dlg.getAttribute("aria-modal")) === "true");
      const geo = await dlg.evaluate((el) => ({ w: Math.round(el.getBoundingClientRect().width), meta: getComputedStyle(el.querySelector(".modal__meta")).color, muted: (() => { const t = document.createElement("i"); t.style.color = "var(--muted)"; document.body.appendChild(t); const c = getComputedStyle(t).color; t.remove(); return c; })() }));
      check(`[${tag}] §3 ширина окна min(560px,100%)`, geo.w <= 561, String(geo.w));
      check(`[${tag}] §3 подзаголовок цвета --muted`, geo.meta === geo.muted, `${geo.meta} vs ${geo.muted}`);
      check(`[${tag}] п.2 заголовок`, (await dlg.getByRole("heading").innerText()) === "Заметка: Иванова Анна Петровна");
      const meta = await dlg.locator(".modal__meta").innerText();
      check(`[${tag}] п.2 подзаголовок «Изменена: логин, дд.мм.гг, чч:мм»`, meta === "Изменена: ivanova.a, 20.10.26, 12:05", meta);
      const ta = TA(dlg);
      const foc = await ta.evaluate((a) => ({ f: document.activeElement === a, s: a.selectionStart, e: a.selectionEnd, l: a.value.length }));
      check(`[${tag}] п.2 фокус в поле, курсор в конце`, foc.f && foc.s === foc.l && foc.e === foc.l, JSON.stringify(foc));
      check(`[${tag}] п.2 placeholder`, (await ta.getAttribute("placeholder")) === "Например: ограничения по проектам или командировкам");
      check(`[${tag}] п.4 подсказка`, (await dlg.innerText()).includes("Учитывается при сопоставлении с потребностью"));
      const cn = (await dlg.locator("#note-cnt").innerText()).trim();
      check(`[${tag}] п.5 счётчик «N / 2000»`, /^\d+ \/ 2000$/.test(cn), cn);
      const save = dlg.getByRole("button", { name: "Сохранить" });
      check(`[${tag}] п.6 «Сохранить» disabled при неизменённом`, await save.isDisabled());
      await page.keyboard.press("Control+Enter");
      await page.waitForTimeout(200);
      check(`[${tag}] п.6 Ctrl+Enter при неизменённом ничего не делает (окно открыто)`, await dlg.isVisible());
      await shot(page, "open");
      await ta.press("End"); await page.keyboard.type(" Правка");
      check(`[${tag}] п.6 после правки «Сохранить» активна`, await save.isEnabled());
      await shot(page, "edit");
      for (const [len, over, canSave] of [[1900, false, true], [1962, false, true], [2000, false, true], [2001, true, false]]) {
        await fillText(dlg, "я".repeat(len));
        const cnt = dlg.locator("#note-cnt");
        const cls = await cnt.getAttribute("class");
        const fw = await cnt.evaluate((e) => getComputedStyle(e).fontWeight);
        const hint = await dlg.locator("#note-hint").innerText();
        check(`[${tag}] п.5 ${len}: ${over ? "красный, подсказка заменена, Сохранить disabled" : "не превышение, Сохранить активна"}`,
          (cls.includes("is-over") === over) && ((await save.isEnabled()) === canSave) && (over ? hint === "Сократите текст на 1 знак" : hint === "Учитывается при сопоставлении с потребностью"), `${cls} / ${hint}`);
        if (len === 1900 || len === 1962) check(`[${tag}] п.5 ${len}: счётчик жирный`, Number(fw) >= 600, fw);
        if (len === 1962) await shot(page, "limit");
      }
      await fillText(dlg, "я".repeat(1899));
      check(`[${tag}] п.5 при 1899 не жирный`, Number(await dlg.locator("#note-cnt").evaluate((e) => getComputedStyle(e).fontWeight)) < 600);
      for (const [ex, word] of [[1, "знак"], [2, "знака"], [5, "знаков"], [11, "знаков"], [21, "знак"], [4, "знака"], [12, "знаков"], [22, "знака"]]) {
        await fillText(dlg, "я".repeat(2000 + ex));
        const hint = await dlg.locator("#note-hint").innerText();
        check(`[${tag}] п.5 превышение на ${ex}: «${word}»`, hint === `Сократите текст на ${ex} ${word}`, hint);
      }
      await fillText(dlg, "я".repeat(2011));
      const border = await ta.evaluate((e) => getComputedStyle(e).borderTopColor);
      const errc = await dlg.locator("#note-hint").evaluate((e) => getComputedStyle(e).color);
      check(`[${tag}] п.5 рамка поля цвета --err`, border === errc, `${border} vs ${errc}`);
      await shot(page, "over");
      await fillText(dlg, "😀".repeat(2000));
      check(`[${tag}] п.5 эмодзи: 2000 кодпоинтов = «2000 / 2000», Сохранить активна`, ((await dlg.locator("#note-cnt").innerText()).trim() === "2000 / 2000") && (await save.isEnabled()));
      await page.close();
    }
    // П.2: пусто, Esc, ×, Отмена, фокус
    {
      const page = await ctx.newPage();
      await setup(page, [{}]);
      const dlg = await open(page, /Изменить заметку: Петров/);
      check(`[${tag}] п.2 пусто: «Заметки пока нет»`, (await dlg.locator(".modal__meta").innerText()) === "Заметки пока нет");
      check(`[${tag}] п.8 «Очистить» disabled при пустом поле`, await dlg.getByRole("button", { name: "Очистить", exact: true }).isDisabled());
      await shot(page, "empty");
      await page.keyboard.press("Escape");
      await dlg.waitFor({ state: "detached" });
      const f1 = await page.evaluate(() => document.activeElement?.getAttribute("data-note-key"));
      check(`[${tag}] п.2 Esc закрывает, фокус на «Изменить» строки`, f1 === "EMP-2", String(f1));
      await page.locator('[data-note-key="EMP-2"]').click();
      await page.getByRole("dialog").locator(".modal__x").click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      const f2 = await page.evaluate(() => document.activeElement?.getAttribute("data-note-key"));
      check(`[${tag}] п.2 × закрывает, фокус на «Изменить»`, f2 === "EMP-2", String(f2));
      await page.locator('[data-note-key="EMP-2"]').click();
      await page.getByRole("dialog").getByRole("button", { name: "Отмена" }).click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      check(`[${tag}] п.2 «Отмена» закрывает`, true);
      await page.close();
    }
    // П.7, 3, 11, 12: успех
    {
      const page = await ctx.newPage();
      const puts = await setup(page, [{}]);
      const dlg = await open(page, /Изменить заметку: Петров/);
      await fillText(dlg, "Первая строка\nВторая строка");
      await page.keyboard.press("Control+Enter");
      await dlg.waitFor({ state: "detached" });
      check(`[${tag}] п.7 Ctrl+Enter: PUT с введённым текстом`, puts.length === 1 && puts[0].body.note === "Первая строка\nВторая строка", JSON.stringify(puts));
      const cell = await lastCell(page, "Петров").innerText();
      check(`[${tag}] п.7 в строке новый текст, п.3 без автора/даты/«Сохранено»`, cell.includes("Первая строка") && !/test\.user|21\.10|11:15|Сохранено|очищена/.test(cell), JSON.stringify(cell));
      const ws = await lastCell(page, "Петров").locator(".note").evaluate((e) => getComputedStyle(e).whiteSpace);
      check(`[${tag}] п.7 переносы строк сохраняются (white-space)`, ws === "pre-wrap", ws);
      const st = await page.getByRole("status").innerText();
      check(`[${tag}] п.12 role=status «Сохранено»`, st === "Сохранено", st);
      check(`[${tag}] п.12 визуально скрыт`, await page.locator(".sr-status").evaluate((e) => { const r = e.getBoundingClientRect(); return r.width <= 1 && r.height <= 1; }));
      const f = await page.evaluate(() => document.activeElement?.getAttribute("data-note-key"));
      check(`[${tag}] п.2 фокус после сохранения на «Изменить» строки`, f === "EMP-2", String(f));
      await shot(page, "saved");
      await page.locator('[data-note-key="EMP-2"]').click();
      const d2 = page.getByRole("dialog");
      const meta = await d2.locator(".modal__meta").innerText();
      check(`[${tag}] п.7 повторное открытие: автор и дата из ответа`, meta === "Изменена: test.user, 21.10.26, 11:15", meta);
      await TA(d2).fill("x");
      await page.mouse.click(5, 5);
      await page.waitForTimeout(250);
      check(`[${tag}] п.11 подложка при изменённом тексте не закрывает`, await d2.isVisible());
      await TA(d2).fill("Первая строка\nВторая строка");
      await page.mouse.click(5, 5);
      await d2.waitFor({ state: "detached" });
      check(`[${tag}] п.11 подложка при неизменённом закрывает`, true);
      await page.close();
    }
    // П.8: очистка
    {
      const page = await ctx.newPage();
      const puts = await setup(page, [{}]);
      const dlg = await open(page);
      await dlg.getByRole("button", { name: "Очистить", exact: true }).click();
      check(`[${tag}] п.8 «Очистить» стирает поле без запроса`, (await TA(dlg).inputValue()) === "" && puts.length === 0);
      check(`[${tag}] п.8 фокус в поле`, await TA(dlg).evaluate((a) => document.activeElement === a));
      const danger = dlg.getByRole("button", { name: "Очистить заметку" });
      check(`[${tag}] п.8 главная кнопка «Очистить заметку»`, await danger.isVisible());
      const cl = await dlg.evaluate((el) => { const c = (v) => { const t = document.createElement("i"); t.style.color = `var(${v})`; document.body.appendChild(t); const r = getComputedStyle(t).color; t.remove(); return r; }; const st = (s) => { const b = el.querySelector(s); const x = getComputedStyle(b); return { color: x.color, border: x.borderTopWidth, bg: x.backgroundColor }; }; return { err: c("--err"), muted: c("--muted"), danger: st(".note-dlg__danger"), clear: st(".note-dlg__clear") }; });
      check(`[${tag}] п.8 «Очистить заметку» текст --err`, cl.danger.color === cl.err, `${cl.danger.color} vs ${cl.err}`);
      check(`[${tag}] §3 «Очистить» текстовая кнопка --muted без рамки`, cl.clear.color === cl.muted && cl.clear.border === "0px", JSON.stringify(cl.clear));
      console.log(`INFO [${tag}] цвет «Очистить заметку» ${await danger.evaluate((e) => getComputedStyle(e).color)}`);
      check(`[${tag}] п.8 «Очистить» disabled при пустом поле`, await dlg.getByRole("button", { name: "Очистить", exact: true }).isDisabled());
      await shot(page, "clear");
      await danger.click();
      await dlg.waitFor({ state: "detached" });
      check(`[${tag}] п.8 PUT с пустой строкой`, puts.length === 1 && puts[0].body.note === "", JSON.stringify(puts));
      const cell = await lastCell(page, "Иванова").innerText();
      check(`[${tag}] п.8 в ячейке «нет заметки», без пометок`, cell.includes("нет заметки") && !/очищена|ivanova|test\.user/.test(cell), JSON.stringify(cell));
      check(`[${tag}] п.12 status «Заметка очищена»`, (await page.getByRole("status").innerText()) === "Заметка очищена");
      await shot(page, "cleared");
      await page.locator('[data-note-key="EMP-1"]').click();
      check(`[${tag}] п.8 повторное открытие: «Заметки пока нет»`, (await page.getByRole("dialog").locator(".modal__meta").innerText()) === "Заметки пока нет");
      await page.close();
    }
    // П.9: сохранение
    {
      const page = await ctx.newPage();
      await setup(page, [{ delay: 1500 }]);
      const dlg = await open(page);
      await fillText(dlg, "Новая");
      await dlg.getByRole("button", { name: "Сохранить" }).click();
      await dlg.getByText("Сохраняем…").waitFor();
      check(`[${tag}] п.9 поле только для чтения`, await TA(dlg).evaluate((a) => a.readOnly));
      const dis = await Promise.all([dlg.getByRole("button", { name: "Сохранить" }).isDisabled(), dlg.getByRole("button", { name: "Отмена" }).isDisabled(), dlg.getByRole("button", { name: "Очистить", exact: true }).isDisabled(), dlg.locator(".modal__x").isDisabled()]);
      check(`[${tag}] п.9 Сохранить/Отмена/Очистить/× disabled`, dis.every(Boolean), JSON.stringify(dis));
      check(`[${tag}] п.9 спиннер`, (await dlg.locator(".spinner").count()) === 1);
      await shot(page, "saving");
      await page.keyboard.press("Escape");
      await page.mouse.click(5, 5);
      await page.waitForTimeout(300);
      check(`[${tag}] п.9 Esc и подложка не закрывают`, await dlg.isVisible());
      await dlg.waitFor({ state: "detached" });
      await page.close();
    }
    // П.10: ошибки
    for (const [status, detail] of [[404, "Сотрудник не найден"], [503, "База резюме временно недоступна"]]) {
      const page = await ctx.newPage();
      const puts = await setup(page, [{ status, detail }, {}]);
      const dlg = await open(page);
      await fillText(dlg, "Текст при ошибке");
      await dlg.getByRole("button", { name: "Сохранить" }).click();
      const al = dlg.getByRole("alert");
      await al.waitFor();
      check(`[${tag}] п.10 ${status}: текст ответа как есть в role=alert`, (await al.innerText()) === detail, await al.innerText());
      check(`[${tag}] п.10 ${status}: окно открыто, текст сохранён`, (await dlg.isVisible()) && (await TA(dlg).inputValue()) === "Текст при ошибке");
      await page.waitForTimeout(100);
      check(`[${tag}] п.10 ${status}: кнопки доступны, фокус в поле`, (await dlg.getByRole("button", { name: "Сохранить" }).isEnabled()) && (await dlg.getByRole("button", { name: "Отмена" }).isEnabled()) && (await TA(dlg).evaluate((a) => document.activeElement === a)));
      if (status === 404) await shot(page, "error");
      const ec = await al.evaluate((e) => getComputedStyle(e).color);
      const bg = await dlg.evaluate((e) => getComputedStyle(e).backgroundColor);
      check(`[${tag}] п.13 контраст ошибки >= 4.5`, contrast(ec, bg) >= 4.5, contrast(ec, bg).toFixed(2));
      await dlg.getByRole("button", { name: "Сохранить" }).click();
      await dlg.waitFor({ state: "detached" });
      check(`[${tag}] п.10 ${status}: повтор возможен и успешен`, puts.length === 2);
      await page.close();
    }
    // П.2, 13: длинная заметка
    for (const h of [800, 400]) {
      const page = await ctx.newPage();
      await page.setViewportSize({ width, height: h });
      await setup(page, [{}]);
      const dlg = await open(page, /Изменить заметку: Сидоров/);
      const m = await dlg.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const foot = el.querySelector(".modal__foot");
        const sc = [...el.querySelectorAll("*")].find((n) => /auto|scroll/.test(getComputedStyle(n).overflowY) && n.scrollHeight > n.clientHeight + 2);
        return { top: Math.round(r.top), bottom: Math.round(r.bottom), scrollX: el.scrollWidth > el.clientWidth + 1, footBottom: Math.round(foot?.getBoundingClientRect().bottom), scroller: !!sc };
      });
      check(`[${tag}] h${h} п.2/13 окно в экране, нет горизонтальной прокрутки, футер виден`, m.top >= 0 && m.bottom <= h && !m.scrollX && m.footBottom <= h, JSON.stringify(m));
      if (h === 400) check(`[${tag}] h400 п.2 прокручивается тело`, m.scroller);
      await shot(page, `long_h${h}`);
      if (h === 800) {
        await page.keyboard.press("Escape");
        const ow = await lastCell(page, "Сидоров").locator(".note").evaluate((e) => getComputedStyle(e).overflowWrap);
        check(`[${tag}] п.2 таблица: длинная заметка, overflow-wrap anywhere`, ow === "anywhere", ow);
        await shot(page, "table_long");
      }
      await page.close();
    }
    // П.13: контраст, фокус, Tab-ловушка
    {
      const page = await ctx.newPage();
      await setup(page, [{}]);
      const dlg = await open(page);
      await fillText(dlg, "я".repeat(1950));
      const bg = await dlg.evaluate((e) => getComputedStyle(e).backgroundColor);
      const colors = await dlg.evaluate((el) => {
        const g = (s) => getComputedStyle(el.querySelector(s)).color;
        const prim = el.querySelector("button.primary");
        return { hint: g("#note-hint"), cnt: g("#note-cnt"), meta: g(".modal__meta"), clear: g(".note-dlg__clear"), primary: [getComputedStyle(prim).color, getComputedStyle(prim).backgroundColor] };
      });
      const cs = [colors.hint, colors.cnt, colors.meta].map((c) => contrast(c, bg).toFixed(2));
      check(`[${tag}] п.13 контраст подсказки/счётчика/подзаголовка >= 4.5`, cs.every((v) => Number(v) >= 4.5), cs.join(","));
      const bgf = await dlg.locator(".modal__foot").evaluate((e) => getComputedStyle(e).backgroundColor);
      const cp = contrast(colors.primary[0], colors.primary[1]);
      check(`[${tag}] п.13 контраст «Сохранить» >= 4.5`, cp >= 4.5, cp.toFixed(2));
      const cc = contrast(colors.clear, bgf);
      check(`[${tag}] п.13 контраст «Очистить» >= 4.5`, cc >= 4.5, cc.toFixed(2));
      const seen = []; let ringOk = true;
      for (let i = 0; i < 7; i++) {
        await page.keyboard.press("Tab");
        const info = await page.evaluate(() => { const a = document.activeElement; const s = getComputedStyle(a); return { inDlg: !!a.closest('[role="dialog"]'), t: (a.getAttribute("aria-label") || a.innerText || a.id || a.tagName).slice(0, 20), ring: (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none" }; });
        seen.push(info.t + (info.inDlg ? "" : "!OUT")); ringOk = ringOk && info.ring;
      }
      check(`[${tag}] п.7 Tab циклически внутри окна`, !seen.some((s) => s.includes("!OUT")), seen.join(" > "));
      check(`[${tag}] п.13 кольцо фокуса на всех интерактивных`, ringOk, seen.join(" > "));
      await shot(page, "focus");
      await page.close();
    }
  } catch (e) { check(`[${tag}] сценарий без исключений`, false, String(e).split("\n").slice(0, 3).join(" | ")); }
  await ctx.close();
}
await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`ИТОГО: ${results.filter((r) => r.ok).length}/${results.length} PASS`);
