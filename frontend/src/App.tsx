import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { AdminRoute, ProtectedRoute } from "./components/Guards";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import NeedsPage from "./pages/NeedsPage";
import Stub from "./pages/Stub";

// Содержимое разделов — задачи Dev-8, 9, 10 (Потребности, Сотрудники, Резюме) и Dev-21 (Настройки).
export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/needs" element={<NeedsPage />} />
            <Route path="/employees" element={<Stub title="Сотрудники" />} />
            <Route path="/resumes" element={<Stub title="Резюме" />} />
            <Route element={<AdminRoute />}>
              <Route path="/settings" element={<Stub title="Настройки" />} />
            </Route>
            <Route path="*" element={<Navigate to="/needs" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
