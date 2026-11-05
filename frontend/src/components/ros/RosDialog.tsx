import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { NeedOut, RosSettings } from "../../api/types";
import { parseRate } from "../../lib/assign";
import { calcRos, fmtNum, fmtPct, ROS_DEFAULTS, rosRate, vatFactor } from "../../lib/ros";
import Modal from "../Modal";

// Модальное окно «ROS специалиста» (FR-039, decision.md KON-161 - вариант A, approved).
// ЗП вводится вручную и никуда не записывается (БД/Jira/логи); расчёт - на клиенте
// с параметрами из «Настроек», пересчёт автоматом при вводе (без кнопки «Рассчитать»).
const MAX_RATE = 1000000; // как у поля «Ставка» при назначении (FR-039)

export const ROS_SALARY_EMPTY = "Введите ЗП числом больше нуля"; // UC-ROS 5а
export const ROS_RATE_EMPTY = "Введите ставку числом больше нуля"; // UC-ROS 5б
export const ROS_RATE_LIMIT = "Ставка не может превышать 1 000 000"; // UC-ROS 5в

// Валидация ручного ввода ставки: null - ок, текст - ошибка под полем
function rateError(v: number | null | undefined, empty: string): string | null {
  if (v === null) return empty;
  if (v === undefined) return empty;
  if (v <= 0) return empty;
  if (v > MAX_RATE) return ROS_RATE_LIMIT;
  return null;
}

export default function RosDialog({ need, onClose }: { need: NeedOut; onClose: () => void }) {
  const [params, setParams] = useState<RosSettings>(ROS_DEFAULTS);
  const [salary, setSalary] = useState("");
  const [manualRate, setManualRate] = useState("");

  // Параметры модели: общие для всех, читаются при каждом открытии окна (без кэша)
  useEffect(() => {
    let alive = true;
    api
      .rosSettings()
      .then((p) => {
        if (alive) setParams(p);
      })
      .catch(() => {
        /* настройка недоступна - считаем по эталонной модели ROS_model_v2 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const rate = rosRate(need.rate, params.vat_rate);
  const salaryNum = parseRate(salary);
  const rateNum = rate.kind === "readonly" ? (rate.withVat ?? null) : parseRate(manualRate);

  // UC-ROS 5а-5в: ошибки под полями, пересчёт заблокирован
  const salaryErr =
    salary.trim() === "" ? null : salaryNum === undefined || salaryNum === null || salaryNum <= 0 ? ROS_SALARY_EMPTY : null;
  const rErr =
    rate.kind === "manual" && manualRate.trim() !== "" ? rateError(rateNum, ROS_RATE_EMPTY) : null;

  const ros = useMemo(() => {
    if (salaryNum === null || salaryNum === undefined || salaryNum <= 0) return null;
    if (rateNum === null || rateNum === undefined || rateNum <= 0 || rateNum > MAX_RATE) return null;
    return calcRos(params, salaryNum, rateNum);
  }, [params, salaryNum, rateNum]);

  const meta = [
    need.jira_key,
    need.epic_name || need.epic_key || "",
    need.rate.amount != null ? `ставка в потребности: ${rate.demandRate}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Modal title="ROS специалиста" wide size="profile" meta={meta} onClose={onClose}
      footer={
        <>
          <span className="ros__note">ЗП и расчёт не сохраняются и нигде не фиксируются.</span>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Отмена
          </button>
        </>
      }
    >
      <div className="ros">
        <div className={`field${rErr ? " field--err" : ""}`}>
          <label htmlFor="ros-rate">
            {rate.kind === "readonly" ? "Ставка для расчёта, руб. с НДС за час (задана клиентом)" : "Ставка для расчёта, руб. с НДС за час"}
          </label>
          {rate.kind === "readonly" ? (
            <input id="ros-rate" readOnly value={rate.withVat !== null ? fmtNum(rate.withVat) : ""} />
          ) : (
            <input
              id="ros-rate"
              inputMode="decimal"
              placeholder="3 800"
              value={manualRate}
              onChange={(e) => setManualRate(e.target.value)}
            />
          )}
          {rate.kind === "readonly" && rate.vatConverted && (
            <span className="ros__hint">Ставка без НДС приведена к «с НДС» (× {vatFactor(params.vat_rate)})</span>
          )}
          {rate.kind === "manual" && <span className="ros__hint">В потребности: {rate.demandRate}</span>}
          {rErr && <span className="field-hint">{rErr}</span>}
        </div>
        <div className={`field${salaryErr ? " field--err" : ""}`}>
          <label htmlFor="ros-salary">ЗП кандидата, гросс (руб./мес)</label>
          <input id="ros-salary" inputMode="decimal" placeholder="130 000" value={salary} onChange={(e) => setSalary(e.target.value)} />
          {salaryErr && <span className="field-hint">{salaryErr}</span>}
        </div>
        <div className="ros__vals">
          <div className={`ros-card ros-card--main${ros ? "" : " ros-card--empty"}`}>
            <div className="ros-card__cap">
              {ros ? `ROS до лимита ФОТ (первые ${ros.transitionMonth} мес.)` : "ROS до лимита ФОТ"}
            </div>
            <div className="ros-card__num">{ros ? `${fmtPct(ros.rosBefore)}%` : "—"}</div>
            <div className="ros-card__sub">
              {ros ? `прибыль ${fmtNum(ros.profitBefore)} руб./мес после всех расходов и налога` : "введите ЗП и ставку"}
            </div>
          </div>
          <div className={`ros-card${ros ? "" : " ros-card--empty"}`}>
            <div className="ros-card__cap">
              {ros ? `ROS после лимита ФОТ (с ${ros.transitionMonth + 1}-го месяца)` : "ROS после лимита ФОТ"}
            </div>
            <div className="ros-card__num">{ros ? `${fmtPct(ros.rosAfter)}%` : "—"}</div>
            <div className="ros-card__sub">
              {ros
                ? `прибыль ${fmtNum(ros.profitAfter)} руб./мес (ставка взносов ${fmtPct(params.insurance_after)}%)`
                : "введите ЗП и ставку"}
            </div>
          </div>
        </div>
        <span className="ros__month">
          Месяц перехода по лимиту ФОТ: N = ОКРУГЛВНИЗ(Лимит ФОТ / ЗП, 0) — «до лимита» N месяцев, «после» с N+1-го.
        </span>
      </div>
    </Modal>
  );
}
