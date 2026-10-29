import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { statusClass, statusTone } from "../lib/statusTone";
import type { StatusTone } from "../lib/statusTone";
import { EMPLOYEE_STATUSES } from "../lib/employees";
import CandidateList from "../components/matching/CandidateList";

const NEEDS: [string, StatusTone][] = [
  ["Требуется кандидат", "warn"],
  ["Поиск кандидата", "info"],
  ["Найм", "info"],
  ["Кандидатов нет", "err"],
  ["Кандидат назначен", "ok"],
];
const EMPLOYEES: [string, StatusTone][] = [
  ["Свободен", "ok"],
  ["Скоро освобождается", "warn"],
  ["Ожидает подключения", "warn"],
  ["Коммерческий проект", "info"],
  ["Внутренний проект", "info"],
  ["Выход новичка", "info"],
  ["Программа обучения", "info"],
  ["Недоступен для подключения", "err"],
  ["Уволен", "neu"],
  ["Декрет", "neu"],
];

describe("statusTone (KON-150)", () => {
  it.each([...NEEDS, ...EMPLOYEES])("%s -> %s", (s, tone) => expect(statusTone(s)).toBe(tone));
  it("покрывает все 10 статусов сотрудников", () => {
    expect(EMPLOYEES.map(([s]) => s).sort()).toEqual([...EMPLOYEE_STATUSES].sort());
  });
  it("регистр и пробелы нормализуются", () => {
    expect(statusTone("  СКОРО   освобождается ")).toBe("warn");
  });
  it("неизвестный, пустой и null -> нейтральный", () => {
    expect(statusTone("Отказ по кандидату")).toBe("neu");
    expect(statusTone("")).toBe("neu");
    expect(statusTone(null)).toBe("neu");
  });
  it("класс: нейтральный - базовый, прочие - с модификатором", () => {
    expect(statusClass("Свободен")).toBe("status status--ok");
    expect(statusClass("Декрет")).toBe("status");
    expect(statusClass("что-то")).toBe("status");
  });
  it("CandidateList красит статус кандидата", () => {
    render(
      <CandidateList
        rows={[{ key: "E-1", name: "Кандидат", status: "Свободен", percent: 80, rationale: "", stale: false, tags: [] }]}
        onResume={() => {}}
      />,
    );
    expect(screen.getByText("Свободен")).toHaveClass("status", "status--ok");
  });
});
