import { Routes, Route } from 'react-router-dom';
import StudentPage from './pages/StudentPage';
import InstructorPage from './pages/InstructorPage';
import HodPage from './pages/HodPage';
import AdminPage from './pages/AdminPage';
import LoginPage from './pages/LoginPage';  

function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/student" element={<StudentPage />} />
      <Route path="/instructor" element={<InstructorPage />} />
      <Route path="/hod" element={<HodPage />} />
      <Route path="/admin" element={<AdminPage />} />
    </Routes>
  );
}

export default App;