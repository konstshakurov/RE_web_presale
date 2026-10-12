import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { MatchItem, MatchState } from "../../api/types";
import { PAGE_SIZE, isAwaiting, isRunning } from "../../lib/matching";
import { formatDateTime } from "../../lib/needs";
import { usePolling } from "../../lib/usePolling";
import { useProviders } from "../../lib/useProviders";
import CvViewer from "../CvViewer";
import CandidateList from "./CandidateList";
import type { CandidateRow } from "./CandidateList";
import ProviderSelect from "./ProviderSelect";
import MatchingInfo from "./MatchingInfo";
import RunPreviewDialog from "./RunPreviewDialog";
import RunStatusBar from "./RunStatusBar";

interface Props {
  needKey: string;
  autoStart: boolean; // открыт кнопкой «Сопоставить»: сразу ставим расчёт (кэш вернётся без LLM)
}

// Скрининг идёт, только когда допустимых кандидатов больше размера глубокой оценки
const SCREEN_HINT =
  "Предварительная оценка по навыкам — быстрый отбор при большом числе кандидатов; при малом числе кандидатов не выполняется";

export function toRow(i: MatchItem): CandidateRow {
  return {
    key: i.employee_key,
    name: i.full_name,
    status: i.status,
    percent: i.percent,
    rationale: i.rationale,
    gaps: i.gaps ?? null,
    note: i.note,
    noteAuthor: i.note_author,
    noteUpdatedAt: i.note_updated_at,
    stale: i.stale,
    tags: (i.skills ?? []).slice(0, 6),
  };
}

// Блок «Кандидаты» потребности (FR-036, FR-005): расчёт в фоне, опрос, подтверждение внешней отправки (FR-023).
export default function NeedCandidates({ needKey, autoStart }: Props) {
  const [data, setData] = useState<MatchState | null>(null);
  const [items, setItems] = useState<MatchItem[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewRun, setPreviewRun] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [viewKey, setViewKey] = useState<string | null>(null);
  const { providers, providerId, setProviderId } = useProviders();
  const itemsLen = useRef(0);
  itemsLen.current = items.length;
  const started = useRef(false);

  const apply = useCallback((s: MatchState) => {
    setData(s);
    setItems(s.items ?? []);
    setError("");
  }, []);

  const fail = useCallback((e: unknown, fallback: string) => {
    setError(e instanceof ApiError ? e.message : fallback);
  }, []);

  const reload = useCallback(async () => {
    try {
      apply(await api.matchState(needKey, Math.min(100, Math.max(PAGE_SIZE, itemsLen.current)), 0));
    } catch (e) {
      fail(e, "Не удалось загрузить кандидатов");
    }
  }, [needKey, apply, fail]);

  async function run(action: () => Promise<MatchState>, failText: string) {
    setBusy(true);
    try {
      apply(await action());
    } catch (e) {
      fail(e, failText);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (autoStart) void run(() => api.startMatch(needKey, { provider_id: providerId }), "Не удалось запустить сопоставление");
    else void reload();
  }, []);

  usePolling(isRunning(data?.status), reload);

  // Прогон ждёт подтверждения - сразу показываем предпросмотр (один раз на прогон)
  useEffect(() => {
    if (data && isAwaiting(data) && data.run_id && data.run_id !== dismissed) setPreviewRun(data.run_id);
  }, [data, dismissed]);

  async function more() {
    setBusy(true);
    try {
      const s = await api.matchState(needKey, PAGE_SIZE, items.length);
      setData(s);
      setItems((prev) => [...prev, ...(s.items ?? [])]);
    } catch (e) {
      fail(e, "Не удалось загрузить кандидатов");
    } finally {
      setBusy(false);
    }
  }

  const running = isRunning(data?.status) || isAwaiting(data);
  const none = !data || data.status === "none";
  const total = data?.total ?? 0;
  const remaining = data?.remaining ?? 0;

  return (
    <div className="candidates-block">
      <div className="candidates-block__head">
        <strong>Кандидаты на {needKey}</strong>
        <MatchingInfo />
        <ProviderSelect providers={providers} value={providerId} onChange={setProviderId} disabled={busy || running} />
        {none && !running && (
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void run(() => api.startMatch(needKey, { provider_id: providerId }), "Не удалось запустить сопоставление")}
          >
            Сопоставить
          </button>
        )}
        {!none && (
          <button
            type="button"
            disabled={busy || running}
            title="Заново оценить всех кандидатов"
            onClick={() =>
              void run(() => api.startMatch(needKey, { provider_id: providerId, force: true }), "Не удалось запустить расчёт")
            }
          >
            Пересчитать
          </button>
        )}
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Загрузка…</p>}
      {data && (
        <RunStatusBar
          state={data}
          onOpenPreview={() => data.run_id && setPreviewRun(data.run_id)}
          onRetried={() => void reload()}
        />
      )}
      {data && data.stale && !isRunning(data.status) && (
        <p className="badge badge--stale">
          Результат устарел (потребность, заметка или модель изменились) — нажмите «Пересчитать»
        </p>
      )}
      {data && data.status === "none" && !running && !error && <p className="muted">Сопоставление ещё не выполнялось.</p>}
      {data && data.calculated_at && (
        <p className="muted" title={SCREEN_HINT}>
          Рассчитано: {formatDateTime(data.calculated_at)}
          {data.model ? `, модель ${data.model}` : ""}. Оценено кандидатов: {data.evaluated} из {data.candidates_total}
          {data.screen_total && (data.screened ?? 0) > 0
            ? `; предварительная оценка по навыкам: ${data.screened} из ${data.screen_total}`
            : ""}
          .
        </p>
      )}

      {data && items.length > 0 && <CandidateList rows={items.map(toRow)} onResume={setViewKey} />}
      {data && items.length === 0 && !running && data.status !== "none" && !error && (
        <p className="empty">Подходящих кандидатов нет. Можно перейти к смене статуса потребности на «Кандидатов нет».</p>
      )}

      {data && (items.length > 0 || data.can_evaluate_next) && (
        <div className="pager">
          {items.length > 0 && (
            <span>
              Показано {items.length} из {total}
            </span>
          )}
          <button type="button" disabled={busy || items.length >= total} onClick={() => void more()}>
            Показать ещё
          </button>
          {data.can_evaluate_next && (
            <button
              type="button"
              disabled={busy || running}
              onClick={() => void run(() => api.matchNext(needKey, providerId), "Не удалось запустить оценку")}
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
