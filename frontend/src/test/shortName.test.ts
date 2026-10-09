import { describe, expect, it } from "vitest";

import { shortName } from "../lib/shortName";

describe("shortName", () => {
  it("три слова: без отчества", () => {
    expect(shortName("Тест Тестов Тестович", "u")).toBe("Тест Тестов");
  });
  it("два слова как есть", () => expect(shortName("Тест Тестов", "u")).toBe("Тест Тестов"));
  it("одно слово как есть", () => expect(shortName("Тест", "u")).toBe("Тест"));
  it("лишние пробелы и табуляция", () => {
    expect(shortName("  Тест \t Тестов   Тестович ", "u")).toBe("Тест Тестов");
  });
  it.each([null, undefined, "", "   "])("пусто (%s) -> логин", (v) => {
    expect(shortName(v, "test.user")).toBe("test.user");
  });
});
