import { describe, expect, it } from "vitest";

import type { SearchItem } from "../api/types";
import {
  hasTimeout,
  parseTerms,
  percentTone,
  providerUrlError,
  replacementsText,
  retryableTasks,
  searchPercent,
} from "../lib/matching";

describe("percentTone", () => {
  it("цветовая шкала по границам 50 и 75", () => {
    expect(percentTone(100)).toBe("high");
    expect(percentTone(75)).toBe("high");
    expect(percentTone(74)).toBe("mid");
    expect(percentTone(50)).toBe("mid");
    expect(percentTone(49)).toBe("low");
    expect(percentTone(0)).toBe("low");
  });
});

describe("providerUrlError", () => {
  it("внешний - только HTTPS, внутренний допускает http", () => {
    expect(providerUrlError("http://x.example.test/v1", true)).toMatch(/HTTPS/);
    expect(providerUrlError("https://x.example.test/v1", true)).toBe("");
    expect(providerUrlError("http://x.internal/v1", false)).toBe("");
    expect(providerUrlError("ftp://x.example.test", false)).not.toBe("");
    expect(providerUrlError("", false)).not.toBe("");
  });
});

describe("parseTerms", () => {
  it("обрезает, убирает пустые и повторы без учёта регистра", () => {
    expect(parseTerms(" Альфа \n\nальфа\r\nБета")).toEqual(["Альфа", "Бета"]);
  });
});

describe("searchPercent", () => {
  const base = { source: "llm", percent: null, screen_percent: null } as SearchItem;
  it("llm - процент, screening - предварительный, fallback - без процента", () => {
    expect(searchPercent({ ...base, percent: 80 })).toEqual({ value: 80, preliminary: false });
    expect(searchPercent({ ...base, source: "screening", screen_percent: 30 })).toEqual({ value: 30, preliminary: true });
    expect(searchPercent({ ...base, source: "fallback", percent: 55 })).toEqual({ value: null, preliminary: false });
  });
});

describe("задачи и подстановки", () => {
  const t = (status: string) => ({ task_id: status, status, requires_confirmation: false, batch_status: "", candidates: 1 });
  it("retryableTasks / hasTimeout", () => {
    const tasks = [t("done"), t("timeout"), t("failed")];
    expect(retryableTasks(tasks)).toHaveLength(2);
    expect(hasTimeout(tasks)).toBe(true);
    expect(hasTimeout([t("failed")])).toBe(false);
  });
  it("replacementsText", () => {
    expect(replacementsText({ persons: 2, companies: 0 })).toBe("persons: 2");
    expect(replacementsText({})).toBe("подстановок нет");
  });
});

describe("candidatesLabel", () => {
  it("склоняет «кандидат» по числу", async () => {
    const { candidatesLabel } = await import("../lib/matching");
    expect(candidatesLabel(0)).toBe("0 кандидатов");
    expect(candidatesLabel(1)).toBe("1 кандидат");
    expect(candidatesLabel(2)).toBe("2 кандидата");
    expect(candidatesLabel(4)).toBe("4 кандидата");
    expect(candidatesLabel(5)).toBe("5 кандидатов");
    expect(candidatesLabel(8)).toBe("8 кандидатов");
    expect(candidatesLabel(11)).toBe("11 кандидатов");
    expect(candidatesLabel(12)).toBe("12 кандидатов");
    expect(candidatesLabel(21)).toBe("21 кандидат");
    expect(candidatesLabel(23)).toBe("23 кандидата");
    expect(candidatesLabel(111)).toBe("111 кандидатов");
  });
});
