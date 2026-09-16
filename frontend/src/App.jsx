import { Navigate, Route, Routes } from "react-router-dom";

import LoginPage from "./pages/LoginPage.jsx";
import StudentPage from "./pages/StudentPage.jsx";
import InstructorPage from "./pages/InstructorPage.jsx";
import HodPage from "./pages/HodPage.jsx";
import AdminPage from "./pages/AdminPage.jsx";

/**
 * Role dashboards per docs/PRD.md:
 *   /login       - role entry (JWT auth to be wired later)
 *   /student     - Module 1: Zero-Proxy Attendance Engine
 *   /instructor  - Modules 1-3: sessions, proposals, workload
 *   /hod         - Module 2: 2-of-3 approvals
 *   /admin       - timetables, device rebinding, system status
 */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/student" element={<StudentPage />} />
      <Route path="/instructor" element={<InstructorPage />} />
      <Route path="/hod" element={<HodPage />} />
      <Route path="/admin" element={<AdminPage />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
