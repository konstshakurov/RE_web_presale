import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { AdminRoute, ProtectedRoute } from "./components/Guards";
import Layout from "./components/Layout";
import EmployeesPage from "./pages/EmployeesPage";
import LoginPage from "./pages/LoginPage";
import NeedsPage from "./pages/NeedsPage";
import ResumesPage from "./pages/ResumesPage";
import SettingsPage from "./pages/SettingsPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/needs" element={<NeedsPage />} />
            <Route path="/employees" element={<EmployeesPage />} />
            <Route path="/resumes" element={<ResumesPage />} />
            <Route element={<AdminRoute />}>
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/needs" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
