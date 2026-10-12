import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { SearchItem, SearchParams, SearchState } from "../../api/types";
import { PAGE_SIZE, SOURCE_LABELS, isAwaiting, isRunning, searchPercent } from "../../lib/matching";
import { formatDateTime } from "../../lib/needs";
import { usePolling } from "../../lib/usePolling";
import { useProviders } from "../../lib/useProviders";
import CvViewer from "../CvViewer";
import CandidateList from "./CandidateList";
import type { CandidateRow } from "./CandidateList";
import ProviderSelect from "./ProviderSelect";
import RunPreviewDialog from "./RunPreviewDialog";
import RunStatusBar from "./RunStatusBar";

export interface SearchFilters {
  status?: string | null;
  technology: string[];
  match: "all" | "any";
  q?: string | null;
}

export const QUERY_MIN = 3;
export const QUERY_MAX = 500;

export function toSearchRow(i: SearchItem): CandidateRow {
  const p = searchPercent(i);
  return {
    key: i.employee_key,
    name: i.full_name,
    status: i.status,
    percent: p.value,
    preliminary: p.preliminary,
    sourceLabel: i.source === "fallback" ? SOURCE_LABELS.fallback : undefined,
    rationale: i.rationale,
    note: i.note,
    noteAuthor: i.note_author,
    noteUpdatedAt: i.note_updated_at,
    stale: i.stale,
    tags: [...(i.matched_skills ?? []), ...(i.matched_terms ?? [])].slice(0, 8),
  };
}

// «Поиск по смыслу (LLM)» (FR-032): запрос на естественном языке, фильтры раздела передаются как область поиска.
// Без LLM список строится по технологиям и тексту (fallback) и сопровождается сообщением notice.
export default function SemanticSearch({ filters }: { filters: SearchFilters }) {
  const [draft, setDraft] = useState("");
  const [params, setParams] = useState<SearchParams | null>(null);
  const [data, setData] = useState<SearchState | null>(null);
  const [items, setItems] = useState<SearchItem[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewRun, setPreviewRun] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [viewKey, setViewKey] = useState<string | null>(null);
  const { providers, providerId, setProviderId } = useProviders();
  const itemsLen = useRef(0);
  itemsLen.current = items.length;

  const apply = useCallback((s: SearchState) => {
    setData(s);
    setItems(s.items ?? []);
    setError("");
  }, []);

  // Прогон ждёт подтверждения - показываем предпросмотр (один раз на прогон)
  useEffect(() => {
    if (data && isAwaiting(data) && data.run_id && data.run_id !== dismissed) setPreviewRun(data.run_id);
  }, [data, dismissed]);

  const fail = (e: unknown, fallback: string) => setError(e instanceof ApiError ? e.message : fallback);

  async function run(p: SearchParams, action: (p: SearchParams) => Promise<SearchState>, failText: string) {
    setBusy(true);
    try {
      apply(await action(p));
    } catch (e) {
      fail(e, failText);
    } finally {
      setBusy(false);
    }
  }

  const reload = useCallback(async () => {
    if (!params) return;
    try {
      apply(await api.searchState({ ...params, limit: Math.min(100, Math.max(PAGE_SIZE, itemsLen.current)) }, 0));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось обновить результат поиска");
    }
  }, [params, apply]);

  usePolling(isRunning(data?.status), reload);

  const tooShort = draft.trim().length < QUERY_MIN;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (tooShort) return;
    const p: SearchParams = {
      query: draft.trim().slice(0, QUERY_MAX),
      provider_id: providerId,
      status: filters.status || null,
      technology: filters.technology,
      match: filters.match,
      q: filters.q || null,
      limit: PAGE_SIZE,
    };
    setParams(p);
    setDismissed(null);
    void run(p, (x) => api.startSearch(x), "Не удалось выполнить поиск");
  }

  async function more() {
    if (!params) return;
    setBusy(true);
    try {
      const s = await api.searchState({ ...params, limit: PAGE_SIZE }, items.length);
      setData(s);
      setItems((prev) => [...prev, ...(s.items ?? [])]);
    } catch (e) {
      fail(e, "Не удалось загрузить результаты");
    } finally {
      setBusy(false);
    }
  }

  const running = isRunning(data?.status) || isAwaiting(data);
  const remaining = data?.remaining ?? 0;
  const total = data?.total ?? 0;

  return (
    <div className="semantic">
      <form className="semantic__form" onSubmit={onSubmit}>
        <label>
          Поиск по смыслу (LLM)
          <textarea
            rows={2}
            value={draft}
            maxLength={QUERY_MAX}
            placeholder="Например: аналитик с опытом интеграций 1С и REST, работал в банковской сфере"
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        <ProviderSelect providers={providers} value={providerId} onChange={setProviderId} disabled={busy || running} />
        <button type="submit" className="primary" disabled={busy || running || tooShort}>
          Найти по смыслу
        </button>
        {params && data && !running && (
          <button
            type="button"
            disabled={busy}
            title="Заново оценить, не используя сохранённые оценки"
            onClick={() => void run({ ...params, provider_id: providerId, force: true }, (x) => api.startSearch(x), "Не удалось выполнить поиск")}
          >
            Пересчитать
          </button>
        )}
      </form>
      <p className="muted">
        Запрос {QUERY_MIN}–{QUERY_MAX} знаков. Область поиска ограничивают фильтры «Поиск», «Статус сотрудника» и
        «Технология». Во внешнюю LLM уходит только обезличенный текст и только после вашего подтверждения.
      </p>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <RunStatusBar state={data} onOpenPreview={() => data.run_id && setPreviewRun(data.run_id)} onRetried={() => void reload()} />
      )}
      {data?.notice && (
        <p className="badge badge--stale" role="status">
          {data.notice}
        </p>
      )}
      {data && data.stale && !running && <p className="badge badge--stale">Результат устарел — нажмите «Пересчитать»</p>}
      {data && data.calculated_at && (
        <p className="muted">
          Рассчитано: {formatDateTime(data.calculated_at)}
          {data.model ? `, модель ${data.model}` : ""}. Найдено в области: {data.candidates_total}, оценено LLM:{" "}
          {data.evaluated}.
        </p>
      )}

      {data && items.length > 0 && <CandidateList rows={items.map(toSearchRow)} onResume={setViewKey} assign={false} />}
      {data && items.length === 0 && !running && !error && <p className="empty">По запросу никого не найдено.</p>}

      {data && items.length > 0 && (
        <div className="pager">
          <span>
            Показано {items.length} из {total}
          </span>
          <button type="button" disabled={busy || items.length >= total} onClick={() => void more()}>
            Показать ещё
          </button>
          {data.can_evaluate_next && params && (
            <button
              type="button"
              disabled={busy || running}
              onClick={() => void run({ ...params, provider_id: providerId }, (x) => api.searchNext(x), "Не удалось запустить оценку")}
            >
              Оценить следующих
              {data.deep_size ? ` (${Math.min(data.deep_size, remaining || data.deep_size)}, осталось ${remaining})` : ""}
            </button>
          )}
        </div>
      )}

      {previewRun && (
        <RunPreviewDialog
          runId={previewRun}
          onClose={() => {
            setDismissed(previewRun);
            setPreviewRun(null);
          }}
          onConfirmed={() => {
            setPreviewRun(null);
            void reload();
          }}
          onCancelled={() => {
            setDismissed(previewRun);
            setPreviewRun(null);
            void reload();
          }}
        />
      )}
      {viewKey && <CvViewer employeeKey={viewKey} onClose={() => setViewKey(null)} />}
    </div>
  );
}
