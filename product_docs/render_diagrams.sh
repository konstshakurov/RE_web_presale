#!/usr/bin/env bash
# Рендер PlantUML-диаграмм product_docs в SVG через официальный образ plantuml/plantuml.
# Использование (из любой папки):   bash product_docs/render_diagrams.sh [путь-к-.puml ...]
# Без аргументов обрабатывает все product_docs/**/seq/*.puml. Нужен запущенный Docker.
# SVG кладётся рядом с .puml. Если PlantUML нашёл синтаксическую ошибку, скрипт завершается с кодом 1.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -gt 0 ]; then
  files=("$@")
else
  mapfile -t files < <(find "$root" -path '*/seq/*.puml' | sort)
fi

# Windows (Git Bash): путь для -v нужен в виде C:/..., без конвертации путей MSYS внутри контейнера
if pwd -W >/dev/null 2>&1; then to_host() { (cd "$1" && pwd -W); }; else to_host() { (cd "$1" && pwd); }; fi

status=0
for f in "${files[@]}"; do
  dir="$(dirname "$f")"; name="$(basename "$f")"
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(to_host "$dir"):/data" plantuml/plantuml \
    -tsvg -charset UTF-8 "/data/$name" >/dev/null
  svg="$dir/${name%.puml}.svg"
  if [ ! -s "$svg" ] || grep -q -i 'syntax error' "$svg"; then
    echo "ОШИБКА: $name — PlantUML не смог разобрать диаграмму" >&2
    status=1
  else
    echo "OK: $name -> ${name%.puml}.svg"
  fi
done
exit $status
