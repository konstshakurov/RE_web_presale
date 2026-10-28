// Явная передача учётных данных менеджеру паролей через Credential Management API (CR-011).
// Нужна для Chrome, который не предлагает сохранить пароль после fetch-входа; в Firefox API нет,
// там работает штатная форма. Пароль не сохраняется и не логируется: только передаётся браузеру.

interface PasswordCredentialInit {
  id: string;
  password: string;
  name?: string;
}

type PasswordCredentialCtor = new (init: PasswordCredentialInit) => Credential;

export function storePasswordCredential(id: string, password: string): void {
  try {
    const Ctor = (window as unknown as { PasswordCredential?: PasswordCredentialCtor }).PasswordCredential;
    const creds = navigator.credentials;
    if (!Ctor || typeof creds?.store !== "function") return;
    // Без await: результат не влияет на вход; отказ браузера молча игнорируется.
    void Promise.resolve(creds.store(new Ctor({ id, password, name: id }))).catch(() => undefined);
  } catch {
    // нет API / не secure context - вход не страдает
  }
}
