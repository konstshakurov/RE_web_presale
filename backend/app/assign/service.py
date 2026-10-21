"""Назначение кандидата, смена статуса потребности и комментарии в Jira (KON-43, этап 2).

Основания: FR-008/009/010/013/018 (назначение), FR-011/026 (статус-действия по белому списку),
FR-029 (отказ Jira - текст как есть, введённое не теряется), NFR-009 (вызов Jira <= 10 с -
лимит задаёт адаптер), NFR-006 (в логах нет паролей, токенов и текстов комментариев).

Принципы:
* всё пишется токеном самого пользователя: действия видны в Jira под его учёткой (BR-001), своих
  ограничений приложение не вводит - только белый список статус-действий (FR-026) и проверка
  обязательных полей ДО отправки (проба KON-30);
* перед записью потребность и её переходы перечитываются (FR-028); после - перечитываются снова
  и кэш обновляется (отказ перечитывания операцию не отменяет);
* справочники (причины, НДС, валюта, единица) берутся из ответа Jira и не хранятся в приложении;
* повторное назначение: побеждает последний (FR-010); переход «Кандидат назначен» не
  выполняется, если потребность уже в этом статусе, - тогда пишутся поля, файлы и комментарий;
* при сбое посреди назначения ответ несёт ``completed`` - выполненные шаги; повтор с ними не
  дублирует вложения и комментарии;
* ставка вносится при назначении и пишется отдельным вызовом ScriptRunner (``JiraPort.set_rate``,
  проба KON-99, CR-008) после успешного назначения - только если «Кто предлагает ставку» =
  «Производство»; при «Клиент» не отправляется. Сбой записи ставки - частичный успех: назначение
  не откатывается, ответ несёт ``rate_pending`` и предупреждение, повтор - тем же запросом с
  ``completed``. При «Найм» (801) ставка не вводится и в поля перехода не передаётся (FR-011);
* Jira - публичный ``JiraPort`` (``JiraAdapter``); приватных методов адаптера сервис не трогает.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from ..integrations.jira import (
    CODE_JIRA_ERROR,
    MAX_RATE_RUB,
    RATE_CURRENCY_RUB,
    RATE_PROPOSER_PRODUCTION,
    RATE_UNIT_HOUR,
    RATE_VAT_YES,
    JiraIntegrationError,
    JiraPort,
    TransitionField,
    TransitionView,
)
from ..sync.mapping import (
    F_PROFILE,
    F_RATE,
    F_RATE_CURRENCY,
    F_RATE_UNIT,
    F_RATE_VAT,
    NEED_FIELDS,
    NeedRow,
    map_need,
)

log = logging.getLogger(__name__)

STATUS_ASSIGNED = "Кандидат назначен"
#: Белый список действий РН/РО/Presale (FR-026, design/KON-79/status_actions.md):
#: действие -> (целевой статус Jira, подпись кнопки). «Кандидат назначен» - только «Назначить».
ACTIONS: dict[str, tuple[str, str]] = {
    "search": ("Поиск кандидата", "Поиск"),
    "hire": ("Найм", "Найм"),
    "no_candidates": ("Кандидатов нет", "Кандидатов нет"),
}
#: «Причина отсутствия кандидата»: id из пробы KON-30; поле ищется по имени в ответе Jira,
#: id - запасной (ISS-010).
F_REASON = "customfield_16100"
#: «Нужно предоставить CV» (правило клиента): при «Да» Jira не пропускает переход 21 из-за
#: экранного валидатора «Вложение» (проба KON-42, D1; CR-006).
F_NEED_CV = "customfield_11506"
CV_RESTORE_WARNING = (
    "Значение 11506 «Нужно предоставить CV» осталось «Нет»: верните «Да» вручную в Jira")
CURRENCY_NAMES = {"RUB": "руб.", "USD": "usd", "KZT": "kzt"}

STEP_FIELDS = "fields"
STEP_TRANSITION = "transition"
STEP_COMMENT = "comment"
STEP_RATE = "rate"
PROPOSER_PRODUCTION = "от производства"
STEP_ATTACH = "attach:"  # + id файла


class AssignError(Exception):
    """Отказ операции. ``message`` - текст Jira как есть либо понятная причина до отправки."""

    def __init__(self, code: str, message: str, *, step: str | None = None,
                 completed: list[str] | None = None, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.step = step
        self.completed = list(completed or [])
        self.status = status
        super().__init__(f"[{code}] {message}")


# --- вход/выход ---

@dataclass(frozen=True)
class AssignInput:
    employee_key: str
    resume_file_id: int | None = None  # None - Основное CV
    extra_file_ids: tuple[int, ...] = ()
    rate_rub: Decimal | None = None  # «Ставка, руб. с НДС»; обязательна при «от производства»
    comment: str = ""
    completed: tuple[str, ...] = ()  # шаги, уже выполненные прошлой попыткой


@dataclass(frozen=True)
class FieldOut:
    field_id: str
    name: str
    required: bool
    kind: str  # reason | rate | vat | currency | unit | other
    options: tuple[tuple[str, str], ...]  # (id, имя) из Jira
    prefill_id: str | None


@dataclass(frozen=True)
class ActionOut:
    action: str
    label: str
    target_status: str
    comment_required: bool
    fields: tuple[FieldOut, ...]


@dataclass(frozen=True)
class ActionsOut:
    need_key: str
    status: str
    actions: tuple[ActionOut, ...]


@dataclass(frozen=True)
class OpResult:
    need_key: str
    status: str | None  # статус после операции; None - перечитать не удалось
    refreshed: bool
    steps: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)  # операция прошла, но (FR-029)
    rate_pending: bool = False  # назначение прошло, ставка не записана - повтор возможен


class EmployeeSource(Protocol):
    def get_profile(self, employee_key: str) -> tuple[bool, str | None]:
        """(карточка есть, jira_login для поля «Профиль сотрудника»)."""
        ...


class FileSource(Protocol):
    def get_file(self, file_id: int) -> Any: ...
    def get_content(self, file_id: int) -> tuple[Any, bytes] | None: ...
    def current_main(self, employee_key: str) -> Any: ...


class NeedCache(Protocol):
    def upsert_needs(self, rows: list[NeedRow]) -> int: ...


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _kind(spec: TransitionField) -> str:
    if spec.field_id == F_REASON or "причина отсутствия" in _norm(spec.name):
        return "reason"
    kinds = {F_RATE: "rate", F_RATE_VAT: "vat", F_RATE_CURRENCY: "currency", F_RATE_UNIT: "unit"}
    return kinds.get(spec.field_id, "other")


def _pick(spec: TransitionField, wanted: str | None) -> str | None:
    """Id значения справочника Jira по имени (без учёта регистра; «ч» ~ «ч (час)»)."""
    if not wanted:
        return None
    w = _norm(wanted)
    for v in spec.allowed_values:
        if _norm(v.name) == w:
            return v.id
    for v in spec.allowed_values:
        if _norm(v.name).startswith(w + " ") or w.startswith(_norm(v.name) + " "):
            return v.id
    return None


def _prefill(spec: TransitionField, need: NeedRow | None) -> str | None:
    """Ставка перехода «Найм» предзаполняется из потребности (FR-011)."""
    if need is None:
        return None
    kind = _kind(spec)
    if kind == "vat" and need.rate_includes_vat is not None:
        return _pick(spec, "Да" if need.rate_includes_vat else "Нет")
    if kind == "currency" and need.rate_currency:
        return _pick(spec, CURRENCY_NAMES.get(need.rate_currency, need.rate_currency))
    if kind == "unit":
        return _pick(spec, need.rate_unit)
    return None


class AssignService:
    def __init__(
        self,
        jira: JiraPort,
        token_of: Callable[[str], str | None],
        employees: EmployeeSource | None,
        files: FileSource | None,
        cache: NeedCache | None,
        profile_field: str = F_PROFILE,
    ) -> None:
        self._jira = jira
        self._token_of = token_of
        self._employees = employees
        self._files = files
        self._cache = cache
        self._profile_field = profile_field

    # --- общее ---

    def _token(self, login: str) -> str:
        token = self._token_of(login)
        if not token:
            raise AssignError("needs_reauthorization",
                              "Сессия Jira не создана: введите пароль Jira")
        return token

    def _jira_call(self, fn, *, step: str | None = None, completed: list[str] | None = None):
        """Отказы Jira -> AssignError с текстом Jira и кодом единой карты (FR-029)."""
        from ..integrations.jira import classify

        try:
            return fn()
        except AssignError:
            raise
        except JiraIntegrationError as exc:
            raise self._from_integration(exc, step, completed) from None
        except Exception as exc:  # noqa: BLE001 - неизвестное тоже сводим к карте
            raise self._from_integration(classify(exc), step, completed) from None

    @staticmethod
    def _from_integration(exc, step, completed) -> AssignError:
        from ..logging_setup import redact

        return AssignError(exc.code, redact(exc.message)[:2000], step=step, completed=completed,
                           status=exc.status)

    def _read_need(self, token: str, key: str) -> tuple[NeedRow, str]:
        issue = self._jira_call(lambda: self._jira.get_issue(token, key, fields=list(NEED_FIELDS)))
        need = map_need(issue) if isinstance(issue, dict) else None
        if need is None:
            raise AssignError("need_not_found", f"Потребность {key} не прочитана из Jira")
        return need, need.status

    def _refresh(self, token: str, key: str) -> tuple[str | None, bool]:
        """Перечитать потребность и обновить кэш; отказ не отменяет выполненную операцию."""
        try:
            need, status = self._read_need(token, key)
            if self._cache is not None:
                self._cache.upsert_needs([need])
            return status, True
        except Exception as exc:  # noqa: BLE001
            log.warning("потребность не перечитана после записи: %s", type(exc).__name__)
            return None, False

    @staticmethod
    def _find(transitions: list[TransitionView], target: str) -> TransitionView | None:
        for t in transitions:
            if _norm(t.to_status) == _norm(target):
                return t
        return None

    @staticmethod
    def _missing(t: TransitionView, fields: dict[str, Any], skip: tuple[str, ...] = ()) -> list[str]:
        return [f.name or f.field_id for f in t.fields
                if f.required and f.field_id not in skip and not fields.get(f.field_id)]

    @staticmethod
    def _rate_to_send(rate: Decimal | None, need: NeedRow, done: list[str]) -> Decimal | None:
        """Ставка к записи; ``None`` - не отправлять (проверка до любой записи).

        Отправляется только при «Кто предлагает ставку» = «Производство» (БП-2: тогда она
        обязательна, пустая не уходит). При «Клиент» своя ставка не отправляется (CR-008).
        """
        if STEP_RATE in done or need.rate_proposer != PROPOSER_PRODUCTION:
            return None
        if rate is None:
            raise AssignError("rate_required",
                              "Укажите ставку: она обязательна для потребности «от производства»")
        if not rate.is_finite() or rate <= 0:
            raise AssignError("bad_request", "Ставка должна быть положительным числом")
        if rate > MAX_RATE_RUB:
            raise AssignError("bad_request", f"Ставка не может превышать {MAX_RATE_RUB:,} руб.".replace(",", " "))
        return rate

    # --- статус-действия (FR-011, FR-026) ---

    def list_actions(self, login: str, need_key: str) -> ActionsOut:
        """Белый список действий ∩ переходы Jira из текущего статуса. Универсального списка нет.

        Вызывается при клике на кнопку статуса (KON-96); кнопки в списке строятся из кэша без Jira.
        """
        token = self._token(login)
        need, status = self._read_need(token, need_key)
        transitions = self._jira_call(lambda: self._jira.get_transitions(token, need_key))
        out: list[ActionOut] = []
        for action, (target, label) in ACTIONS.items():
            t = self._find(transitions, target)
            if t is None:
                continue
            fields = tuple(
                FieldOut(f.field_id, f.name, f.required, _kind(f),
                         tuple((v.id, v.name) for v in f.allowed_values), _prefill(f, need))
                for f in t.fields
            )
            out.append(ActionOut(action, label, target, action == "no_candidates", fields))
        return ActionsOut(need_key, status, tuple(out))

    def change_status(
        self, login: str, need_key: str, action: str, *, comment: str = "",
        fields: dict[str, str] | None = None,
    ) -> OpResult:
        """``fields``: field_id -> id значения справочника Jira (из ``list_actions``).

        Ставка здесь не вводится и не передаётся (CR-008, FR-011): для «Найм» поля НДС, валюты
        и единицы предзаполняются из потребности.
        """
        if action not in ACTIONS:
            raise AssignError("bad_request", "Недопустимое действие со статусом")
        target, label = ACTIONS[action]
        token = self._token(login)
        need, status = self._read_need(token, need_key)  # FR-028: перечитать перед записью
        transitions = self._jira_call(lambda: self._jira.get_transitions(token, need_key))
        t = self._find(transitions, target)
        if t is None:
            raise AssignError(
                "transition_unavailable",
                f"Jira не предлагает переход в «{target}» из статуса «{status}»")
        text = (comment or "").strip()
        values: dict[str, Any] = {}
        for spec in t.fields:
            if spec.field_id == F_RATE:
                continue  # ставка вносится при назначении, а не на переходе (CR-008)
            chosen = (fields or {}).get(spec.field_id) or _prefill(spec, need)
            if chosen:
                values[spec.field_id] = {"id": str(chosen)}
        if action == "no_candidates":
            # причина и комментарий обязательны всегда: REST не объявляет комментарий
            # обязательным, а Jira отказывает без него (проба KON-30)
            reason = next((f for f in t.fields if _kind(f) == "reason"), None)
            rid = (fields or {}).get(reason.field_id if reason else F_REASON)
            if not rid:
                raise AssignError("bad_request", "Укажите причину отсутствия кандидата")
            if reason is None:
                values[F_REASON] = {"id": str(rid)}
            if not text:
                raise AssignError("bad_request", "Укажите комментарий")
        missing = self._missing(t, values, skip=(F_RATE,))
        if missing:  # до отправки, а не по отказу Jira (проба KON-30)
            raise AssignError("bad_request",
                              "Не заполнены обязательные поля: " + ", ".join(missing))
        text = text or f"Статус изменён из re_web_presale: {label}"  # комментарий перехода всегда
        self._jira_call(lambda: self._jira.do_transition(
            token, need_key, t.id, fields=values, comment=text), step=STEP_TRANSITION)
        log.info("статус потребности изменён: %s, действие %s", need_key, action)
        new_status, refreshed = self._refresh(token, need_key)
        return OpResult(need_key, new_status, refreshed, (STEP_TRANSITION,))

    # --- назначение (FR-008, FR-009, FR-010, FR-018) ---

    def _load_files(self, inp: AssignInput) -> list[tuple[int, str, bytes, str]]:
        if self._files is None:
            raise AssignError("not_enabled", "Хранилище CV не настроено (CV_DB_URL)")
        resume = (
            self._files.get_file(inp.resume_file_id) if inp.resume_file_id is not None
            else self._files.current_main(inp.employee_key)
        )
        if (resume is None or resume.employee_key != inp.employee_key or resume.is_archived
                or resume.kind not in ("main_cv", "extra_cv")):
            raise AssignError("file_not_found", "Резюме сотрудника не найдено")
        ids = [resume.id]
        for fid in inp.extra_file_ids:
            meta = self._files.get_file(fid)
            if (meta is None or meta.employee_key != inp.employee_key or meta.is_archived
                    or meta.kind not in ("extra_cv", "materials")):
                raise AssignError("file_not_found", f"Файл {fid} сотрудника не найден")
            if fid not in ids:
                ids.append(fid)
        out = []
        for fid in ids:
            got = self._files.get_content(fid)
            if got is None:
                raise AssignError("file_not_found", f"Содержимое файла {fid} не найдено")
            meta, content = got
            out.append((fid, meta.file_name, content, meta.mime_type or "application/octet-stream"))
        return out

    def assign(self, login: str, need_key: str, inp: AssignInput) -> OpResult:
        if self._employees is None:
            raise AssignError("not_enabled", "База сотрудников не настроена (CV_DB_URL)")
        token = self._token(login)
        # --- проверки без записи ---
        exists, emp_login = self._employees.get_profile(inp.employee_key)
        if not exists:
            raise AssignError("employee_not_found", "Сотрудник не найден")
        if not emp_login:
            raise AssignError(
                "employee_no_profile",
                "У сотрудника нет учётной записи Jira («Профиль сотрудника»): "
                "назначение невозможно")
        files = self._load_files(inp)
        need, status = self._read_need(token, need_key)  # FR-028
        rate = self._rate_to_send(inp.rate_rub, need, list(inp.completed))  # до записи (БП-2)
        transitions = self._jira_call(lambda: self._jira.get_transitions(token, need_key))
        t = self._find(transitions, STATUS_ASSIGNED)
        already = _norm(status) == _norm(STATUS_ASSIGNED)
        if t is None and not already:
            raise AssignError(
                "assign_unavailable",
                f"Jira не предлагает переход в «{STATUS_ASSIGNED}» из статуса «{status}»")

        known: dict[str, Any] = {self._profile_field: {"name": emp_login},
                                 "assignee": {"name": login}}
        in_transition: dict[str, Any] = {}
        if t is not None:
            by_id = {f.field_id: f for f in t.fields}
            in_transition = {k: v for k, v in known.items() if k in by_id}
            for f in t.fields:  # остальные обязательные поля перехода - из потребности
                if f.field_id not in in_transition:
                    pre = _prefill(f, need)
                    if pre:
                        in_transition[f.field_id] = {"id": pre}
            missing = self._missing(t, in_transition)
            if missing:
                raise AssignError(
                    "bad_request", "Jira требует для перехода «Кандидат назначен» поля: "
                    + ", ".join(missing))
        direct = {k: v for k, v in known.items() if k not in in_transition}

        # --- запись под учёткой пользователя ---
        done = list(inp.completed)
        warnings: list[str] = []

        def step(name: str, fn) -> None:
            if name in done:
                return
            self._jira_call(fn, step=name, completed=done)
            done.append(name)

        step(STEP_FIELDS,
             lambda: self._jira.set_fields(token, need_key, direct) if direct else None)
        for fid, fname, content, mime in files:
            step(STEP_ATTACH + str(fid),
                 lambda fname=fname, content=content, mime=mime:
                 self._jira.attach_file(token, need_key, fname, content, mime))
        text = (inp.comment or "").strip()
        if t is not None:
            text = text or "Кандидат назначен через re_web_presale"  # комментарий перехода всегда
            step(STEP_TRANSITION, lambda: self._transition_assigned(
                token, need_key, t.id, in_transition, text, warnings))
        elif text and STEP_TRANSITION not in done:  # комментарий уже ушёл с переходом
            step(STEP_COMMENT, lambda: self._jira.add_comment(token, need_key, text))
        rate_pending = False
        if rate is not None:
            # назначение уже выполнено и не откатывается: сбой ставки - частичный успех (FR-029)
            try:
                self._jira_call(lambda: self._jira.set_rate(
                    token, need_key, amount=rate, vat=RATE_VAT_YES, currency=RATE_CURRENCY_RUB,
                    unit=RATE_UNIT_HOUR, proposer=RATE_PROPOSER_PRODUCTION, username=login),
                    step=STEP_RATE, completed=done)
                done.append(STEP_RATE)
            except AssignError as err:
                rate_pending = True
                warnings.append(
                    f"Кандидат назначен, но ставка не записана в Jira: {err.message}. "
                    "Введённое сохранено, повторите запись ставки")
                log.warning("ставка не записана: %s, код %s", need_key, err.code)
        log.info("кандидат назначен: %s, шагов %d", need_key, len(done))
        new_status, refreshed = self._refresh(token, need_key)
        return OpResult(need_key, new_status, refreshed, tuple(done), tuple(warnings),
                        rate_pending)

    # --- переход 21 и обход правила «Нужно предоставить CV» (CR-006) ---

    def _transition_assigned(self, token: str, key: str, transition_id: str,
                             fields: dict[str, Any], comment: str, warnings: list[str]) -> None:
        """Переход «Кандидат назначен» с обходом экранного валидатора «Вложение».

        ОТКЛОНЕНИЕ от FR-015 (приложение не меняет правило клиента) осознанное, согласовано в
        CR-006; основание - проба KON-42 (architecture/probes/KON-42_report.md, D1): при
        ``customfield_11506`` = «Да» переход 21 по REST отклоняется (400, ``attachment``), хотя CV
        приложено. Порядок: обычная попытка -> (только при этом отказе и 11506 = «Да»)
        11506 = «Нет» -> переход -> 11506 = «Да» ВСЕГДА (finally). +2 записи к сессии (NFR-009).
        """
        try:
            self._jira.do_transition(token, key, transition_id, fields=fields, comment=comment)
            return
        except JiraIntegrationError as first:
            if not _is_cv_validator_refusal(first) or not self._need_cv_rule_on(token, key):
                raise
            refusal = first
        try:
            self._jira.set_fields(token, key, {F_NEED_CV: {"value": "Нет"}})
        except JiraIntegrationError as exc:
            raise _reworded(
                exc, f"Обход правила «Нужно предоставить CV» не удался: {exc.message}. "
                "Назначьте кандидата вручную в Jira") from None
        restore_failed = False
        try:
            try:
                self._jira.do_transition(token, key, transition_id, fields=fields,
                                         comment=comment)
            finally:
                try:
                    self._jira.set_fields(token, key, {F_NEED_CV: {"value": "Да"}})
                except Exception as exc:  # noqa: BLE001 - возврат не удался: сказать оператору
                    restore_failed = True
                    log.error("%s не возвращено в «Да»: %s", F_NEED_CV, type(exc).__name__)
        except JiraIntegrationError as exc:
            text = f"{exc.message} (первый отказ Jira: {refusal.message})"
            if exc.code == CODE_JIRA_ERROR:
                text += ". Обход не помог, назначьте кандидата вручную в Jira"
            if restore_failed:
                text += ". " + CV_RESTORE_WARNING
            raise _reworded(exc, text) from None
        if restore_failed:
            warnings.append(CV_RESTORE_WARNING)

    def _need_cv_rule_on(self, token: str, key: str) -> bool:
        """Правило клиента включено, если 11506 = «Да» (читаем только после отказа валидатора)."""
        issue = self._jira.get_issue(token, key, fields=[F_NEED_CV])
        value = ((issue or {}).get("fields") or {}).get(F_NEED_CV)
        name = value.get("value") if isinstance(value, dict) else value
        return _norm(str(name or "")) == "да"


def _is_cv_validator_refusal(exc: JiraIntegrationError) -> bool:
    msg = (exc.message or "").lower()
    return (exc.code == CODE_JIRA_ERROR and exc.status == 400
            and ("attachment" in msg or "вложени" in msg))


def _reworded(exc: JiraIntegrationError, message: str) -> JiraIntegrationError:
    return JiraIntegrationError(message, code=exc.code, reason=exc.reason, status=exc.status,
                                retryable=exc.retryable)
