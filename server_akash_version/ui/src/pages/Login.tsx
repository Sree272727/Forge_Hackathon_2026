import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getGoogleAuthUrl } from '../api/auth';

const REDIRECT_URI = 'http://localhost:3000/auth/google/callback';

export default function Login() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isAuthenticated) {
    navigate('/dashboard');
    return null;
  }

  const handleGoogleLogin = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const { url } = await getGoogleAuthUrl(REDIRECT_URI);
      window.location.href = url;
    } catch {
      setError('Failed to initiate Google login. Please try again.');
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0B021C] relative overflow-hidden">
      <div className="absolute inset-0 opacity-[0.13]">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: `
              linear-gradient(to right, #D0D5DD 1px, transparent 1px),
              linear-gradient(to bottom, #D0D5DD 1px, transparent 1px)
            `,
            backgroundSize: '152px 152px',
          }}
        />
      </div>

      <div className="absolute -bottom-[400px] -left-[400px] w-[800px] h-[800px] bg-[#8243EA] opacity-40 rounded-full blur-[300px] pointer-events-none" />
      <div className="absolute bottom-[200px] left-[200px] w-[600px] h-[600px] bg-[#8243EA] opacity-20 rounded-full blur-[300px] pointer-events-none" />

      <div className="relative z-10 min-h-screen flex flex-col">
        <header className="px-8 py-6 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <div className="w-10 h-10 bg-[#8243EA] rounded-lg flex items-center justify-center">
              <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>
            <span className="text-white font-semibold text-xl">ExcelAI</span>
          </Link>

          <Link
            to="/"
            className="px-5 py-2.5 text-sm font-semibold text-gray-200 border border-white/15 rounded-lg hover:bg-white/5 transition-colors"
          >
            Back to Home
          </Link>
        </header>

        <main className="flex-1 flex items-center justify-center px-4">
          <div className="w-full max-w-md bg-[#1a1a2e]/80 backdrop-blur-sm border border-white/10 rounded-2xl p-8">
            <div className="text-center mb-6">
              <h1 className="text-white text-2xl font-bold mb-2">Login to ExcelAI</h1>
              <p className="text-gray-400 text-sm">Continue with Google to access your dashboard</p>
            </div>

            {error && (
              <div className="mb-4 p-3 bg-red-500/10 border border-red-400/40 rounded-lg text-red-200 text-sm">
                {error}
              </div>
            )}

            <button
              onClick={handleGoogleLogin}
              disabled={isLoading}
              className="w-full flex items-center justify-center gap-3 px-4 py-3.5 bg-[#252542] border border-white/10 rounded-xl text-gray-100 font-medium hover:bg-[#2a2a4a] hover:border-[#8243EA]/40 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <svg className="animate-spin h-5 w-5 text-[#A78BFA]" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
              ) : (
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path
                    fill="#4285F4"
                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                  />
                  <path
                    fill="#34A853"
                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                  />
                  <path
                    fill="#EA4335"
                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  />
                </svg>
              )}
              <span>{isLoading ? 'Signing in...' : 'Continue with Google'}</span>
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
