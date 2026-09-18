import { Navigate, Route, Routes } from 'react-router-dom';
import StudentPage from './pages/StudentPage';
import InstructorPage from './pages/InstructorPage';
import HodPage from './pages/HodPage';
import AdminPage from './pages/AdminPage';
import LoginPage from './pages/LoginPage';  

function App() {
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

export default App;