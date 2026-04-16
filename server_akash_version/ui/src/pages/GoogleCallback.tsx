import { useEffect, useState, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { exchangeGoogleCode } from '../api/auth';

const REDIRECT_URI = 'http://localhost:3000/auth/google/callback';

export default function GoogleCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { login } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const hasAttempted = useRef(false);

  useEffect(() => {
    // Prevent double execution in React strict mode
    if (hasAttempted.current) return;

    const code = searchParams.get('code');
    const errorParam = searchParams.get('error');

    if (errorParam) {
      setError('Google authentication was cancelled or failed.');
      return;
    }

    if (!code) {
      setError('No authorization code received from Google.');
      return;
    }

    hasAttempted.current = true;

    // Exchange code for tokens
    const authenticate = async () => {
      try {
        console.log('Exchanging code for tokens...');
        const response = await exchangeGoogleCode(code, REDIRECT_URI);
        console.log('Authentication successful:', response.user.email);
        login(response.user, response.tokens);
        navigate('/dashboard');
      } catch (err) {
        console.error('Authentication error:', err);
        setError(err instanceof Error ? err.message : 'Authentication failed');
      }
    };

    authenticate();
  }, [searchParams, login, navigate]);

  if (error) {
    return (
      <div className="min-h-screen bg-[#0B021C] relative overflow-hidden flex items-center justify-center p-4">
        <div className="absolute inset-0 opacity-[0.12]">
          <div
            className="absolute inset-0"
            style={{
              backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 26px, #8243EA30 26px, #8243EA30 27px), repeating-linear-gradient(90deg, transparent, transparent 26px, #8243EA30 26px, #8243EA30 27px)',
            }}
          />
        </div>
        <div className="absolute -bottom-[320px] -left-[320px] w-[640px] h-[640px] bg-[#8243EA] opacity-30 rounded-full blur-[260px] pointer-events-none" />
        <div className="max-w-md w-full bg-[#1a1a2e]/70 backdrop-blur-sm border border-white/10 rounded-2xl p-8 text-center relative z-10">
          <div className="w-16 h-16 bg-red-500/10 border border-red-400/40 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg
              className="w-8 h-8 text-red-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-white mb-2">
            Authentication Failed
          </h1>
          <p className="text-gray-300 mb-6">{error}</p>
          <button
            onClick={() => navigate('/')}
            className="px-6 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg hover:opacity-90 transition-opacity"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0B021C] relative overflow-hidden flex items-center justify-center p-4">
      <div className="absolute inset-0 opacity-[0.12]">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 26px, #8243EA30 26px, #8243EA30 27px), repeating-linear-gradient(90deg, transparent, transparent 26px, #8243EA30 26px, #8243EA30 27px)',
          }}
        />
      </div>
      <div className="absolute -bottom-[320px] -left-[320px] w-[640px] h-[640px] bg-[#8243EA] opacity-30 rounded-full blur-[260px] pointer-events-none" />
      <div className="max-w-md w-full bg-[#1a1a2e]/70 backdrop-blur-sm border border-white/10 rounded-2xl p-8 text-center relative z-10">
        <div className="w-16 h-16 bg-[#252542] border border-white/10 rounded-full flex items-center justify-center mx-auto mb-4">
          <svg
            className="animate-spin h-8 w-8 text-[#A78BFA]"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
        </div>
        <h1 className="text-xl font-bold text-white mb-2">
          Signing you in...
        </h1>
        <p className="text-gray-300">
          Please wait while we complete your authentication.
        </p>
      </div>
    </div>
  );
}
