#!/usr/bin/env python3
"""Static checks for PlantUML sequence-diagram conventions."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


DECLARATION_RE = re.compile(
    r'^\s*(actor|participant|database|queue)\s+(?:"[^"]+"\s+as\s+)?([^\s]+)\s*$'
)
ACTIVATE_RE = re.compile(r"^\s*activate\s+([^\s]+)\s*$")
DEACTIVATE_RE = re.compile(r"^\s*deactivate\s+([^\s]+)\s*$")
MESSAGE_RE = re.compile(r"^\s*([^\s]+)\s+(-->|->)\s+([^\s]+)\s*:\s*(.*?)\s*$")


def meaningful_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    in_block_comment = False
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("/'"):
            in_block_comment = True
        if in_block_comment:
            if stripped.endswith("'/"):
                in_block_comment = False
            continue
        if not stripped or stripped.startswith("'"):
            continue
        result.append((number, raw))
    return result


def validate(text: str) -> list[str]:
    errors: list[str] = []
    lines = meaningful_lines(text)
    tokens = [line.strip() for _, line in lines]

    if not tokens or tokens[0] != "@startuml":
        errors.append("Код должен начинаться с @startuml.")
    if not tokens or tokens[-1] != "@enduml":
        errors.append("Код должен заканчиваться @enduml.")
    if any(token == "autoactivate on" for token in tokens):
        errors.append("Нельзя использовать autoactivate on: активации должны быть явными.")

    queues: set[str] = set()
    declared: set[str] = set()
    for number, line in lines:
        match = DECLARATION_RE.match(line)
        if not match:
            continue
        kind, alias = match.groups()
        if alias in declared:
            errors.append(f"Строка {number}: участник {alias} объявлен повторно.")
        declared.add(alias)
        if kind == "queue":
            queues.add(alias)

    active: dict[str, int] = defaultdict(int)
    first_message_seen = False
    pending_self: tuple[str, int] | None = None
    event_number = 0
    last_deactivate_event: dict[str, int] = {}

    for number, line in lines:
        token = line.strip()
        if token in {"@startuml", "@enduml", "autonumber"}:
            continue
        if DECLARATION_RE.match(line) or token.startswith(("title ", "header ", "footer ")):
            continue
        if token.startswith(("==", "alt ", "else", "end", "opt ", "loop ", "break ", "group ", "note ")):
            continue

        activate = ACTIVATE_RE.match(line)
        if activate:
            alias = activate.group(1)
            if alias in queues:
                errors.append(f"Строка {number}: у брокера {alias} не должно быть бара активации.")
            if last_deactivate_event.get(alias) == event_number:
                errors.append(
                    f"Строка {number}: activate {alias} стоит в той же позиции диаграммы, "
                    f"что и предыдущий deactivate {alias}. Сначала покажите новое сообщение "
                    f"от {alias}, затем активируйте участника, иначе PlantUML вернёт "
                    "Activate/Deactivate already done."
                )
            active[alias] += 1
            if pending_self:
                expected, source_line = pending_self
                if alias != expected:
                    errors.append(
                        f"Строка {source_line}: после самовызова {expected} нужен вложенный "
                        f"activate {expected}."
                    )
                pending_self = None
            continue

        if pending_self:
            alias, source_line = pending_self
            errors.append(
                f"Строка {source_line}: после самовызова {alias} нужен вложенный activate {alias}."
            )
            pending_self = None

        deactivate = DEACTIVATE_RE.match(line)
        if deactivate:
            alias = deactivate.group(1)
            if active[alias] == 0:
                errors.append(f"Строка {number}: deactivate {alias} не имеет парного activate.")
            else:
                active[alias] -= 1
            last_deactivate_event[alias] = event_number
            continue

        message = MESSAGE_RE.match(line)
        if not message:
            continue
        source, arrow, target, label = message.groups()
        event_number += 1
        if not label:
            errors.append(f"Строка {number}: сообщение не имеет подписи.")
        if not first_message_seen:
            first_message_seen = True
            if source not in queues and active[source] == 0:
                errors.append(
                    f"Строка {number}: первый инициатор {source} должен быть активирован до сообщения."
                )
        if source == target:
            if active[source] == 0:
                errors.append(f"Строка {number}: самовызов {source} начат без внешней активации.")
            pending_self = (source, number)
        if arrow == "-->" and not label:
            errors.append(f"Строка {number}: ответ должен содержать данные, статус или сообщение.")

    if pending_self:
        alias, source_line = pending_self
        errors.append(f"Строка {source_line}: после самовызова {alias} нужен вложенный activate {alias}.")

    for alias, depth in sorted(active.items()):
        if depth:
            errors.append(f"Участник {alias}: осталось незакрытых баров активации — {depth}.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagram", type=Path, help="Путь к файлу .puml")
    args = parser.parse_args()
    text = args.diagram.read_text(encoding="utf-8")
    errors = validate(text)
    if errors:
        print("Найдены ошибки:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("OK: базовые правила UML Sequence соблюдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
