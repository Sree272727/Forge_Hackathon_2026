import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { refreshAuthTokens, STORAGE_KEY_TOKENS, type Tokens } from '../api/auth';
import {
  ApiError,
  getDataSource,
  getDataSourceAnalysis,
  type DataSource,
  type WorkbookAnalysis,
} from '../api/dataSources';
import Layout from '../components/Layout';

export default function DataSourceDetail() {
  const { id } = useParams<{ id: string }>();
  const { isAuthenticated, isLoading: authLoading, tokens, updateTokens, logout } = useAuth();
  const navigate = useNavigate();

  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [analysis, setAnalysis] = useState<WorkbookAnalysis | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const accessToken = tokens?.access_token ?? '';

  const withAuthRetry = async <T,>(requestFn: (token: string) => Promise<T>): Promise<T> => {
    const currentAccessToken = tokens?.access_token;
    if (!currentAccessToken) {
      throw new Error('You must be logged in to continue');
    }

    try {
      return await requestFn(currentAccessToken);
    } catch (error) {
      const status =
        error instanceof ApiError
          ? error.status
          : (typeof error === 'object' && error && 'status' in error ? Number((error as { status: unknown }).status) : 0);
      const isUnauthorized = status === 401;

      let refreshToken: string | undefined = tokens?.refresh_token;
      if (!refreshToken) {
        const storedTokensRaw = localStorage.getItem(STORAGE_KEY_TOKENS);
        if (storedTokensRaw) {
          try {
            const parsed = JSON.parse(storedTokensRaw) as Partial<Tokens>;
            refreshToken = parsed.refresh_token ?? undefined;
          } catch {
            refreshToken = undefined;
          }
        }
      }

      if (!isUnauthorized || !refreshToken) {
        throw error;
      }

      try {
        const refreshed = await refreshAuthTokens(refreshToken);
        const refreshedTokens: Tokens = {
          access_token: refreshed.access_token,
          refresh_token: refreshed.refresh_token,
          token_type: refreshed.token_type,
          expires_in: refreshed.expires_in,
        };
        updateTokens(refreshedTokens);
        return await requestFn(refreshedTokens.access_token);
      } catch {
        logout();
        navigate('/');
        throw new Error('Your session has expired. Please sign in again.');
      }
    }
  };

  useEffect(() => {
    const fetchData = async () => {
      if (!id || !accessToken) return;

      setIsLoading(true);
      setError(null);

      try {
        const [ds, analysisData] = await Promise.all([
          withAuthRetry((token) => getDataSource(token, id)),
          withAuthRetry((token) => getDataSourceAnalysis(token, id)),
        ]);

        setDataSource(ds);
        setAnalysis(analysisData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data source');
      } finally {
        setIsLoading(false);
      }
    };

    if (isAuthenticated && accessToken) {
      fetchData();
    }
  }, [id, isAuthenticated, accessToken]);

  if (!authLoading && !isAuthenticated) {
    navigate('/');
    return null;
  }

  if (authLoading || isLoading) {
    return (
      <Layout activeNavItem="my-files" onNavItemClick={(navId) => navigate(`/dashboard/${navId}`)}>
        <div className="h-full flex items-center justify-center">
          <div className="animate-spin h-8 w-8 border-4 border-[#8243EA] border-t-transparent rounded-full" />
        </div>
      </Layout>
    );
  }

  if (error || !dataSource) {
    return (
      <Layout activeNavItem="my-files" onNavItemClick={(navId) => navigate(`/dashboard/${navId}`)}>
        <div className="h-full m-4 flex flex-col">
          <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
            <div className="text-center py-12">
              <div className="w-16 h-16 bg-red-500/10 border border-red-400/30 rounded-xl flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Error Loading Data Source</h3>
              <p className="text-gray-400 text-sm mb-4">{error || 'Data source not found'}</p>
              <button
                onClick={() => navigate('/dashboard/my-files')}
                className="px-6 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg hover:opacity-90 transition-opacity"
              >
                Back to Files
              </button>
            </div>
          </div>
        </div>
      </Layout>
    );
  }

  const formatFileSize = (bytes: number): string => {
    const mb = bytes / (1024 * 1024);
    if (mb >= 1) return `${mb.toFixed(2)} MB`;
    const kb = bytes / 1024;
    return `${kb.toFixed(1)} KB`;
  };

  const formatDate = (isoDate: string): string => {
    return new Date(isoDate).toLocaleString();
  };

  return (
    <Layout activeNavItem="my-files" onNavItemClick={(navId) => navigate(`/dashboard/${navId}`)}>
      <div className="h-full m-4 flex flex-col gap-4 overflow-auto">
        {/* Back Button & Header */}
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/dashboard/my-files')}
            className="p-2 bg-[#252542] border border-white/10 rounded-lg text-gray-400 hover:text-white hover:border-[#8243EA]/50 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-2xl font-bold text-white">{dataSource.name}</h1>
            <p className="text-gray-400 text-sm">{dataSource.original_file_name}</p>
          </div>
        </div>

        {/* File Info Card */}
        <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
          <h2 className="text-[#A78BFA] font-semibold mb-4 flex items-center gap-2">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            File Information
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-[#252542] rounded-xl p-4">
              <p className="text-gray-400 text-xs uppercase tracking-wide">File Size</p>
              <p className="text-xl font-bold text-white mt-1">{formatFileSize(dataSource.file_size_bytes)}</p>
            </div>
            <div className="bg-[#252542] rounded-xl p-4">
              <p className="text-gray-400 text-xs uppercase tracking-wide">Sheets</p>
              <p className="text-xl font-bold text-white mt-1">{dataSource.sheet_count}</p>
            </div>
            <div className="bg-[#252542] rounded-xl p-4">
              <p className="text-gray-400 text-xs uppercase tracking-wide">Extension</p>
              <p className="text-xl font-bold text-white mt-1">{dataSource.file_extension}</p>
            </div>
            <div className="bg-[#252542] rounded-xl p-4">
              <p className="text-gray-400 text-xs uppercase tracking-wide">Uploaded</p>
              <p className="text-sm font-semibold text-white mt-1">{formatDate(dataSource.created_at)}</p>
            </div>
          </div>
          <div className="mt-4 bg-[#252542] rounded-xl p-4">
            <p className="text-gray-400 text-xs uppercase tracking-wide mb-2">Sheet Names</p>
            <div className="flex flex-wrap gap-2">
              {dataSource.sheet_names.map((name, idx) => (
                <span key={idx} className="px-3 py-1 bg-[#8243EA]/20 text-[#C4B5FD] rounded-lg text-sm">
                  {name}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Analysis Section */}
        {analysis ? (
          <>
            {/* Analysis Summary */}
            <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
              <h2 className="text-[#A78BFA] font-semibold mb-4 flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                Workbook Analysis
              </h2>

              {analysis.overall_purpose && (
                <div className="mb-4 p-4 bg-[#252542] rounded-xl border-l-4 border-[#8243EA]">
                  <p className="text-gray-400 text-xs uppercase tracking-wide mb-1">Purpose</p>
                  <p className="text-white font-medium">{analysis.overall_purpose}</p>
                </div>
              )}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide">Total Rows</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {analysis.summary?.total_rows?.toLocaleString() ?? '-'}
                  </p>
                </div>
                <div className="bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide">Total Columns</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {analysis.summary?.total_columns?.toLocaleString() ?? '-'}
                  </p>
                </div>
                <div className="bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide">Formulas</p>
                  <p className={`text-xl font-bold mt-1 ${analysis.total_formulas > 0 ? 'text-[#A78BFA]' : 'text-white'}`}>
                    {analysis.total_formulas.toLocaleString()}
                  </p>
                </div>
                <div className="bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide">Errors</p>
                  <p className={`text-xl font-bold mt-1 ${analysis.total_errors > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                    {analysis.total_errors.toLocaleString()}
                  </p>
                </div>
              </div>

              {/* Formula Categories */}
              {analysis.summary?.formula_categories && analysis.summary.formula_categories.length > 0 && (
                <div className="mt-4 bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide mb-2">Formula Categories</p>
                  <div className="flex flex-wrap gap-2">
                    {analysis.summary.formula_categories.map((cat, idx) => (
                      <span key={idx} className="px-3 py-1 bg-[#8243EA]/20 text-[#C4B5FD] rounded-lg text-sm font-mono">
                        {cat}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Error Types */}
              {analysis.summary?.error_types && analysis.summary.error_types.length > 0 && (
                <div className="mt-4 bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide mb-2">Error Types Found</p>
                  <div className="flex flex-wrap gap-2">
                    {analysis.summary.error_types.map((errType, idx) => (
                      <span key={idx} className="px-3 py-1 bg-red-500/20 text-red-300 rounded-lg text-sm font-mono">
                        {errType}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Column Purposes */}
              {analysis.summary?.column_purposes && Object.keys(analysis.summary.column_purposes).length > 0 && (
                <div className="mt-4 bg-[#252542] rounded-xl p-4">
                  <p className="text-gray-400 text-xs uppercase tracking-wide mb-2">Column Types</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(analysis.summary.column_purposes).map(([purpose, count], idx) => (
                      <span key={idx} className="px-3 py-1 bg-emerald-500/20 text-emerald-300 rounded-lg text-sm">
                        {purpose}: {count}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Sheet Details */}
            <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
              <h2 className="text-[#A78BFA] font-semibold mb-4 flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                </svg>
                Sheet Details ({analysis.sheets.length})
              </h2>

              <div className="space-y-4">
                {analysis.sheets.map((sheet, idx) => (
                  <div key={idx} className="bg-[#252542] rounded-xl p-4">
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <h3 className="text-white font-semibold text-lg">{sheet.name}</h3>
                        {sheet.inferred_purpose && (
                          <p className="text-gray-400 text-sm mt-1">{sheet.inferred_purpose}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-4 text-sm">
                        <span className="text-gray-400">
                          {sheet.row_count.toLocaleString()} rows
                        </span>
                        <span className="text-gray-400">
                          {sheet.column_count} cols
                        </span>
                        {sheet.formula_count > 0 && (
                          <span className="text-[#A78BFA]">
                            {sheet.formula_count} formulas
                          </span>
                        )}
                        {sheet.error_count > 0 && (
                          <span className="text-red-400">
                            {sheet.error_count} errors
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Data Patterns */}
                    {sheet.data_patterns && sheet.data_patterns.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-white/10">
                        <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Data Patterns</p>
                        <div className="flex flex-wrap gap-2">
                          {sheet.data_patterns.map((pattern, pIdx) => (
                            <span key={pIdx} className="px-2 py-1 bg-white/5 text-gray-300 rounded text-xs">
                              {pattern}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          /* No Analysis Yet */
          <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-[#252542] border border-white/10 rounded-xl flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Analysis Pending</h3>
              <p className="text-gray-400 text-sm mb-4">
                The workbook analysis is still being processed. This happens automatically in the background.
              </p>
              <button
                onClick={() => window.location.reload()}
                className="px-4 py-2 bg-white/5 border border-white/10 text-white rounded-lg text-sm hover:bg-white/10 transition-colors"
              >
                Refresh
              </button>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
          <div className="flex items-center gap-4">
            <button
              onClick={() => {
                navigate('/dashboard/ask-ai');
                // The dashboard will handle selecting this data source
              }}
              className="flex-1 py-3 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-xl font-semibold hover:opacity-90 transition-opacity flex items-center justify-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              Ask Questions About This File
            </button>
          </div>
        </div>
      </div>
    </Layout>
  );
}
