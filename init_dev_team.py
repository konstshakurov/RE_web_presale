#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подключает опциональную команду разработки (arch, ux, dev, qa, devops) из `templates/dev_team/`.

    python init_dev_team.py --project-id <project_id> [--title "Название"] [--owner "Имя"]
                            [--tracker Linear] [--tracker-mcp <имя MCP-сервера>] [--task-prefix "Pre."]
                            [--backend-dir backend/] [--frontend-dir frontend/src/]
                            [--tests-dir backend/tests/] [--migrations-dir db/migrations/]
                            [--roles arch,ux,dev,qa,devops] [--dry-run] [--force]

Что делает: рендерит шаблоны, подставляя значения проекта вместо `{{...}}`, в
  .claude/agents/<роль>.md, .claude/rules/dev_team.md, .claude/rules/ui_workflow.md,
  .claude/skills/ux_design/SKILL.md   (скилл — только если выбрана роль ux).
Существующие файлы не перезаписываются без --force. Платформа без этого шага ведёт только
аналитику; четвёрка платформенных агентов (analyst, orchestrator, worker_llm, worker_file) не
меняется. Новые и изменённые файлы агентов подхватываются только после перезапуска сессии
(`/exit`, затем `claude --continue`). Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "templates" / "dev_team"
CLAUDE = ROOT / ".claude"
ALL_ROLES = ("arch", "ux", "dev", "qa", "devops")
BASE_TOOLS = "Bash, Read, Write, Edit, Glob, Grep, ToolSearch"
PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]|_(?!_))*$")
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def detect_project_id() -> str | None:
    data = ROOT / "output" / "data"
    if not data.is_dir():
        return None
    found = [p.name for p in data.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    return found[0] if len(found) == 1 else None


def build_values(args: argparse.Namespace) -> dict[str, str]:
    project_id = args.project_id or detect_project_id()
    if not project_id:
        sys.exit("Укажите --project-id (или положите проект в output/data/<project_id>/).")
    if not PROJECT_ID_RE.match(project_id):
        sys.exit(f"project_id «{project_id}» не подходит: строчные латинские буквы, цифры, «-» и «_», "
                 "без пробелов и сдвоенного «_» (то же правило, что и для папки проекта).")
    tools = BASE_TOOLS + (f", {args.tracker_mcp}" if args.tracker_mcp else "")
    values = {
        "PROJECT_ID": project_id,
        "PROJECT_TITLE": args.title or project_id,
        "OWNER": args.owner or "владелец продукта",
        "TRACKER": args.tracker or "трекер задач",
        "TASK_PREFIX": args.task_prefix or "",
        "BACKEND_DIR": args.backend_dir,
        "FRONTEND_DIR": args.frontend_dir,
        "TESTS_DIR": args.tests_dir,
        "MIGRATIONS_DIR": args.migrations_dir,
        "TOOLS": tools,
    }
    for key, value in values.items():
        if "{{" in value or "}}" in value or "\n" in value:
            sys.exit(f"Значение {key} содержит недопустимые символы.")
    return values


def render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    left = PLACEHOLDER_RE.findall(text)
    if left:
        raise ValueError("не подставлены маркеры: " + ", ".join(sorted(set(left))))
    return text


def plan(roles: list[str]) -> list[tuple[Path, Path]]:
    """(исходник, назначение) для выбранных ролей."""
    items: list[tuple[Path, Path]] = []
    for role in ALL_ROLES:
        if role in roles:
            items.append((SOURCE / "agents" / f"{role}.md", CLAUDE / "agents" / f"{role}.md"))
    items.append((SOURCE / "rules" / "dev_team.md", CLAUDE / "rules" / "dev_team.md"))
    # ворота UI нужны и без роли ux: агенты ссылаются на них; skill проектирования — только с ux
    items.append((SOURCE / "rules" / "ui_workflow.md", CLAUDE / "rules" / "ui_workflow.md"))
    if "ux" in roles:
        items.append((SOURCE / "skills" / "ux_design" / "SKILL.md",
                      CLAUDE / "skills" / "ux_design" / "SKILL.md"))
    return items


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-id")
    ap.add_argument("--title", help="название проекта для текстов (по умолчанию project_id)")
    ap.add_argument("--owner", help="кто утверждает решения, меняющие скоуп/срок и дизайн")
    ap.add_argument("--tracker", help="название трекера задач (Linear, Jira, …)")
    ap.add_argument("--tracker-mcp", help="имя MCP-сервера трекера для поля tools агентов, если он подключён")
    ap.add_argument("--task-prefix", default="", help="префикс задач, например «Pre.»: Pre.Dev-N")
    ap.add_argument("--backend-dir", default="backend/")
    ap.add_argument("--frontend-dir", default="frontend/")
    ap.add_argument("--tests-dir", default="backend/tests/")
    ap.add_argument("--migrations-dir", default="db/migrations/")
    ap.add_argument("--roles", default=",".join(ALL_ROLES),
                    help="через запятую; по умолчанию все пять (без ux проект без интерфейса)")
    ap.add_argument("--dry-run", action="store_true", help="показать, что будет создано")
    ap.add_argument("--force", action="store_true", help="перезаписать существующие файлы")
    args = ap.parse_args()

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    unknown = [r for r in roles if r not in ALL_ROLES]
    if unknown or not roles:
        sys.exit(f"Неизвестные роли: {', '.join(unknown) or '(пусто)'}. Допустимо: {', '.join(ALL_ROLES)}.")
    if not SOURCE.is_dir():
        sys.exit(f"Не найден шаблон: {SOURCE}")

    values = build_values(args)
    created, skipped = [], []
    rendered: list[tuple[Path, str]] = []
    for src, dst in plan(roles):
        try:
            rendered.append((dst, render(src.read_text(encoding="utf-8"), values)))
        except ValueError as exc:
            sys.exit(f"{src.relative_to(ROOT)}: {exc}")

    for dst, text in rendered:
        rel = dst.relative_to(ROOT)
        if dst.exists() and not args.force:
            skipped.append(rel)
            continue
        if not args.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text, encoding="utf-8", newline="\n")
        created.append(rel)

    verb = "будет создан" if args.dry_run else "создан"
    for rel in created:
        print(f"{verb}: {rel.as_posix()}")
    for rel in skipped:
        print(f"пропущен (уже есть, --force перезапишет): {rel.as_posix()}")
    if created and not args.dry_run:
        print("\nГотово. Перезапустите сессию (/exit, затем claude --continue): новые файлы агентов "
              "подхватываются только после перезапуска. Правила работы — .claude/rules/dev_team.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
