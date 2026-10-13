#!/usr/bin/env bash
# Проверка отсутствия секретов в репозитории (и, с --images, в собранных образах).
# Сканирует отслеживаемые и неотслеживаемые, но не игнорируемые файлы кода и конфигурации.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0

# 1. .env не должен быть в индексе
if git ls-files --error-unmatch .env >/dev/null 2>&1; then echo "FAIL  .env в индексе Git"; fail=1; fi
# 2. .env должен игнорироваться
if ! git check-ignore -q .env; then echo "FAIL  .env не в .gitignore"; fail=1; fi

# 3. Блок re_web_presale в шаблоне не содержит значений (блок Confluence платформы не проверяется)
if sed -n '/re_web_presale (KON-32)/,$p' .env.example | tr -d '
' | grep -E '^[A-Z_]+=.+' ; then echo "FAIL  .env.example содержит значения"; fail=1; fi

# 4. Паттерны секретов (output/, docs/, skills/ — артефакты BABOK и документация, не код)
pat_keys='-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}'
pat_assign='(password|passwd|secret|api[_-]?key|token|enc_key)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9+/_=.-]{12,}'
files=$(git ls-files --cached --others --exclude-standard \
  | grep -vE '^(output|docs|skills|input|estimate|architecture|tests)/|package-lock\.json$|^scripts/check_secrets\.sh$|^\.env\.example$|\.md$')
if [ -n "$files" ]; then
  hits=$(echo "$files" | xargs grep -nIE "$pat_keys" 2>/dev/null)
  hits2=$(echo "$files" | xargs grep -nIEi "$pat_assign" 2>/dev/null | grep -vE '\$\{|<[A-Za-z_]+>|os\.environ|os\.urandom|getenv|:\?|self\.|config\.|body\.|external_key|:'"'"'|FAKE_|[\"']test-[a-z0-9-]+[\"']')
  if [ -n "$hits$hits2" ]; then echo "FAIL  похожие на секреты строки:"; echo "$hits"; echo "$hits2" | sed -E 's/(=|:)[[:space:]]*.*/\1 <скрыто>/'; fail=1; fi
fi

# 5. Образы (--images): нет .env и ключей в слоях, нет секретов в ENV образа
if [ "${1:-}" = "--images" ]; then
  for img in $(docker compose config --images 2>/dev/null | grep -E 're_web_presale|^re_web'); do
    if docker run --rm --entrypoint sh "$img" -c 'find / -xdev \( -name ".env" -o -name "*.pem" -o -name "id_rsa" \) -not -path "/proc/*" -not -path "/usr/*" -not -path "/etc/ssl*" -not -path "/etc/pki/*" 2>/dev/null | grep -q .'; then
      echo "FAIL  образ $img содержит .env/ключи"; fail=1
    fi
    if docker image inspect "$img" --format '{{json .Config.Env}}' | grep -qiE 'password|secret|enc_key|token'; then
      echo "FAIL  ENV образа $img содержит секретоподобные имена"; fail=1
    fi
  done
fi

if [ $fail -eq 0 ]; then echo "ИТОГ: секретов не найдено"; else echo "ИТОГ: ОШИБКА"; fi
exit $fail
