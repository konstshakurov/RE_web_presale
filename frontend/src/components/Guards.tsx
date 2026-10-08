import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import ForbiddenPage from "../pages/ForbiddenPage";

export function ProtectedRoute() {
  const { user, loading } = useAuth();
  if (loading) return <p className="content">Загрузка…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

// Сервер проверяет доступ отдельно (admin-API); здесь — только UI.
export function AdminRoute() {
  const { user } = useAuth();
  if (user?.role !== "admin") return <ForbiddenPage />;
  return <Outlet />;
}
