"""Тезисы правил клиента из комментариев эпика (KON-160): API и сбор результата LLM."""
from .factory import task_handler
from .service import PURPOSE, ClientRulesError, ClientRulesService, build_client_rules

__all__ = [
    "PURPOSE", "ClientRulesError", "ClientRulesService", "build_client_rules", "task_handler",
]
