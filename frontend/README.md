# frontend

React 18 + TypeScript + Vite + react-router. Каркас (Dev-20): шапка с логотипом, страница входа, четыре раздела-заглушки.
`npm ci; npm run lint; npm test; npm run build`. Dev-сервер проксирует `/api` на `localhost:8000`.

## Сопоставление, поиск по смыслу, настройки LLM (KON-41)
- «Потребности»: столбец «Кандидаты» (`components/matching/NeedCandidates.tsx`): «Сопоставить» (POST), опрос GET до
  `done|partial|failed`, % с цветовой шкалой, обоснование по клику, «Показать ещё» (offset по 10), «Оценить следующих»,
  «Пересчитать» (`force`), метка устаревшего результата. «Назначить» - заглушка до этапа 2.
- Внешняя отправка (FR-023): `RunPreviewDialog` показывает обезличенный текст всего прогона
  (`GET /matching/runs/{run_id}`), один `confirm` и отмена; закрытие окна ничего не отправляет. Таймаут - сообщение и
  «Повторить» (`POST /llm/tasks/{id}/retry`).
- «Резюме»: «Поиск по смыслу (LLM)» (`SemanticSearch`), фильтр «Технология» (`TechnologyPicker`, `match=all|any`),
  технологии в столбце «Описание». «Профиль» сотрудника берёт разделы CV из `GET /resume/employees/{key}/profile`.
- «Настройки» → «LLM» (только админ, `components/settings/LlmTab.tsx`): провайдеры (ключ вводится, обратно не
  показывается, внешний - только HTTPS), словарь обезличивания, размер глубокой оценки N и пакета скрининга.
- Период опроса - `POLL_MS` в `src/lib/matching.ts`.
