import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: ReactNode;
  activeNavItem?: string;
  onNavItemClick?: (id: string) => void;
}

export default function Layout({ children, activeNavItem = 'chat', onNavItemClick }: LayoutProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <div className="flex h-screen bg-[#0f0f1a]">
      {/* Sidebar */}
      <Sidebar activeItem={activeNavItem} onItemClick={onNavItemClick} />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top Header */}
        <header className="h-16 bg-[#1a1a2e]/80 backdrop-blur-sm border-b border-white/10 flex items-center justify-between px-6">
          <div className="flex items-center gap-4">
            <h1 className="text-lg font-bold text-white">Dashboard</h1>
            <span className="px-2.5 py-1 bg-[#8243EA]/20 text-[#A78BFA] text-xs font-semibold rounded-full">Pro</span>
          </div>

          {/* User Profile */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-3">
              {user?.profile_picture ? (
                <img
                  src={user.profile_picture}
                  alt={user.full_name || user.email}
                  className="w-10 h-10 rounded-full ring-2 ring-[#8243EA]/50"
                />
              ) : (
                <div className="w-10 h-10 bg-gradient-to-br from-[#8243EA] to-[#6366F1] rounded-full flex items-center justify-center ring-2 ring-[#8243EA]/30">
                  <span className="text-white font-bold text-sm">
                    {user?.email?.charAt(0).toUpperCase()}
                  </span>
                </div>
              )}
              <div className="hidden sm:block">
                <p className="text-sm font-semibold text-white">
                  {user?.full_name || user?.email}
                </p>
                <p className="text-xs text-gray-400">{user?.email}</p>
              </div>
            </div>
            <button
              onClick={handleLogout}
              className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-all"
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 overflow-auto bg-gradient-to-br from-[#0f0f1a] via-[#1a1a2e] to-[#0f0f1a]">
          {children}
        </main>
      </div>
    </div>
  );
}
