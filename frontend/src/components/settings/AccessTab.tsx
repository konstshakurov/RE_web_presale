import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { AdminUser } from "../../api/types";
import { formatDateTime } from "../../lib/needs";
import Modal from "../Modal";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Не удалось выполнить действие";
}

// Список допущенных (FR-021, UC-006): добавление, отзыв и возврат доступа. 401 уводит на вход
// (обработчик клиента); сообщения сервера (последний админ) показываются как есть.
export default function AccessTab() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState<AdminUser | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.adminUsers();
      if (!Array.isArray(list)) throw new Error("bad response");
      setUsers(list);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить список допущенных");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="toolbar">
        <button type="button" className="primary" onClick={() => setAdding(true)}>
          Добавить
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {users === null && !error && <p className="muted">Загрузка…</p>}
      {users && (
        <table className="needs">
          <thead>
            <tr>
              <th>Логин</th>
              <th>Роль</th>
              <th>Статус</th>
              <th>Добавлен</th>
              <th>Изменён</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.login}>
                <td>{u.login}</td>
                <td>{u.role === "admin" ? "Администратор" : "Пользователь"}</td>
                <td>{u.active ? "Допущен" : "Отозван"}</td>
                <td>{formatDateTime(u.created_at)}</td>
                <td>{formatDateTime(u.updated_at)}</td>
                <td>
                  <button type="button" className="link" onClick={() => setConfirm(u)}>
                    {u.active ? "Отозвать" : "Вернуть доступ"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {adding && (
        <AddUserDialog
          onClose={() => setAdding(false)}
          onDone={() => {
            setAdding(false);
            void load();
          }}
        />
      )}
      {confirm && (
        <ConfirmDialog
          user={confirm}
          onClose={() => setConfirm(null)}
          onDone={() => {
            setConfirm(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

function AddUserDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [login, setLogin] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const u = await api.addUser(login.trim());
      if (role !== u.role) await api.patchUser(u.login, { role });
      onDone();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }

  return (
    <Modal title="Добавить пользователя" onClose={onClose}>
      <form className="modal__form" onSubmit={(e) => void onSubmit(e)}>
        <label>
          Логин
          <input value={login} maxLength={128} required onChange={(e) => setLogin(e.target.value)} />
        </label>
        <label>
          Роль
          <select value={role} onChange={(e) => setRole(e.target.value as "user" | "admin")}>
            <option value="user">Пользователь</option>
            <option value="admin">Администратор</option>
          </select>
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal__actions">
          <button type="submit" className="primary" disabled={busy}>
            Добавить
          </button>
          <button type="button" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ConfirmDialog({
  user,
  onClose,
  onDone,
}: {
  user: AdminUser;
  onClose: () => void;
  onDone: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const revoke = user.active;

  async function run() {
    setBusy(true);
    setError("");
    try {
      await api.patchUser(user.login, { active: !revoke });
      onDone();
    } catch (err) {
      setError(errText(err)); // «нельзя отозвать последнего администратора» - как есть
      setBusy(false);
    }
  }

  return (
    <Modal title={revoke ? "Отозвать доступ" : "Вернуть доступ"} onClose={onClose}>
      <p>
        {revoke
          ? `Отозвать доступ у ${user.login}? Все его сессии завершатся сразу.`
          : `Вернуть доступ пользователю ${user.login}?`}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal__actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void run()}>
          Подтвердить
        </button>
        <button type="button" onClick={onClose}>
          Отмена
        </button>
      </div>
    </Modal>
  );
}
