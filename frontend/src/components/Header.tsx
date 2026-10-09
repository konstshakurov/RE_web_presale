import { NavLink } from "react-router-dom";

import logo from "../assets/logo.png";
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
      <img className="topbar__logo" src={logo} alt="RE" />
      {user && (
        <>
          <nav className="topbar__nav" aria-label="Разделы">
            {SECTIONS.filter((s) => !s.adminOnly || isAdmin).map((s) => (
              <NavLink key={s.path} to={s.path}>
                {s.title}
              </NavLink>
            ))}
          </nav>
          <span className="topbar__user">{shortName(user.display_name, user.login)}</span>
          <button type="button" onClick={() => void logout()}>
            Выйти
          </button>
        </>
      )}
    </header>
  );
}
