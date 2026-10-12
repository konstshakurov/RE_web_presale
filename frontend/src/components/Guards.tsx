import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import ForbiddenPage from "../pages/ForbiddenPage";

export function ProtectedRoute() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="content">Загрузка…</p>;
  // 401 на любой странице: на вход, с запоминанием маршрута (возврат после входа)
  if (!user) {
    const from = location.pathname + location.search;
    return <Navigate to="/login" replace state={{ from }} />;
  }
  return <Outlet />;
}

// Сервер проверяет доступ отдельно (admin-API); здесь — только UI.
export function AdminRoute() {
  const { user } = useAuth();
  if (user?.role !== "admin") return <ForbiddenPage />;
  return <Outlet />;
}
