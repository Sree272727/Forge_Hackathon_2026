import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { STORAGE_KEY_TOKENS, STORAGE_KEY_USER, type User, type Tokens } from '../api/auth';

interface AuthContextType {
  user: User | null;
  tokens: Tokens | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (user: User, tokens: Tokens) => void;
  updateTokens: (tokens: Tokens) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [tokens, setTokens] = useState<Tokens | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Load auth state from localStorage on mount
    const storedUser = localStorage.getItem(STORAGE_KEY_USER);
    const storedTokens = localStorage.getItem(STORAGE_KEY_TOKENS);

    if (storedUser && storedTokens) {
      setUser(JSON.parse(storedUser));
      setTokens(JSON.parse(storedTokens));
    }
    setIsLoading(false);
  }, []);

  const login = (user: User, tokens: Tokens) => {
    setUser(user);
    setTokens(tokens);
    localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user));
    localStorage.setItem(STORAGE_KEY_TOKENS, JSON.stringify(tokens));
  };

  const updateTokens = (newTokens: Tokens) => {
    setTokens(newTokens);
    localStorage.setItem(STORAGE_KEY_TOKENS, JSON.stringify(newTokens));
  };

  const logout = () => {
    setUser(null);
    setTokens(null);
    localStorage.removeItem(STORAGE_KEY_USER);
    localStorage.removeItem(STORAGE_KEY_TOKENS);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        tokens,
        isAuthenticated: !!user,
        isLoading,
        login,
        updateTokens,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
