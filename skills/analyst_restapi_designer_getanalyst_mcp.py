"""
GetAnalyst REST API Designer — MCP-инструмент трассировки постановки на метод.

Инструменты:
  - register_rest_api_method — сохраняет готовую постановку на REST API-метод
    как артефакт проекта и регистрирует её как COMP-узел в графе трассировки
    5.1, связанный с исходным требованием (обычно FR-XXX или UC-XXX) связью
    `satisfies`.

Зачем: analyst_restapi_designer_getanalyst — чисто prompt-скилл без MCP,
результат (постановка на метод) нигде не оседал в графе трассировки 5.1.
Задача KON-24 (обратная связь Марии Кубениной, 2026-09-07): результат
СА-скиллов может «уплыть» от бизнес-требований без прослеживаемости.
Сделано по аналогии с analyst_db_designer_getanalyst_mcp.py.

Хранение: тот же JSON-репозиторий (edge list), что и в 5.1/7.1 —
projects/{project_id}/output/data/{project_id}_traceability_repo.json.
Артефакт (постановка): projects/{project_id}/output/specs/.

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

mcp = FastMCP("GetAnalyst_RestAPI_Designer")

REPO_FILENAME = "traceability_repo.json"


# ---------------------------------------------------------------------------
# Утилиты — репозиторий 5.1 (helpers дублируются по конвенции проекта —
# каждый *_mcp.py работает как независимый процесс; см. analyst_db_designer_getanalyst_mcp.py)
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
    logger.info(f"Репозиторий 5.1 обновлён (GetAnalyst REST API Designer): {path}")


def _register_component(project_id: str, component_id: str, title: str, source_artifact: str) -> str:
    """Регистрирует постановку на метод как COMP-узел в репозитории 5.1 (статус confirmed —
    это готовый технический артефакт, а не черновик требования)."""
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
        "source": "getanalyst_restapi_designer",
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
# Регистрация постановки на REST API-метод
# ---------------------------------------------------------------------------

@mcp.tool()
def register_rest_api_method(
    project_id: str,
    component_id: str,
    title: str,
    spec_md: str,
    source_req_id: str = "",
    rationale: str = "",
) -> str:
    """
    Сохраняет готовую постановку на REST API-метод, спроектированную по правилам
    analyst_restapi_designer_getanalyst, и регистрирует её в графе трассировки
    5.1 как COMP-узел, связанный с исходным требованием связью `satisfies`.

    Вызывать после того, как постановка полностью спроектирована и прошла
    чек-лист качества скилла — этот инструмент не проектирует и не проверяет
    сам контракт метода.

    Args:
        project_id:      Идентификатор проекта (как во всех задачах BABOK).
        component_id:    ID компонента для графа 5.1, например
                         "COMP-API-POST-Applications". Использовать префикс
                         COMP-API-, чтобы отличать от БД/UI/кода.
        title:           Название метода: «[POST /applications] Создать заявку».
        spec_md:         Готовая постановка целиком (по шаблону GetAnalyst) —
                         без авторских рассуждений и блока «Нюансы и рекомендации».
        source_req_id:   ID требования, для которого спроектирован метод —
                         обычно FR-XXX или UC-XXX. Если оставить пустым, метод
                         будет зарегистрирован, но без связи `satisfies` —
                         предупредить пользователя, что это orphan-компонент.
        rationale:       Обоснование связи. По умолчанию — типовая формулировка.

    Returns:
        Подтверждение сохранения файла + результат регистрации в 5.1.
    """
    logger.info(f"register_rest_api_method: {component_id} в проекте '{project_id}'")

    safe_id = component_id.lower().replace("-", "_")
    safe_title = title.lower().replace(" ", "_").replace("/", "_")[:30]

    md_filename = f"{safe_id}_{safe_title}.md"
    md_path = _save_spec(spec_md, project_id, md_filename)

    reg_note = _register_component(project_id, component_id, title, md_path)

    if source_req_id:
        link_note = _add_satisfies_link(
            project_id,
            component_id,
            source_req_id,
            rationale or f"Постановка REST API-метода (GetAnalyst REST API Designer) реализует {source_req_id}.",
        )
    else:
        link_note = (
            f"⚠️ `source_req_id` не передан — `{component_id}` зарегистрирован как orphan-компонент "
            f"без связи `satisfies`. Уточни у BA исходное требование (обычно FR-XXX или UC-XXX) и "
            f"добавь связь через `add_trace_link` (5.1) либо повторным вызовом этого инструмента."
        )

    return (
        f"✅ Постановка сохранена: `{md_path}`\n\n"
        f"**Регистрация в 5.1:** {reg_note}\n"
        f"**Связь:** {link_note}"
    )


if __name__ == "__main__":
    mcp.run()
