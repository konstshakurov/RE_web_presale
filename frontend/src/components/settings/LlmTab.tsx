import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { Dictionary, MatchSettings, ProviderOut } from "../../api/types";
import { LLM_TIMEOUT_SEC, TECH_TERM_MAX_LEN, mergeTechTerms, parseTerms, providerUrlError } from "../../lib/matching";
import Modal from "../Modal";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Не удалось выполнить действие";
}

// Вкладка «LLM» (FR-030, FR-023, FR-005; только админ): провайдеры, словарь обезличивания, технологии, настройки сопоставления.
// Ключ API вводится и обратно не показывается: backend отдаёт только has_key.
export default function LlmTab() {
  return (
    <div className="llm-settings">
      <ProvidersSection />
      <DictionarySection />
      <TechTermsSection />
      <MatchSettingsSection />
    </div>
  );
}

function ProvidersSection() {
  const [items, setItems] = useState<ProviderOut[] | null>(null);
  const [error, setError] = useState("");
  const [edit, setEdit] = useState<ProviderOut | "new" | null>(null);
  const [del, setDel] = useState<ProviderOut | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.adminProviders();
      if (!Array.isArray(list)) throw new Error("bad response");
      setItems(list);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить провайдеров");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="group-card">
      <div className="group-card__head">
        <h3>Провайдеры LLM</h3>
        <span className="group-card__spacer" />
        <button type="button" className="primary" onClick={() => setEdit("new")}>
          Добавить провайдера
        </button>
      </div>
      <p className="muted">
        По умолчанию используется корпоративная Qwen (без обезличивания). Во внешнюю модель уходит только обезличенный
        текст и только после подтверждения пользователя. Ключ API хранится зашифрованным и не показывается.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {items === null && !error && <p className="muted">Загрузка…</p>}
      {items && (
        <table className="needs">
          <thead>
            <tr>
              <th>Название</th>
              <th>Тип</th>
              <th>URL</th>
              <th>Модель</th>
              <th>Таймаут, с</th>
              <th>Ключ</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>
                  {p.name} {p.is_default && <span className="status">по умолчанию</span>}
                </td>
                <td>{p.is_external ? "Внешний" : "Внутренний"}</td>
                <td>{p.url}</td>
                <td>{p.model || <span className="muted">—</span>}</td>
                <td>{p.timeout_sec}</td>
                <td>{p.has_key ? "задан" : "нет"}</td>
                <td>
                  <button type="button" className="link" onClick={() => setEdit(p)}>
                    Изменить
                  </button>
                  <button type="button" className="link link--danger" onClick={() => setDel(p)}>
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {edit && (
        <ProviderDialog
          provider={edit === "new" ? null : edit}
          onClose={() => setEdit(null)}
          onDone={() => {
            setEdit(null);
            void load();
          }}
        />
      )}
      {del && (
        <DeleteDialog
          provider={del}
          onClose={() => setDel(null)}
          onDone={() => {
            setDel(null);
            void load();
          }}
        />
      )}
    </section>
  );
}

function ProviderDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: ProviderOut | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(provider?.name ?? "");
  const [url, setUrl] = useState(provider?.url ?? "");
  const [external, setExternal] = useState(provider?.is_external ?? true);
  const [model, setModel] = useState(provider?.model ?? "");
  const [timeout, setTimeoutSec] = useState(String(provider?.timeout_sec ?? LLM_TIMEOUT_SEC));
  const [isDefault, setDefault] = useState(provider?.is_default ?? false);
  const [apiKey, setApiKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const urlError = url ? providerUrlError(url, external) : "";
  const timeoutNum = Number(timeout);
  const timeoutOk = Number.isInteger(timeoutNum) && timeoutNum >= 1 && timeoutNum <= 3600;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const bad = providerUrlError(url, external);
    if (bad) return setError(bad);
    if (!timeoutOk) return setError("Таймаут: целое число секунд от 1 до 3600");
    setBusy(true);
    setError("");
    try {
      if (provider) {
        await api.patchProvider(provider.id, {
          name: name.trim(),
          url: url.trim(),
          is_external: external,
          is_default: isDefault,
          model: model.trim(),
          timeout_sec: timeoutNum,
          ...(apiKey ? { api_key: apiKey } : {}),
          ...(removeKey && !apiKey ? { remove_key: true } : {}),
        });
      } else {
        await api.createProvider({
          name: name.trim(),
          url: url.trim(),
          is_external: external,
          is_default: isDefault,
          model: model.trim(),
          timeout_sec: timeoutNum,
          api_key: apiKey || null,
        });
      }
      setApiKey(""); // ключ не остаётся в состоянии формы
      onDone();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }

  return (
    <Modal title={provider ? "Изменить провайдера" : "Добавить провайдера"}>
      <form className="modal__form" onSubmit={(e) => void onSubmit(e)}>
        <label>
          Название
          <input value={name} maxLength={128} required onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          URL (базовый адрес OpenAI-совместимого API)
          <input
            type="url"
            value={url}
            maxLength={512}
            required
            placeholder="https://"
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        {urlError && <p className="error">{urlError}</p>}
        <label className="check">
          <input type="checkbox" checked={external} onChange={(e) => setExternal(e.target.checked)} />
          Внешний провайдер (текст обезличивается, нужно подтверждение; только HTTPS)
        </label>
        <label>
          Модель
          <input value={model} maxLength={128} onChange={(e) => setModel(e.target.value)} />
        </label>
        <label>
          Таймаут запроса, с
          <input
            type="number"
            min={1}
            max={3600}
            value={timeout}
            onChange={(e) => setTimeoutSec(e.target.value)}
          />
        </label>
        <label>
          {provider ? "Новый ключ API (пусто — не менять)" : "Ключ API"}
          <input
            type="password"
            value={apiKey}
            maxLength={4096}
            autoComplete="new-password"
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>
        {provider?.has_key && (
          <label className="check">
            <input type="checkbox" checked={removeKey} onChange={(e) => setRemoveKey(e.target.checked)} />
            Удалить сохранённый ключ (сейчас ключ задан)
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={isDefault} onChange={(e) => setDefault(e.target.checked)} />
          Провайдер по умолчанию
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal__actions">
          <button type="submit" className="primary" disabled={busy}>
            Сохранить
          </button>
          <button type="button" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DeleteDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: ProviderOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError("");
    try {
      await api.deleteProvider(provider.id);
      onDone();
    } catch (e) {
      setError(errText(e)); // например, провайдер по умолчанию или используется задачами - текст сервера как есть
      setBusy(false);
    }
  }

  return (
    <Modal title="Удалить провайдера">
      <p>Удалить провайдера «{provider.name}»? Его ключ будет удалён.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal__actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void run()}>
          Удалить
        </button>
        <button type="button" onClick={onClose}>
          Отмена
        </button>
      </div>
    </Modal>
  );
}

const DICT_FIELDS: { key: keyof Dictionary; title: string }[] = [
  { key: "persons", title: "Персоны (ФИО)" },
  { key: "companies", title: "Компании и клиенты" },
  { key: "projects", title: "Проекты и продукты" },
  { key: "locations", title: "Локации" },
];

function DictionarySection() {
  const [text, setText] = useState<Record<keyof Dictionary, string> | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .dictionary()
      .then((d) => {
        if (!alive) return;
        setText({
          persons: (d.persons ?? []).join("\n"),
          companies: (d.companies ?? []).join("\n"),
          projects: (d.projects ?? []).join("\n"),
          locations: (d.locations ?? []).join("\n"),
        });
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить словарь"));
    return () => {
      alive = false;
    };
  }, []);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (!text) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const d = await api.saveDictionary({
        persons: parseTerms(text.persons),
        companies: parseTerms(text.companies),
        projects: parseTerms(text.projects),
        locations: parseTerms(text.locations),
      });
      setText({
        persons: d.persons.join("\n"),
        companies: d.companies.join("\n"),
        projects: d.projects.join("\n"),
        locations: d.locations.join("\n"),
      });
      setSaved(true);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="group-card">
      <h3>Словарь обезличивания</h3>
      <p className="muted">
        Дополнительные слова и названия, которые заменяются перед отправкой во внешнюю LLM (помимо данных из Jira и CV).
        По одному в строке.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!text && !error && <p className="muted">Загрузка…</p>}
      {text && (
        <form className="dictionary" onSubmit={(e) => void onSave(e)}>
          {DICT_FIELDS.map((f) => (
            <label key={f.key}>
              {f.title}
              <textarea
                rows={5}
                value={text[f.key]}
                onChange={(e) => {
                  setSaved(false);
                  setText({ ...text, [f.key]: e.target.value });
                }}
              />
            </label>
          ))}
          <div className="modal__actions">
            <button type="submit" className="primary" disabled={busy}>
              Сохранить словарь
            </button>
            {saved && <span role="status">Сохранено</span>}
          </div>
        </form>
      )}
    </section>
  );
}

// Технологии, которые не обезличиваются (KON-73): расширение встроенного белого списка.
function TechTermsSection() {
  const [terms, setTerms] = useState<string[] | null>(null);
  const [overlaps, setOverlaps] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .techTerms()
      .then((d) => {
        if (!alive) return;
        setTerms(d.terms ?? []);
        setOverlaps(d.overlaps ?? []);
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить список технологий"));
    return () => {
      alive = false;
    };
  }, []);

  function add() {
    if (!terms || !input.trim()) return;
    const r = mergeTechTerms(terms, input);
    if (r.tooLong.length) return setError(`Название технологии не длиннее ${TECH_TERM_MAX_LEN} знаков: ${r.tooLong[0]}`);
    if (r.overflow) return setError("В списке не более 500 технологий");
    setError("");
    setSaved(false);
    setTerms(r.terms);
    setInput("");
  }

  async function onSave() {
    if (!terms) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const d = await api.saveTechTerms(terms);
      setTerms(d.terms);
      setOverlaps(d.overlaps ?? []);
      setSaved(true);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="group-card">
      <h3>Технологии, которые не обезличиваются</h3>
      <p className="muted">
        Названия технологий и продуктов (Jira, Kafka, внутренний фреймворк), которые остаются в тексте для внешней LLM
        и не заменяются на [КОМПАНИЯ_n]. Список дополняет встроенный. Названия компаний и клиентов сюда добавлять нельзя:
        их нужно обезличивать, для этого служит словарь выше (у словаря приоритет). Изменения действуют сразу.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!terms && !error && <p className="muted">Загрузка…</p>}
      {terms && (
        <div className="tech-terms">
          {overlaps.length > 0 && (
            <p className="error" role="status">
              Есть в словаре обезличивания, поэтому продолжают маскироваться: {overlaps.join(", ")}
            </p>
          )}
          {terms.length > 0 ? (
            <ul className="chips" aria-label="Технологии без обезличивания">
              {terms.map((t) => (
                <li key={t} className="chip">
                  {t}
                  <button
                    type="button"
                    aria-label={`Убрать ${t}`}
                    onClick={() => {
                      setSaved(false);
                      setTerms(terms.filter((x) => x !== t));
                    }}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Дополнительных технологий нет, действует встроенный список.</p>
          )}
          <label>
            Добавить технологии (через запятую или с новой строки)
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  add();
                }
              }}
            />
          </label>
          <div className="modal__actions">
            <button type="button" onClick={add} disabled={!input.trim()}>
              Добавить
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void onSave()}>
              Сохранить список технологий
            </button>
            {saved && <span role="status">Сохранено</span>}
          </div>
        </div>
      )}
    </section>
  );
}

function MatchSettingsSection() {
  const [s, setS] = useState<MatchSettings | null>(null);
  const [deep, setDeep] = useState("");
  const [batch, setBatch] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const fill = (v: MatchSettings) => {
    setS(v);
    setDeep(String(v.deep_size));
    setBatch(String(v.screen_batch));
  };

  useEffect(() => {
    let alive = true;
    api
      .matchSettings()
      .then((v) => alive && fill(v))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить настройки"));
    return () => {
      alive = false;
    };
  }, []);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (!s) return;
    const d = Number(deep);
    const b = Number(batch);
    if (!Number.isInteger(d) || d < s.deep_size_min || d > s.deep_size_max)
      return setError(`Размер глубокой оценки: от ${s.deep_size_min} до ${s.deep_size_max}`);
    if (!Number.isInteger(b) || b < s.screen_batch_min || b > s.screen_batch_max)
      return setError(`Размер пакета скрининга: от ${s.screen_batch_min} до ${s.screen_batch_max}`);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      fill(await api.saveMatchSettings({ deep_size: d, screen_batch: b }));
      setSaved(true);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="group-card">
      <h3>Сопоставление кандидатов</h3>
      <p className="muted">Действует на новые расчёты. Скрининг оценивает всех кандидатов по навыкам, глубокая оценка — первых N по тексту CV.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!s && !error && <p className="muted">Загрузка…</p>}
      {s && (
        <form className="match-settings" noValidate onSubmit={(e) => void onSave(e)}>
          <label>
            Размер глубокой оценки N ({s.deep_size_min}–{s.deep_size_max})
            <input
              type="number"
              min={s.deep_size_min}
              max={s.deep_size_max}
              value={deep}
              onChange={(e) => {
                setSaved(false);
                setDeep(e.target.value);
              }}
            />
          </label>
          <label>
            Размер пакета скрининга ({s.screen_batch_min}–{s.screen_batch_max})
            <input
              type="number"
              min={s.screen_batch_min}
              max={s.screen_batch_max}
              value={batch}
              onChange={(e) => {
                setSaved(false);
                setBatch(e.target.value);
              }}
            />
          </label>
          <div className="modal__actions">
            <button type="submit" className="primary" disabled={busy}>
              Сохранить настройки
            </button>
            {saved && <span role="status">Сохранено</span>}
          </div>
        </form>
      )}
    </section>
  );
}
