import { NavLink } from "react-router-dom";

import logoHead from "../assets/logo_head.svg";
import logoWord from "../assets/logo_wordmark.svg";
import { useAuth } from "../auth/AuthContext";
import { shortName } from "../lib/shortName";

// Разделы верхнего меню (FR-031); «Настройки» только при role=admin.
const SECTIONS = [
  { path: "/needs", title: "Потребности", adminOnly: false },
  { path: "/employees", title: "Сотрудники", adminOnly: false },
  { path: "/resumes", title: "Резюме", adminOnly: false },
  { path: "/settings", title: "Настройки", adminOnly: true },
];

export default function Header() {
  const { user, logout } = useAuth();
  const isAdmin = user?.role === "admin";
  return (
    <header className="topbar">
      <a className="topbar__brand" href="/" aria-label="regoose">
        <img className="topbar__head" src={logoHead} alt="" />
        <span className="topbar__wordbox">
          <span className="topbar__wordline">
            <span className="topbar__wordslot">
              <img className="topbar__word" src={logoWord} alt="" />
            </span>
          </span>
        </span>
      </a>
      {user && (
        <>
          <nav className="topbar__nav" aria-label="Разделы">
            {SECTIONS.filter((s) => !s.adminOnly || isAdmin).map((s) => (
              <NavLink key={s.path} to={s.path}>
                {s.title}
              </NavLink>
            ))}
          </nav>
          <div className="topbar__user">
            <span className="topbar__name" title={`${user.login} · ${isAdmin ? "администратор" : "пользователь"}`}>
              {shortName(user.display_name, user.login)}
            </span>
            {isAdmin && (
              <span className="topbar__badge">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z" />
                  <path d="M9 12l2 2 4-4" />
                </svg>
                Админ
              </span>
            )}
            <button type="button" className="topbar__out" aria-label="Выйти" title="Выйти" onClick={() => void logout()}>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <path d="M16 17l5-5-5-5" />
                <path d="M21 12H9" />
              </svg>
            </button>
          </div>
        </>
      )}
    </header>
  );
}
