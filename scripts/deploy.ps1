# Однострочный автодеплой с машины владельца (KON-123, вариант 3).
#
# Использование (PowerShell, из корня репозитория):
#   .\scripts\deploy.ps1
#   .\scripts\deploy.ps1 -SkipFrontendBuild        # быстрее, но не по правилам приёмки
#
# Что делает:
#   1. проверки до выкладки: чистое дерево, HEAD запушен, ветка main,
#      backend unit-тесты, сборка frontend, check_secrets.sh;
#      красная проверка отменяет деплой ДО отправки на ВМ;
#   2. git archive HEAD (список путей как в infra/deploy.md §1);
#   3. scp архива и scripts/deploy.sh на ВМ (ключ id_ed25519_vm_re,
#      парольная фраза вводится владельцем / берётся из ssh-agent);
#   4. ssh: bash /tmp/re_deploy.sh <hash> <archive> — сам ВМ-скрипт
#      делает миграции/сборку/health/откат, см. scripts/deploy.sh;
#   5. сверка DEPLOYED_VERSION и итоговая сводка.
#
# Секреты: не хранит и не печатает; фраза ключа и .env на ВМ не трогаются.
[CmdletBinding()]
param(
  [string]$VmHost = "vm-re.simbirsoft",
  [string]$VmUser = "user",
  [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519_vm_re",
  [switch]$SkipBackendTests,
  [switch]$SkipFrontendBuild,
  [switch]$SkipSecretsCheck,
  [switch]$AllowNonMain
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # ...\RE_web_presale
Set-Location $repo
& git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "!! $repo не git-репозиторий" -ForegroundColor Red; exit 1 }

function Step($msg) { Write-Host (">> " + $msg) -ForegroundColor Cyan }
function Stop-D($msg) { Write-Host ("!! " + $msg) -ForegroundColor Red; exit 1 }

# --- 0. окружение: ssh ключ есть, ssh доступен ---
if (-not (Test-Path $KeyPath)) {
  Stop-D "нет ключа $KeyPath (проверь путь или параметр -KeyPath)"
}
$sshBase = @("-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "$VmUser@$VmHost")
Step "ssh доступ: ssh $VmUser@$VmHost"
$sshProbe = & ssh @sshBase "echo ok" 2>$null
if ($sshProbe -notmatch "ok") {
  Stop-D "ssh не прошёл в BatchMode: помести ключ в ssh-agent (ssh-add $KeyPath) — парольную фразу вводит владелец"
}

# --- 1. git: чистое дерево, ветка main, HEAD запушен ---
Step "проверки git"
$dirty = & git status --short
if ($dirty) { Stop-D "рабочее дерево не чистое:`n$dirty" }
$branch = (& git rev-parse --abbrev-ref HEAD)
if (-not $AllowNonMain -and $branch -ne "main") {
  Stop-D "HEAD не в main ($branch). Автодеплой для main; для другой ветки — параметр -AllowNonMain"
}
& git fetch origin 2>$null
$local = & git rev-parse HEAD
$remote = & git rev-parse "origin/$branch"
if ($local -ne $remote) { Stop-D "HEAD ($local) не запушен (origin/$branch = $remote). Сначала git push" }
$short = (& git rev-parse --short HEAD)
Step "версия на выкладку: $branch @ $short"

# --- 2. проверки до выкладки (красная - деплой не стартует) ---
if (-not $SkipBackendTests) {
  Step "backend unit-тесты (pytest tests/unit)"
  & python -m pytest backend/tests/unit -q
  if ($LASTEXITCODE -ne 0) { Stop-D "unit-тесты backend красные — деплой отменён" }
} else { Write-Host "!! backend тесты пропущены (-SkipBackendTests)" -ForegroundColor Yellow }

if (-not $SkipFrontendBuild) {
  Step "сборка frontend (npm run build)"
  Push-Location "$repo\frontend"
  try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { Stop-D "сборка frontend упала — деплой отменён" }
  } finally { Pop-Location }
} else { Write-Host "!! сборка frontend пропущена (-SkipFrontendBuild)" -ForegroundColor Yellow }

if (-not $SkipSecretsCheck) {
  Step "check_secrets.sh"
  if (Get-Command bash -ErrorAction SilentlyContinue) {
    & bash scripts/check_secrets.sh
    if ($LASTEXITCODE -ne 0) { Stop-D "check_secrets.sh нашёл секреты — деплой отменён" }
  } else { Write-Host "!! bash не найден - check_secrets.sh пропущен (не по правилам приёмки)" -ForegroundColor Yellow }
}

# --- 3. git archive (пути как в infra/deploy.md §1) ---
Step "git archive HEAD"
$paths = "backend", "frontend", "src", "db", "infra", "scripts",
         "docker-compose.yml", "docker-compose.prod.yml", "docker-compose.migrations.yml",
         ".env.example", ".dockerignore", ".gitignore", "README.md", ".gitattributes"
$archive = Join-Path $env:TEMP "re_web_presale_deploy_$short.tar.gz"
& git archive --format=tar.gz -o $archive HEAD @paths
if ($LASTEXITCODE -ne 0) { Stop-D "git archive упал" }
$sizeMb = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Step "архив: $archive ($sizeMb MB)"
if ($sizeMb -gt 20) { Stop-D "архив подозрительно большой ($sizeMb MB) - проверь список путей" }

# --- 4. локальная сборка образов (registry Docker Hub у ВМ нестабилен: сборка на ВМ убрана, KON-123) ---
Step "локальная сборка образов (docker compose build api web worker)"
& docker compose build api web worker
if ($LASTEXITCODE -ne 0) { Stop-D "локальная сборка образов упала — деплой отменён" }

$imgTar = Join-Path $env:TEMP "re_images_$short.tar.gz"
Step "docker save + gzip -> $imgTar"
if (Test-Path $imgTar) { Remove-Item $imgTar -Force }
& bash -c "docker save re_web_presale-api re_web_presale-web re_web_presale-worker | gzip > '$imgTar'"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $imgTar)) { Stop-D "docker save не удался" }
$imgMb = [math]::Round((Get-Item $imgTar).Length / 1MB, 1)
Step "образы упакованы: $imgMb MB"

# --- 5. доставка на ВМ ---
Step "scp архива кода, образов и deploy.sh на ВМ"
$scpBase = @("-i", $KeyPath, "-o", "BatchMode=yes")
& scp @scpBase $archive "${VmUser}@${VmHost}:/tmp/re_web_presale_deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { Stop-D "scp архива не удался" }
& scp @scpBase $imgTar "${VmUser}@${VmHost}:/tmp/re_images.tar.gz"
if ($LASTEXITCODE -ne 0) { Stop-D "scp образов не удался" }
& scp @scpBase "$repo\scripts\deploy.sh" "${VmUser}@${VmHost}:/tmp/re_deploy.sh"
if ($LASTEXITCODE -ne 0) { Stop-D "scp deploy.sh не удался" }

Step "docker load образов на ВМ"
& ssh @sshBase "docker load -i /tmp/re_images.tar.gz"
if ($LASTEXITCODE -ne 0) { Stop-D "docker load на ВМ не удался" }
Remove-Item $imgTar, $archive -ErrorAction SilentlyContinue

# --- 6. деплой на ВМ (deploy.sh: миграции/up/health/rollback/журнал; сборки на ВМ нет) ---
Step "запуск /tmp/re_deploy.sh $short на ВМ"
& ssh @sshBase "bash /tmp/re_deploy.sh $short /tmp/re_web_presale_deploy.tar.gz"
if ($LASTEXITCODE -ne 0) {
  Stop-D "деплой на ВМ не удался (код $LASTEXITCODE). Журнал: ssh $VmUser@$VmHost tail -50 /opt/re_web_presale/deploy.log"
}

# --- 7. финальная сверка ---
Step "сверка DEPLOYED_VERSION"
$remote = & ssh @sshBase "cat /opt/re_web_presale/DEPLOYED_VERSION"
if ($remote -ne $short) { Stop-D "DEPLOYED_VERSION ($remote) не совпал с ожиданием ($short)" }
Write-Host ""
Write-Host ("=== деплой ок: {0} -> {1} ===" -f $VmHost, $remote) -ForegroundColor Green
Write-Host "журнал: ssh $VmUser@$VmHost tail -20 /opt/re_web_presale/deploy.log"
