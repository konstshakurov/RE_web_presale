import { describe, expect, it } from "vitest";

import type { CvLimits, EmployeeOut, FileOut } from "../api/types";
import {
  EMPLOYEE_STATUSES,
  findMatches,
  formatSize,
  groupFiles,
  matchesResume,
  statusOptions,
  textSegments,
  uploadKinds,
  validateUpload,
} from "../lib/employees";

const LIMITS: CvLimits = {
  max_file_mb: 10,
  max_file_bytes: 10 * 1024 * 1024,
  upload_kinds: { materials: "Материалы", main_cv: "Основное CV", extra_cv: "Доп. CV" },
  extensions_by_kind: {
    main_cv: ["doc", "docx"],
    extra_cv: ["doc", "docx"],
    materials: ["doc", "docx", "pdf", "png"],
  },
};

const emp = (over: Partial<EmployeeOut> = {}): EmployeeOut => ({
  jira_key: "EMP-1",
  full_name: "Тестов Тест Тестович",
  status: "Свободен",
  assignable: true,
  synced_at: "2026-10-09T10:00:00Z",
  ...over,
});

const file = (over: Partial<FileOut> = {}): FileOut => ({
  id: 1,
  employee_key: "EMP-1",
  kind: "extra_cv",
  kind_title: "Доп. CV",
  file_name: "cv.docx",
  mime_type: "x",
  size_bytes: 100,
  is_archived: false,
  uploaded_by: "u",
  uploaded_at: "2026-10-01T10:00:00Z",
  extraction_status: "ok",
  can_delete: true,
  can_restore: false,
  ...over,
});

describe("statusOptions", () => {
  it("10 статусов FR-002 в фиксированном порядке, нули для отсутствующих, лишние в конце", () => {
    const o = statusOptions([
      { status: "Декрет", count: 2 },
      { status: "Новый статус", count: 1 },
    ]);
    expect(o.slice(0, 10).map((x) => x.status)).toEqual(EMPLOYEE_STATUSES);
    expect(o.find((x) => x.status === "Декрет")?.count).toBe(2);
    expect(o.find((x) => x.status === "Свободен")?.count).toBe(0);
    expect(o[10]).toEqual({ status: "Новый статус", count: 1 });
  });
});

describe("validateUpload", () => {
  it("формат проверяется по типу файла", () => {
    const pdf = new File(["x"], "cv.pdf");
    expect(validateUpload(pdf, "main_cv", LIMITS)).toMatch(/\.pdf не поддерживается.*\.doc, \.docx/);
    expect(validateUpload(pdf, "materials", LIMITS)).toBeNull();
    expect(validateUpload(new File(["x"], "CV.DOCX"), "extra_cv", LIMITS)).toBeNull();
  });
  it("без расширения и пустой файл отклоняются", () => {
    expect(validateUpload(new File(["x"], "cv"), "main_cv", LIMITS)).toMatch(/\.\?/);
    expect(validateUpload(new File([], "cv.docx"), "main_cv", LIMITS)).toBe("Файл пустой");
  });
  it("больше лимита - отказ с лимитом из настроек", () => {
    const big = new File(["xxxxx"], "cv.docx");
    expect(validateUpload(big, "main_cv", { ...LIMITS, max_file_bytes: 3, max_file_mb: 1 })).toBe("Файл больше лимита 1 МБ");
  });
});

describe("uploadKinds", () => {
  it("порядок: основное, доп., материалы; названия из API", () => {
    expect(uploadKinds(LIMITS).map((k) => k.kind)).toEqual(["main_cv", "extra_cv", "materials"]);
    expect(uploadKinds(LIMITS)[0].title).toBe("Основное CV");
  });
});

describe("поиск в тексте CV", () => {
  it("совпадения без учёта регистра", () => {
    expect(findMatches("1С Аналитик, 1с ERP", "1с")).toEqual([0, 13]);
    expect(findMatches("abc", "")).toEqual([]);
  });
  it("сегменты: совпадения нумеруются, текст сохраняется целиком", () => {
    const segs = textSegments("a ERP b erp c", "erp");
    expect(segs.map((s) => s.text).join("")).toBe("a ERP b erp c");
    expect(segs.filter((s) => s.hit !== null).map((s) => s.hit)).toEqual([0, 1]);
  });
});

describe("matchesResume", () => {
  it("статус и поиск по ФИО, ключу, имени файла", () => {
    const f = [file({ file_name: "Иванов_1С.docx" })];
    expect(matchesResume(emp(), f, "тестов", "")).toBe(true);
    expect(matchesResume(emp(), f, "emp-1", "")).toBe(true);
    expect(matchesResume(emp(), f, "1с.docx", "")).toBe(true);
    expect(matchesResume(emp(), f, "нет такого", "")).toBe(false);
    expect(matchesResume(emp(), f, "", "Декрет")).toBe(false);
    expect(matchesResume(emp(), f, "", "Свободен")).toBe(true);
  });
});

describe("groupFiles и formatSize", () => {
  it("основное CV первым", () => {
    const g = groupFiles([file({ id: 1 }), file({ id: 2, kind: "main_cv", uploaded_at: "2026-09-01T00:00:00Z" })]);
    expect(g.get("EMP-1")?.map((f) => f.id)).toEqual([2, 1]);
  });
  it("размер", () => {
    expect(formatSize(500)).toBe("500 Б");
    expect(formatSize(2048)).toBe("2 КБ");
    expect(formatSize(3 * 1024 * 1024)).toBe("3,0 МБ");
  });
});
