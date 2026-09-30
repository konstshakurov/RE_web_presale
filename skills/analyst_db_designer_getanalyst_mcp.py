"""
GetAnalyst DB Designer — MCP-инструмент трассировки физической модели БД.

Инструменты:
  - register_physical_data_model — сохраняет физ. модель/DDL как артефакт проекта
    и регистрирует её как COMP-узел в графе трассировки 5.1, связанный с исходным
    требованием (обычно ERD-XXX из 7.1) связью `satisfies`.

Зачем: analyst_db_designer_getanalyst — чисто prompt-скилл без MCP, поэтому
результат (физическая модель, DDL) нигде не оседал в графе трассировки 5.1 —
не было связи с бизнес-требованием, из-за которого модель проектировалась.
Задача KON-24 (обратная связь Марии Кубениной, 2026-09-07): результат СА-скиллов
может «уплыть» от бизнес-требований без прослеживаемости. Здесь этот пробел
закрывается для физической модели БД; конвейер 7.1 create_erd не заменяется —
это разные уровни одного домена (концептуальная ERD-требование vs production
физ. модель).

Хранение: тот же JSON-репозиторий (edge list), что и в 5.1/7.1 —
projects/{project_id}/output/data/{project_id}_traceability_repo.json.
Артефакты (описание + DDL): projects/{project_id}/output/specs/.

# Copyright (c) 2026 Anatoly Chaussky. AI-powered Platform AInalyst (AI Платформа AIналитик). Licensed under AGPL v3. Commercial licensing: chaussky@gmail.com
"""

import sys
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR.parent))

import json
import os
from datetime import date
from mcp.server.fastmcp import FastMCP
from skills.common import logger, data_path, normalize_project_id, specs_dir

mcp = FastMCP("GetAnalyst_DB_Designer")

REPO_FILENAME = "traceability_repo.json"


# ---------------------------------------------------------------------------
# Утилиты — репозиторий 5.1 (те же файл и формат, что у requirements_spec_mcp.py
# и requirements_traceability_mcp.py; helpers дублируются по конвенции проекта —
# каждый *_mcp.py работает как независимый процесс)
# ---------------------------------------------------------------------------

def _repo_path(project_id: str) -> str:
    safe = normalize_project_id(project_id)
    return data_path(project_id, f"{safe}_{REPO_FILENAME}")


def _load_repo(project_id: str) -> dict:
    path = _repo_path(project_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "project": project_id,
        "formality_level": "Standard",
        "created": str(date.today()),
        "updated": str(date.today()),
        "requirements": [],
        "links": [],
        "history": [],
    }


def _save_repo(repo: dict) -> None:
    path = _repo_path(repo["project"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    repo["updated"] = str(date.today())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(repo, f, ensure_ascii=False, indent=2)
    logger.info(f"Репозиторий 5.1 обновлён (GetAnalyst DB Designer): {path}")


def _register_component(project_id: str, component_id: str, title: str, source_artifact: str) -> str:
    """Регистрирует физ. модель БД как COMP-узел в репозитории 5.1 (статус confirmed —
    в отличие от draft у 7.1: это готовый технический артефакт, а не черновик требования)."""
    repo = _load_repo(project_id)
    existing_ids = {r["id"] for r in repo["requirements"]}

    if component_id in existing_ids:
        logger.info(f"_register_component: {component_id} уже в репозитории, пропускаем")
        return f"ℹ️ `{component_id}` уже зарегистрирован в репозитории 5.1."

    entry = {
        "id": component_id,
        "type": "component",
        "title": title,
        "version": "1.0",
        "status": "confirmed",
        "priority": "",
        "owner": "",
        "stability": "Unknown",
        "source_artifact": source_artifact,
        "added": str(date.today()),
        "last_reviewed": str(date.today()),
    }
    repo["requirements"].append(entry)
    repo["history"].append({
        "action": "requirement_added",
        "req_id": component_id,
        "source": "getanalyst_db_designer",
        "date": str(date.today()),
    })
    _save_repo(repo)
    return f"✅ `{component_id}` зарегистрирован в репозитории 5.1 (тип: component, статус: confirmed)."


def _add_satisfies_link(project_id: str, component_id: str, source_req_id: str, rationale: str) -> str:
    """Добавляет связь component satisfies source_req_id. Не дублирует существующую связь."""
    repo = _load_repo(project_id)

    for lnk in repo["links"]:
        if lnk["from"] == component_id and lnk["to"] == source_req_id and lnk["relation"] == "satisfies":
            return f"ℹ️ Связь `{component_id} --[satisfies]--> {source_req_id}` уже существует."

    repo["links"].append({
        "from": component_id,
        "to": source_req_id,
        "relation": "satisfies",
        "rationale": rationale,
        "added": str(date.today()),
    })
    repo["history"].append({
        "action": "link_added",
        "from": component_id,
        "to": source_req_id,
        "relation": "satisfies",
        "date": str(date.today()),
    })
    _save_repo(repo)
    return f"✅ Связь `{component_id} --[satisfies]--> {source_req_id}` добавлена в репозиторий 5.1."


def _save_spec(content: str, project_id: str, filename: str) -> str:
    """Сохраняет артефакт в projects/{project_id}/output/specs/."""
    out_dir = specs_dir(project_id)
    os.makedirs(out_dir, exist_ok=True)
    filepath = os.path.join(out_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Спецификация сохранена: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Регистрация физической модели БД
# ---------------------------------------------------------------------------

@mcp.tool()
def register_physical_data_model(
    project_id: str,
    component_id: str,
    title: str,
    description_md: str,
    ddl_sql: str,
    source_req_id: str = "",
    rationale: str = "",
) -> str:
    """
    Сохраняет физическую модель БД (описание + DDL), спроектированную по правилам
    analyst_db_designer_getanalyst, и регистрирует её в графе трассировки 5.1 как
    COMP-узел, связанный с исходным требованием связью `satisfies`.

    Вызывать после того, как физическая модель полностью спроектирована и проверена
    (после самопроверки согласованности описание/диаграмма/SQL из рабочего процесса
    скилла), а не вместо проектирования — этот инструмент не создаёт и не проверяет
    саму модель.

    Args:
        project_id:      Идентификатор проекта (как во всех задачах BABOK).
        component_id:    ID компонента для графа 5.1, например "COMP-DB-Application".
                         Использовать префикс COMP-DB-, чтобы отличать от кода/UI.
        title:           Название модели: «Физическая модель — Заявки на кредит».
        description_md:  Готовое markdown-описание модели (сущности, ключи, индексы,
                         решения и риски) — уже подготовленное по стандартам скилла.
        ddl_sql:         Готовый DDL-скрипт (CREATE TABLE и т.д.).
        source_req_id:   ID требования, которое реализует модель — обычно ERD-XXX
                         из 7.1 (create_erd), иногда FR-XXX. Если оставить пустым,
                         модель будет зарегистрирована, но без связи `satisfies` —
                         предупредить пользователя, что это orphan-компонент.
        rationale:       Обоснование связи. По умолчанию — типовая формулировка.

    Returns:
        Подтверждение сохранения файлов + результат регистрации в 5.1.
    """
    logger.info(f"register_physical_data_model: {component_id} в проекте '{project_id}'")

    safe_id = component_id.lower().replace("-", "_")
    safe_title = title.lower().replace(" ", "_")[:30]

    md_filename = f"{safe_id}_{safe_title}.md"
    sql_filename = f"{safe_id}_{safe_title}.sql"

    md_path = _save_spec(description_md, project_id, md_filename)
    sql_path = _save_spec(ddl_sql, project_id, sql_filename)

    reg_note = _register_component(project_id, component_id, title, md_path)

    if source_req_id:
        link_note = _add_satisfies_link(
            project_id,
            component_id,
            source_req_id,
            rationale or f"Физическая модель БД (GetAnalyst DB Designer) реализует {source_req_id}.",
        )
    else:
        link_note = (
            f"⚠️ `source_req_id` не передан — `{component_id}` зарегистрирован как orphan-компонент "
            f"без связи `satisfies`. Уточни у BA исходное требование (обычно ERD-XXX из 7.1) и "
            f"добавь связь через `add_trace_link` (5.1) либо повторным вызовом этого инструмента."
        )

    return (
        f"✅ Физическая модель сохранена:\n"
        f"- Описание: `{md_path}`\n"
        f"- DDL: `{sql_path}`\n\n"
        f"**Регистрация в 5.1:** {reg_note}\n"
        f"**Связь:** {link_note}"
    )


if __name__ == "__main__":
    mcp.run()
