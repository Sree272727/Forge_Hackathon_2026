import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import Home from './pages/Home';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import DataSourceDetail from './pages/DataSourceDetail';
import GoogleCallback from './pages/GoogleCallback';

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/dashboard/*" element={<Dashboard />} />
          <Route path="/data-source/:id" element={<DataSourceDetail />} />
          <Route path="/auth/google/callback" element={<GoogleCallback />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
