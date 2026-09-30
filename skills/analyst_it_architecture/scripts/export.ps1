# export.ps1
# Экспорт архитектурных артефактов в файловую структуру
#
# Использование:
#   .\scripts\export.ps1 -SystemName "Мой проект" -OutputPath "C:\path\to\output"
#   .\scripts\export.ps1 -SystemName "Мой проект" -Profile enterprise
#
# Создаёт:
#   /[SystemName]/
#   ├── _metadata.md
#   ├── 01-architecture-blueprint.md
#   ├── 02-context-diagram.md
#   ├── 03-container-diagram.md
#   ├── 04-component-diagram.md
#   ├── 05-deployment-diagram.md
#   ├── 06-api-specification.md
#   ├── 07-data-model.md
#   ├── 08-data-flow.md
#   ├── 09-system-pattern.md
#   ├── 10-security-architecture.md
#   ├── 11-nfr-matrix.md
#   ├── 12-technology-stack.md
#   ├── 13-integration-patterns.md
#   └── 14-architecture-decision-records.md

param(
    [Parameter(Mandatory=$true)]
    [string]$SystemName,

    [Parameter(Mandatory=$false)]
    [string]$OutputPath = (Get-Location).Path,

    [Parameter(Mandatory=$false)]
    [ValidateSet("quick", "standard", "enterprise", "startup")]
    [string]$Profile = "standard"
)

# Санирование имени системы для использования в путях
$SafeName = $SystemName -replace '[\\/:*?"<>|]', '_' -replace '\s+', '-'

$BaseDir = Join-Path $OutputPath $SafeName

# Список артефактов с порядковыми номерами и заголовками
$Artifacts = @(
    @{ Number = "01"; Name = "architecture-blueprint"; Title = "Architecture Blueprint" }
    @{ Number = "02"; Name = "context-diagram"; Title = "C4 Context Diagram" }
    @{ Number = "03"; Name = "container-diagram"; Title = "C4 Container Diagram" }
    @{ Number = "04"; Name = "component-diagram"; Title = "C4 Component Diagram" }
    @{ Number = "05"; Name = "deployment-diagram"; Title = "C4 Deployment Diagram" }
    @{ Number = "06"; Name = "api-specification"; Title = "API Specification" }
    @{ Number = "07"; Name = "data-model"; Title = "Data Model" }
    @{ Number = "08"; Name = "data-flow"; Title = "Data Flow" }
    @{ Number = "09"; Name = "system-pattern"; Title = "System Pattern" }
    @{ Number = "10"; Name = "security-architecture"; Title = "Security Architecture" }
    @{ Number = "11"; Name = "nfr-matrix"; Title = "NFR Matrix" }
    @{ Number = "12"; Name = "technology-stack"; Title = "Technology Stack" }
    @{ Number = "13"; Name = "integration-patterns"; Title = "Integration Patterns" }
    @{ Number = "14"; Name = "architecture-decision-records"; Title = "Architecture Decision Records" }
)

# Фильтрация артефактов по профилю
$ExcludeMap = @{
    "quick"   = @("component-diagram", "integration-patterns")
    "startup" = @("component-diagram")
    "standard" = @()
    "enterprise" = @()
}

$ExtraMap = @{
    "enterprise" = @(
        @{ Number = "15"; Name = "compliance-mapping"; Title = "Compliance Mapping" }
        @{ Number = "16"; Name = "migration-strategy"; Title = "Migration Strategy" }
    )
}

$Filtered = $Artifacts | Where-Object { $_.Name -notin $ExcludeMap[$Profile] }
if ($ExtraMap[$Profile]) {
    $Filtered = $Filtered + $ExtraMap[$Profile]
}

# Создание базовой директории
Write-Host "Создание структуры для: $SafeName" -ForegroundColor Cyan
Write-Host "Путь: $BaseDir`n" -ForegroundColor Gray

New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null

# Создание placeholder-файлов для каждого артефакта
foreach ($artifact in $Filtered) {
    $FileName = "${artifact.Number}-${artifact.Name}.md"
    $FilePath = Join-Path $BaseDir $FileName

    $Placeholder = @"
# ${artifact.Title}

**Система:** ${SystemName}
**Дата:** $(Get-Date -Format 'yyyy-MM-dd')
**Статус:** placeholder

## Содержание

[Заполнить артефакт]

---

"@

    Set-Content -Path $FilePath -Value $Placeholder -Encoding UTF8
    Write-Host "  [ Created ] $FileName" -ForegroundColor DarkGray
}

# Создание _metadata.md
$Metadata = @"
# ${SystemName} — Metadata

**Система:** ${SystemName}
**Тип:** [Шаблон]
**Профиль:** [Профиль]
**Дата генерации:** $(Get-Date -Format 'yyyy-MM-dd')
**Версия:** 1.0.0

## Структура артефактов

| № | Файл | Статус |
|---|------|--------|
$(foreach ($a in $Filtered) { "  | $($a.Number) | $($a.Name) | черновик |" })

## История версий

| Версия | Дата | Изменения |
|--------|------|-----------|
| 1.0.0 | $(Get-Date -Format 'yyyy-MM-dd') | Первоначальная генерация |

---

"@

Set-Content -Path (Join-Path $BaseDir "_metadata.md") -Value $Metadata -Encoding UTF8
Write-Host "`n  [ Created ] _metadata.md" -ForegroundColor DarkGray
Write-Host "`nГотово. Артефакты в: $BaseDir" -ForegroundColor Green
