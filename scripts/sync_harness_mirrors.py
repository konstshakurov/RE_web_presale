#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает зеркала канона `.agents/` для сред запуска Claude Code и Codex.

    python scripts/sync_harness_mirrors.py            # пересобрать зеркала
    python scripts/sync_harness_mirrors.py --check    # только проверить, код 1 при расхождении
    python scripts/sync_harness_mirrors.py --force    # перезаписать и файлы без отметки зеркала

Канон — `.agents/rules/*.md` и `.agents/agents/<платформенный агент>.md`. Зеркало — тот же
текст с путями и командами среды и с отметкой «собирает python scripts/sync_harness_mirrors.py»
в начале. Правки делаются только в каноне; зеркала вручную не правятся.

Файл без отметки зеркала не перезаписывается без --force. В --check он считается расхождением,
кроме `.claude/rules/dev_team.md` и `.claude/rules/ui_workflow.md`: их может установить
`init_dev_team.py` из шаблона проекта, и тогда это не зеркало, а экземпляр команды.
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANON_RULES = ROOT / ".agents" / "rules"
CANON_AGENTS = ROOT / ".agents" / "agents"
MARKER = "собирает `python scripts/sync_harness_mirrors.py`"
PLATFORM_AGENTS = ("analyst", "orchestrator", "worker_llm", "worker_file")
# файлы, которые может установить init_dev_team.py; без отметки зеркала это экземпляр, не зеркало
INIT_DEV_TEAM_TARGETS = {".claude/rules/dev_team.md", ".claude/rules/ui_workflow.md"}

HARNESSES = {
    "claude": {
        "title": "Claude Code",
        "dir": ".claude",
        "agents": {a: f".claude/agents/{a}.md" for a in PLATFORM_AGENTS},
        "agent_format": "md",
        "subs": [
            ("`AGENTS.md`", "`CLAUDE.md`"),
            ("корневое `CLAUDE.md`", "корневой `CLAUDE.md`"),
            ("запусти сессию заново — разговор вернётся",
             "запусти заново командой `claude --continue` — разговор вернётся"),
            ("подхватываются только после перезапуска сессии.",
             "подхватываются только после перезапуска сессии (`/exit`, затем `claude --continue`)."),
            ("| скиллы платформы (`skills/`, шаблоны `templates/`) |",
             "| скиллы платформы (`skills/`, шаблоны `templates/`, `.claude/skills/`) |"),
        ],
    },
    "codex": {
        "title": "Codex",
        "dir": ".codex",
        "agents": {"analyst": ".codex/agents/analyst.toml",
                   "orchestrator": ".codex/agents/orchestrator.toml"},
        "agent_format": "toml",
        "subs": [
            ("| скиллы платформы (`skills/`, шаблоны `templates/`) |",
             "| скиллы платформы (`skills/`, шаблоны `templates/`, экземпляры скиллов проекта) |"),
        ],
    },
}


def harness_text(text: str, h: dict) -> str:
    """Пути канона → пути среды, затем подстановки среды."""
    for agent, path in h["agents"].items():
        text = text.replace(f"`.agents/agents/{agent}.md`", f"`{path}`")
    text = text.replace("`.agents/rules/", f"`{h['dir']}/rules/")
    for old, new in h["subs"]:
        text = text.replace(old, new)
    return text


def insert_after_title(text: str, note: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[: i + 1] + ["", note] + lines[i + 1:])
    raise ValueError("нет заголовка первого уровня")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("нет frontmatter")
    head, body = text[4:].split("\n---\n", 1)
    meta = {}
    for line in head.split("\n"):
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build() -> dict[Path, str]:
    """Все зеркала: путь → ожидаемое содержимое (LF)."""
    out: dict[Path, str] = {}
    for h in HARNESSES.values():
        for src in sorted(CANON_RULES.glob("*.md")):
            canon = src.read_text(encoding="utf-8").replace("\r\n", "\n")
            note = (f"> Зеркало канона `.agents/rules/{src.name}` для {h['title']}; {MARKER},\n"
                    f"> вручную не правь — правка делается в каноне.")
            out[ROOT / h["dir"] / "rules" / src.name] = insert_after_title(harness_text(canon, h), note)
        for agent, rel in h["agents"].items():
            canon = (CANON_AGENTS / f"{agent}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
            meta, body = split_frontmatter(canon)
            body = harness_text(body, h)
            if h["agent_format"] == "md":
                note = (f"> Зеркало канона `.agents/agents/{agent}.md` для {h['title']}; {MARKER},\n"
                        f"> вручную не правь — правка делается в каноне.")
                text = canon.split("\n---\n", 1)[0] + "\n---\n" + insert_after_title(body, note)
            else:
                if "'''" in body:
                    raise ValueError(f"{agent}: в тексте есть ''' — TOML-строку не собрать")
                text = (f"# Зеркало канона .agents/agents/{agent}.md для {h['title']}; {MARKER},\n"
                        f"# вручную не правь — правка делается в каноне.\n"
                        f"name = {toml_string(meta['name'])}\n"
                        f"description = {toml_string(meta['description'])}\n"
                        f"developer_instructions = '''\n{body.strip(chr(10))}\n'''\n")
            out[ROOT / rel] = text
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="только проверить; код 1, если зеркало разошлось")
    ap.add_argument("--force", action="store_true", help="перезаписать и файлы без отметки зеркала")
    args = ap.parse_args()

    stale, skipped, written = [], [], []
    for path, expected in build().items():
        rel = path.relative_to(ROOT).as_posix()
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if current is not None and MARKER not in current and not args.force:
            if args.check and rel not in INIT_DEV_TEAM_TARGETS:
                stale.append(rel)
            else:
                skipped.append(rel)
            continue
        if current == expected:
            continue
        if args.check:
            stale.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
        written.append(rel)

    for rel in written:
        print(f"обновлено: {rel}")
    for rel in skipped:
        print(f"пропущено (нет отметки зеркала; --force перезапишет): {rel}")
    if stale and any(r.startswith(".claude/") for r in stale):
        print("Подсказка: зеркала без отметки пересобирает python scripts/sync_harness_mirrors.py --force")
    if stale:
        for rel in stale:
            print(f"расходится с каноном: {rel}")
        print("Запусти python scripts/sync_harness_mirrors.py, чтобы пересобрать зеркала.")
        return 1
    if args.check:
        print("Зеркала совпадают с каноном.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
