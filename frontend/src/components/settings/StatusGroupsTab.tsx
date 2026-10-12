import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { GroupOut, StatusGroupsData } from "../../api/types";
import Modal from "../Modal";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Не удалось выполнить действие";
}

// Состав групп статусов потребностей (FR-035): правка админом без SQL. «Прочие» и «Все статусы»
// виртуальные - не редактируются, а статусы вне групп показаны отдельным блоком.
export default function StatusGroupsTab() {
  const [data, setData] = useState<StatusGroupsData | null>(null);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [toDelete, setToDelete] = useState<GroupOut | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.statusGroups();
      if (!Array.isArray(d?.groups)) throw new Error("bad response");
      setData(d);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить группы статусов");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Любое изменение: показать отказ сервера как есть, затем перечитать состав и счётчики.
  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setError("");
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError(errText(e));
      return false;
    }
  }

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (await act(() => api.createGroup(newName.trim()))) setNewName("");
  }

  function move(index: number, delta: number) {
    if (!data) return;
    const ids = data.groups.map((g) => g.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    void act(() => api.orderGroups(ids));
  }

  return (
    <div className="groups">
      <p className="muted">
        Статус может входить в несколько групп. Группы «Прочие» (статусы вне групп) и «Все статусы» виртуальные и не
        редактируются.
      </p>
      <form className="toolbar" onSubmit={(e) => void onCreate(e)}>
        <label>
          Новая группа
          <input
            value={newName}
            maxLength={128}
            required
            placeholder="Название группы"
            onChange={(e) => setNewName(e.target.value)}
          />
        </label>
        <button type="submit" className="primary">
          Создать группу
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Загрузка…</p>}
      {data &&
        data.groups.map((g, i) => (
          <GroupCard
            key={g.id}
            group={g}
            choices={data.statuses.map((s) => s.status).filter((s) => !g.statuses.includes(s))}
            first={i === 0}
            last={i === data.groups.length - 1}
            onMove={(d) => move(i, d)}
            onRename={(name) => act(() => api.renameGroup(g.id, name))}
            onDelete={() => setToDelete(g)}
            onAdd={(s) => act(() => api.addGroupStatus(g.id, s))}
            onRemove={(s) => void act(() => api.removeGroupStatus(g.id, s))}
          />
        ))}
      {data && (
        <section className="group-card" aria-label="Прочие">
          <h3>
            Прочие <span className="filter__count">{data.other_count}</span>
          </h3>
          {data.other.length === 0 ? (
            <p className="muted">Все известные статусы входят в группы.</p>
          ) : (
            <ul className="chips">
              {data.other.map((s) => (
                <li key={s.status} className="chip">
                  {s.status} <span className="filter__count">{s.count}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {toDelete && (
        <Modal title="Удалить группу">
          <p>Удалить группу «{toDelete.name}»? Потребности останутся, изменится только фильтр.</p>
          <div className="modal__actions">
            <button
              type="button"
              className="primary"
              onClick={() => {
                const id = toDelete.id;
                setToDelete(null);
                void act(() => api.deleteGroup(id));
              }}
            >
              Подтвердить
            </button>
            <button type="button" onClick={() => setToDelete(null)}>
              Отмена
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

interface CardProps {
  group: GroupOut;
  choices: string[];
  first: boolean;
  last: boolean;
  onMove: (delta: number) => void;
  onRename: (name: string) => Promise<boolean>;
  onDelete: () => void;
  onAdd: (status: string) => Promise<boolean>;
  onRemove: (status: string) => void;
}

function GroupCard({ group, choices, first, last, onMove, onRename, onDelete, onAdd, onRemove }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(group.name);
  const [pick, setPick] = useState("");

  async function submitRename(e: FormEvent) {
    e.preventDefault();
    if (await onRename(name.trim())) setEditing(false);
  }

  return (
    <section className="group-card" aria-label={group.name}>
      <header className="group-card__head">
        {editing ? (
          <form onSubmit={(e) => void submitRename(e)}>
            <input aria-label="Название группы" value={name} maxLength={128} onChange={(e) => setName(e.target.value)} />
            <button type="submit">Сохранить</button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setName(group.name);
              }}
            >
              Отмена
            </button>
          </form>
        ) : (
          <>
            <h3>
              {group.name} <span className="filter__count">{group.need_count}</span>
            </h3>
            <button type="button" onClick={() => setEditing(true)}>
              Переименовать
            </button>
          </>
        )}
        <span className="group-card__spacer" />
        <button type="button" aria-label={`Выше: ${group.name}`} disabled={first} onClick={() => onMove(-1)}>
          ↑
        </button>
        <button type="button" aria-label={`Ниже: ${group.name}`} disabled={last} onClick={() => onMove(1)}>
          ↓
        </button>
        <button type="button" onClick={onDelete}>
          Удалить
        </button>
      </header>
      {group.statuses.length === 0 ? (
        <p className="muted">В группе нет статусов.</p>
      ) : (
        <ul className="chips">
          {group.statuses.map((s) => (
            <li key={s} className="chip">
              {s}
              <button type="button" aria-label={`Убрать статус «${s}» из группы «${group.name}»`} onClick={() => onRemove(s)}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="group-card__add"
        onSubmit={(e) => {
          e.preventDefault();
          if (pick) void onAdd(pick).then((ok) => ok && setPick(""));
        }}
      >
        <select aria-label={`Статус для группы «${group.name}»`} value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">Выберите статус</option>
          {choices.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button type="submit" disabled={!pick}>
          Добавить статус
        </button>
      </form>
    </section>
  );
}
