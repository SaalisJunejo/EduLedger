import { useEffect, useState } from 'react';
import { Navigate, Outlet, Route, Routes, useNavigate, useOutletContext } from 'react-router-dom';
import Button from './components/ui/Button.jsx';
import Card from './components/ui/Card.jsx';
import Spinner from './components/ui/Spinner.jsx';
import StatusBanner from './components/ui/StatusBanner.jsx';
import { getRolePath, getToken, loadUser, logout } from './lib/auth.js';
import StudentPage from './pages/StudentPage';
import InstructorPage from './pages/InstructorPage';
import HodPage from './pages/HodPage';
import AdminPage from './pages/AdminPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import SetupPage from './pages/SetupPage';

function ProtectedRoutes() {
  const navigate = useNavigate();
  const token = getToken();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({ token: null, user: null, error: null });

  useEffect(() => {
    if (!token) return;
    let active = true;
    setState({ token, user: null, error: null });
    loadUser()
      .then((user) => {
        if (active) setState({ token, user, error: null });
      })
      .catch((error) => {
        if (active) setState({ token, user: null, error });
      });
    return () => { active = false; };
  }, [token, attempt]);

  if (!token) return <Navigate to="/login" replace />;

  if (state.token !== token || (!state.user && !state.error)) {
    return (
      <main className="page page-centered">
        <Card>
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <Spinner /> Checking your account…
          </div>
        </Card>
      </main>
    );
  }

  if (state.error) {
    return (
      <main className="page page-centered">
        <Card className="w-full max-w-md">
          <StatusBanner tone="error" title="Couldn't load your account">
            {state.error.message}
          </StatusBanner>
          <div className="mt-4 flex gap-2">
            <Button onClick={() => setAttempt((value) => value + 1)}>Retry</Button>
            <Button variant="secondary" onClick={() => {
              logout();
              navigate('/login', { replace: true });
            }}>Logout</Button>
          </div>
        </Card>
      </main>
    );
  }

  return <Outlet context={state.user} />;
}

function RoleRoute({ role, children }) {
  const user = useOutletContext();
  if (user.role !== role) return <Navigate to={getRolePath(user.role)} replace />;
  return children;
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      {/* Public, outside the route guard: nobody holds a token before these run. */}
      <Route path="/register/hod" element={<RegisterPage role="hod" />} />
      <Route path="/register/faculty" element={<RegisterPage role="faculty" />} />
      <Route path="/register/student" element={<RegisterPage role="student" />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<ProtectedRoutes />}>
        <Route path="/student" element={<RoleRoute role="STUDENT"><StudentPage /></RoleRoute>} />
        <Route path="/instructor" element={<RoleRoute role="FACULTY"><InstructorPage /></RoleRoute>} />
        <Route path="/hod" element={<RoleRoute role="HOD"><HodPage /></RoleRoute>} />
        <Route path="/admin" element={<RoleRoute role="ADMIN"><AdminPage /></RoleRoute>} />
      </Route>
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

export default App;