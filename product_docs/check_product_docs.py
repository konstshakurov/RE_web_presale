#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка product_docs: сверка карточек и INDEX с графом 5.1 и файлами.

Запуск (из любой папки):
    python product_docs/check_product_docs.py [--project re_web_presale] [--fix]

Проверяет (ошибка ✖ даёт код возврата 1, предупреждение ⚠ не блокирует):
  ✖ ID из карточек БФ нет в графе 5.1
  ✖ версия/статус в таблице требований карточки не совпадают с графом   (--fix обновляет таблицу)
  ✖ ссылка на спецификацию или страницу компонента не ведёт к файлу
  ✖ БФ есть в INDEX, но нет карточки (и наоборот)
  ✖ карточка planned без заполненного раздела «Планируемое изменение»
  ✖ неизвестное значение статуса БФ
  ⚠ требование не покрыто ни одной БФ (кроме «Сквозных» из INDEX)
  ⚠ требование в статусе under_change (CR закрыт без пакета 5.5?)
  ⚠ компонент без БФ; БФ implemented без компонента
  ⚠ .puml без свежего .svg

Только стандартная библиотека. Пути берутся от корня репозитория (родитель product_docs/).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
STATUSES = ("idea", "planned", "in_dev", "implemented")
COVERED_TYPES = ("FR", "NFR", "BR", "UC", "BP", "DD", "ERD")
ID_RE = re.compile(r"\b([A-Z]{2,4})-(\d{3})\b")
ROW_RE = re.compile(r"^\| ((?:FR|NFR|BR|UC|BP|DD|ERD)-\d{3}) \|")


def expand_compact(text: str) -> set[str]:
    """«NFR-001, 003, 004» -> {NFR-001, NFR-003, NFR-004}; «FR-001; NFR-002» тоже."""
    out: set[str] = set()
    prefix = None
    for tok in re.split(r"[,;]\s*", text):
        tok = tok.strip().strip("`")
        m = re.match(r"^([A-Z]{2,4})-(\d{3})\b", tok)
        if m:
            prefix = m.group(1)
            out.add(f"{prefix}-{m.group(2)}")
        elif prefix and re.match(r"^\d{3}\b", tok):
            out.add(f"{prefix}-{tok[:3]}")
    return out


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def load_graph(project: str) -> dict:
    p = ROOT / "output" / "data" / project / f"{project}_traceability_repo.json"
    if not p.exists():
        sys.exit(f"Не найден граф 5.1: {p}")
    return json.load(open(p, encoding="utf-8"))


def spec_file(project: str, rid: str) -> str | None:
    d = ROOT / "output" / "data" / project / "specs"
    hits = sorted(d.glob(rid.lower().replace("-", "_") + "_*.md"))
    return hits[0].name if hits else None


def req_row(project: str, rid: str, r: dict) -> str:
    sp = spec_file(project, rid)
    link = f"[`{sp}`](../../../output/data/{project}/specs/{sp})" if sp else "—"
    return f"| {rid} | {r.get('title', '')} | {r.get('version', '')} / {r.get('status', '')} | {link} |"


def card_field(text: str, name: str) -> str:
    m = re.search(rf"^\| {re.escape(name)} \| (.*?) \|\s*$", text, re.M)
    return m.group(1).strip() if m else ""


def section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def check(project: str, fix: bool) -> Report:
    rep = Report()
    graph = load_graph(project)
    reqs = {r["id"]: r for r in graph["requirements"]}
    cards = sorted((DOCS / "01_business_functions").glob("BF-*/README.md"))
    index = (DOCS / "INDEX.md").read_text(encoding="utf-8") if (DOCS / "INDEX.md").exists() else ""
    if not index:
        rep.err("нет INDEX.md")

    # --- карточки ---
    covered: set[str] = set()
    comp_refs: set[str] = set()
    card_ids: set[str] = set()
    for card in cards:
        bf = card.parent.name[:5]  # BF-NN
        card_ids.add(bf)
        text = card.read_text(encoding="utf-8")
        status_cell = card_field(text, "Статус")
        status = status_cell.split()[0].strip("`") if status_cell else ""
        if status not in STATUSES:
            rep.err(f"{bf}: неизвестный статус «{status_cell}» (допустимо: {', '.join(STATUSES)})")
        if status == "planned":
            body = section(text, "Планируемое изменение")
            if len(body) < 40:
                rep.err(f"{bf}: статус planned, но раздел «Планируемое изменение» пуст")

        lines = text.split("\n")
        changed = False
        for i, line in enumerate(lines):
            m = ROW_RE.match(line)
            if not m:
                continue
            rid = m.group(1)
            covered.add(rid)
            r = reqs.get(rid)
            if r is None:
                rep.err(f"{bf}: {rid} нет в графе 5.1")
                continue
            want = req_row(project, rid, r)
            if line.strip() != want:
                if fix:
                    lines[i] = want
                    changed = True
                else:
                    cell = line.split("|")[3].strip() if line.count("|") >= 5 else "?"
                    rep.err(f"{bf}: {rid} в карточке «{cell}», в графе «{r.get('version')} / {r.get('status')}» (или название/ссылка) — запустите --fix")
        if changed:
            card.write_text("\n".join(lines), encoding="utf-8")
            print(f"--fix: обновлена таблица требований в {bf}")

        # относительные ссылки на файлы
        for target in re.findall(r"\]\(([^)#]+)\)", text):
            if target.startswith(("http://", "https://")):
                continue
            if not (card.parent / target).exists():
                rep.err(f"{bf}: ссылка не ведёт к файлу: {target}")
        # компоненты
        comp_text = section(text, "Техническое решение (C3): компоненты")
        for c in re.findall(r"02_system_components/([a-z0-9_]+)/", comp_text):
            comp_refs.add(c)
            if not (DOCS / "02_system_components" / c).is_dir():
                rep.err(f"{bf}: компонент {c} не найден в 02_system_components/")
        comps = [c.name for c in (DOCS / "02_system_components").glob("*/")]
        mentioned = [c for c in comps if re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", comp_text)]
        comp_refs.update(mentioned)
        if status == "implemented" and not mentioned:
            rep.warn(f"{bf}: implemented, но ни один компонент из 02_system_components/ не указан")

    # --- INDEX <-> карточки ---
    index_ids = set(re.findall(r"^\| (BF-\d{2}) \|", index, re.M))
    for b in sorted(index_ids - card_ids):
        rep.err(f"{b}: есть в INDEX, но нет карточки в 01_business_functions/")
    for b in sorted(card_ids - index_ids):
        rep.err(f"{b}: есть карточка, но нет строки в INDEX")
    for target in re.findall(r"\]\(([^)#]+)\)", index):
        if not target.startswith("http") and not (DOCS / target).exists():
            rep.err(f"INDEX: ссылка не ведёт к файлу: {target}")

    # --- покрытие требований ---
    m = re.search(r"^Сквозные:\s*(.*)$", index, re.M)
    cross = expand_compact(m.group(1)) if m else set()
    for rid, r in sorted(reqs.items()):
        if rid.split("-")[0] in COVERED_TYPES and rid not in covered and rid not in cross:
            if r.get("status") in ("deprecated", "retired", "superseded", "rejected"):
                continue
            rep.warn(f"{rid} ({r.get('title', '')}) не покрыто ни одной БФ и не в «Сквозных»")

    # --- under_change ---
    uc = [rid for rid, r in reqs.items() if r.get("status") == "under_change"]
    if uc:
        rep.warn(f"в графе {len(uc)} требований в статусе under_change (CR принят, пакет 5.5 не записан): "
                 + ", ".join(sorted(uc)[:8]) + (" …" if len(uc) > 8 else ""))

    # --- компоненты ---
    for comp in sorted((DOCS / "02_system_components").glob("*/")):
        if not (comp / "README.md").exists():
            rep.err(f"компонент {comp.name}: нет README.md (SRV)")
        if comp.name not in comp_refs:
            rep.warn(f"компонент {comp.name}: ни одна БФ на него не ссылается")

    # --- диаграммы ---
    for puml in sorted(DOCS.rglob("seq/*.puml")):
        svg = puml.with_suffix(".svg")
        if not svg.exists():
            rep.warn(f"{puml.relative_to(DOCS)}: нет .svg — bash product_docs/render_diagrams.sh")
        elif svg.stat().st_mtime + 1 < puml.stat().st_mtime:
            rep.warn(f"{puml.relative_to(DOCS)}: .svg старше .puml — перерисуйте")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="project_id (по умолчанию — единственный в output/data/)")
    ap.add_argument("--fix", action="store_true", help="обновить таблицы требований в карточках из графа")
    args = ap.parse_args()
    project = args.project
    if not project:
        projects = [p.name for p in (ROOT / "output" / "data").iterdir() if p.is_dir()]
        if len(projects) != 1:
            sys.exit(f"Укажите --project (найдено: {projects})")
        project = projects[0]
    rep = check(project, args.fix)
    for e in rep.errors:
        print("✖", e)
    for w in rep.warnings:
        print("⚠", w)
    print(f"\nИтого: ошибок {len(rep.errors)}, предупреждений {len(rep.warnings)}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
