import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { refreshAuthTokens, STORAGE_KEY_TOKENS, type Tokens } from '../api/auth';
import { ApiError, listDataSources, uploadDataSource, type DataSource } from '../api/dataSources';
import {
  askQuestion,
  deleteConversation,
  getConversation,
  getSchemaInfo,
  getSuggestedQuestions,
  getUsageSummary,
  listConversations,
  processDataSource,
  type Conversation,
  type ConversationListItem,
  type SchemaInfoResponse,
  type UsageSummaryResponse,
} from '../api/excelAgent';
import Layout from '../components/Layout';

export default function Dashboard() {
  const { isAuthenticated, isLoading, tokens, updateTokens, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dataSourceTotal, setDataSourceTotal] = useState(0);
  const [isDataSourcesLoading, setIsDataSourcesLoading] = useState(false);
  const [isUploadLoading, setIsUploadLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [dataSourceName, setDataSourceName] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  // Ask AI state
  const [selectedDataSourceId, setSelectedDataSourceId] = useState<string | null>(null);
  const [schemaInfo, setSchemaInfo] = useState<SchemaInfoResponse | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [question, setQuestion] = useState('');
  const [isAskingQuestion, setIsAskingQuestion] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [chatHistory, setChatHistory] = useState<Array<{
    type: 'user' | 'assistant';
    content: string;
    codeUsed?: string | null;
    executionTime?: number;
    error?: string | null;
    inputTokens?: number | null;
    outputTokens?: number | null;
    costUsd?: number | null;
  }>>([]);
  const [askError, setAskError] = useState<string | null>(null);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);

  // Conversations state
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [conversationsTotal, setConversationsTotal] = useState(0);
  const [isConversationsLoading, setIsConversationsLoading] = useState(false);
  const [_selectedConversation, setSelectedConversation] = useState<Conversation | null>(null);
  const [usageSummary, setUsageSummary] = useState<UsageSummaryResponse | null>(null);

  const accessToken = tokens?.access_token ?? '';
  const section = location.pathname.split('/')[2] || 'ask-ai';

  const selectedFileSummary = useMemo(() => {
    if (!selectedFile) return null;
    const sizeMb = (selectedFile.size / (1024 * 1024)).toFixed(2);
    return `${selectedFile.name} (${sizeMb} MB)`;
  }, [selectedFile]);

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

  const fetchDataSources = async () => {
    if (!accessToken) return;

    setIsDataSourcesLoading(true);
    setUploadError(null);
    try {
      const response = await withAuthRetry((token) => listDataSources(token, 0, 50));
      setDataSources(response.items);
      setDataSourceTotal(response.total);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'Failed to load data sources');
    } finally {
      setIsDataSourcesLoading(false);
    }
  };

  useEffect(() => {
    if ((section === 'my-files' || section === 'ask-ai') && isAuthenticated && accessToken) {
      fetchDataSources();
    }
    if (section === 'conversations' && isAuthenticated && accessToken) {
      fetchConversations();
      fetchUsageSummary();
    }
  }, [section, isAuthenticated, accessToken]);

  const fetchConversations = async () => {
    if (!accessToken) return;

    setIsConversationsLoading(true);
    try {
      const response = await withAuthRetry((token) => listConversations(token, null, 0, 50));
      setConversations(response.items);
      setConversationsTotal(response.total);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    } finally {
      setIsConversationsLoading(false);
    }
  };

  const fetchUsageSummary = async () => {
    if (!accessToken) return;

    try {
      const summary = await withAuthRetry((token) => getUsageSummary(token, 30));
      setUsageSummary(summary);
    } catch (error) {
      console.error('Failed to load usage summary:', error);
    }
  };

  const handleLoadConversation = async (conversationId: string) => {
    if (!accessToken) return;

    try {
      const conversation = await withAuthRetry((token) => getConversation(token, conversationId));
      setSelectedConversation(conversation);

      // Navigate to Ask AI with this conversation
      setSelectedDataSourceId(conversation.data_source_id);
      setCurrentConversationId(conversation.id);

      // Convert messages to chat history
      const history = conversation.messages.map((msg) => ({
        type: msg.role as 'user' | 'assistant',
        content: msg.content,
        codeUsed: msg.code_used,
        executionTime: msg.execution_time_ms ?? undefined,
        error: msg.is_error ? msg.error_message : null,
        inputTokens: msg.input_tokens,
        outputTokens: msg.output_tokens,
        costUsd: msg.cost_usd,
      }));
      setChatHistory(history);

      navigate('/dashboard/ask-ai');
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleDeleteConversation = async (conversationId: string) => {
    if (!accessToken) return;

    try {
      await withAuthRetry((token) => deleteConversation(token, conversationId));
      await fetchConversations();
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  // Fetch schema info when data source is selected
  useEffect(() => {
    const fetchSchemaInfo = async () => {
      if (!selectedDataSourceId || !accessToken) return;

      try {
        const info = await withAuthRetry((token) => getSchemaInfo(token, selectedDataSourceId));
        setSchemaInfo(info);

        // If ready for queries, fetch suggested questions
        if (info.is_ready_for_queries) {
          const suggestions = await withAuthRetry((token) =>
            getSuggestedQuestions(token, selectedDataSourceId)
          );
          setSuggestedQuestions(suggestions.questions);
        }
      } catch (error) {
        // Schema might not exist yet, that's ok
        setSchemaInfo(null);
        setSuggestedQuestions([]);
      }
    };

    fetchSchemaInfo();
  }, [selectedDataSourceId, accessToken]);

  const handleProcessDataSource = async () => {
    if (!selectedDataSourceId || !accessToken) return;

    setIsProcessing(true);
    setAskError(null);

    try {
      const result = await withAuthRetry((token) =>
        processDataSource(token, selectedDataSourceId, false)
      );

      setSchemaInfo({
        data_source_id: result.data_source_id,
        processing_status: result.processing_status,
        is_ready_for_queries: result.is_ready_for_queries,
        workbook_purpose: result.workbook_purpose,
        sheet_count: 0,
        queryable_questions_count: result.queryable_questions.length,
        has_data_quality_notes: result.data_quality_notes.length > 0,
      });

      if (result.is_ready_for_queries) {
        setSuggestedQuestions(result.queryable_questions);
      }
    } catch (error) {
      setAskError(error instanceof Error ? error.message : 'Failed to process data source');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleAskQuestion = async (questionText?: string) => {
    const q = questionText || question;
    if (!q.trim() || !selectedDataSourceId || !accessToken) return;

    setIsAskingQuestion(true);
    setAskError(null);

    // Add user message to chat
    setChatHistory((prev) => [...prev, { type: 'user', content: q }]);
    setQuestion('');

    try {
      const response = await withAuthRetry((token) =>
        askQuestion(token, selectedDataSourceId, q, currentConversationId)
      );

      // Update conversation ID if we got one
      if (response.conversation_id) {
        setCurrentConversationId(response.conversation_id);
      }

      // Add assistant response to chat
      setChatHistory((prev) => [
        ...prev,
        {
          type: 'assistant',
          content: response.success
            ? formatAnswer(response.answer)
            : `Error: ${response.error}`,
          codeUsed: response.code_used,
          executionTime: response.execution_time_ms,
          error: response.error,
          inputTokens: response.input_tokens,
          outputTokens: response.output_tokens,
          costUsd: response.cost_usd,
        },
      ]);
    } catch (error) {
      setChatHistory((prev) => [
        ...prev,
        {
          type: 'assistant',
          content: `Error: ${error instanceof Error ? error.message : 'Failed to get answer'}`,
          error: error instanceof Error ? error.message : 'Unknown error',
        },
      ]);
    } finally {
      setIsAskingQuestion(false);
    }
  };

  const formatAnswer = (answer: unknown): string => {
    if (answer === null || answer === undefined) return 'No result';
    if (typeof answer === 'string') return answer;
    if (typeof answer === 'number') return answer.toLocaleString();
    if (typeof answer === 'boolean') return answer ? 'Yes' : 'No';
    if (Array.isArray(answer)) {
      if (answer.length === 0) return 'No results found';
      // If it's an array of objects (like DataFrame records), format as table
      if (typeof answer[0] === 'object') {
        return JSON.stringify(answer, null, 2);
      }
      return answer.join(', ');
    }
    if (typeof answer === 'object') {
      return JSON.stringify(answer, null, 2);
    }
    return String(answer);
  };

  useEffect(() => {
    if (isAuthenticated && location.pathname === '/dashboard') {
      navigate('/dashboard/ask-ai', { replace: true });
    }
  }, [isAuthenticated, location.pathname, navigate]);

  const handleUpload = async () => {
    setUploadError(null);
    setUploadSuccess(null);

    if (!accessToken) {
      setUploadError('You must be logged in to upload files');
      return;
    }

    if (!dataSourceName.trim()) {
      setUploadError('Please provide a data source name');
      return;
    }

    if (!selectedFile) {
      setUploadError('Please select an Excel file to upload');
      return;
    }

    setIsUploadLoading(true);
    try {
      const created = await withAuthRetry((token) => uploadDataSource(token, dataSourceName.trim(), selectedFile));
      setUploadSuccess(`Created data source: ${created.name}`);
      setDataSourceName('');
      setSelectedFile(null);
      setIsCreateModalOpen(false);
      await fetchDataSources();
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      setIsUploadLoading(false);
    }
  };

  const formatFileSize = (bytes: number): string => {
    const mb = bytes / (1024 * 1024);
    if (mb >= 1) return `${mb.toFixed(2)} MB`;
    const kb = bytes / 1024;
    return `${kb.toFixed(1)} KB`;
  };

  const formatCreatedAt = (isoDate: string): string => {
    return new Date(isoDate).toLocaleString();
  };

  // Redirect to home if not authenticated
  if (!isLoading && !isAuthenticated) {
    navigate('/');
    return null;
  }

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#0B021C] flex items-center justify-center">
        <div className="animate-spin h-8 w-8 border-4 border-[#8243EA] border-t-transparent rounded-full" />
      </div>
    );
  }

  const handleNavClick = (id: string) => {
    navigate(`/dashboard/${id}`);
  };

  const renderContent = () => {
    switch (section) {
      case 'ask-ai':
        return (
          <div className="h-full flex flex-col">
            {/* Main Content Area - Dark Card */}
            <div className="flex-1 m-4 bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl overflow-hidden flex flex-col border border-white/10">
              {/* Card Header with Data Source Selector */}
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="grid grid-cols-2 gap-1">
                    <div className="w-2 h-2 border-2 border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border-2 border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border-2 border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border-2 border-[#8243EA] rounded-sm" />
                  </div>
                  <span className="text-[#A78BFA] font-bold text-lg">Ask AI</span>
                </div>

                {/* Data Source Selector */}
                <div className="flex items-center gap-3">
                  <select
                    value={selectedDataSourceId || ''}
                    onChange={(e) => {
                      setSelectedDataSourceId(e.target.value || null);
                      setChatHistory([]);
                      setSchemaInfo(null);
                      setSuggestedQuestions([]);
                      setCurrentConversationId(null); // Reset conversation
                    }}
                    className="px-4 py-2 bg-[#252542] border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#8243EA]/50"
                  >
                    <option value="">Select a data source...</option>
                    {dataSources.map((ds) => (
                      <option key={ds.id} value={ds.id}>
                        {ds.name}
                      </option>
                    ))}
                  </select>

                  {/* New Conversation Button */}
                  {currentConversationId && (
                    <button
                      onClick={() => {
                        setChatHistory([]);
                        setCurrentConversationId(null);
                      }}
                      className="px-3 py-2 bg-white/5 border border-white/10 text-white rounded-lg text-sm hover:bg-white/10 transition-colors"
                      title="Start new conversation"
                    >
                      New Chat
                    </button>
                  )}

                  {selectedDataSourceId && !schemaInfo?.is_ready_for_queries && (
                    <button
                      onClick={handleProcessDataSource}
                      disabled={isProcessing}
                      className="px-4 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg text-sm font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
                    >
                      {isProcessing ? 'Processing...' : 'Process File'}
                    </button>
                  )}
                </div>
              </div>

              {/* Error Message */}
              {askError && (
                <div className="mx-6 mt-4 p-3 bg-red-500/10 border border-red-400/40 rounded-lg text-sm text-red-200">
                  {askError}
                </div>
              )}

              {/* Schema Info Banner */}
              {schemaInfo && (
                <div className="mx-6 mt-4 p-4 bg-[#252542] border border-white/10 rounded-xl">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-white font-semibold">
                        {schemaInfo.workbook_purpose || 'Data Source Ready'}
                      </p>
                      <p className="text-gray-400 text-sm mt-1">
                        Status: <span className={schemaInfo.is_ready_for_queries ? 'text-emerald-400' : 'text-yellow-400'}>
                          {schemaInfo.processing_status}
                        </span>
                        {schemaInfo.is_ready_for_queries && (
                          <span className="ml-3 text-gray-500">
                            {schemaInfo.queryable_questions_count} suggested questions
                          </span>
                        )}
                      </p>
                    </div>
                    {schemaInfo.is_ready_for_queries && (
                      <div className="flex items-center gap-2 text-emerald-400">
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        <span className="text-sm font-semibold">Ready</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Chat Area */}
              <div className="flex-1 p-6 overflow-auto">
                {!selectedDataSourceId ? (
                  // No data source selected state
                  <div className="h-full flex items-center justify-center">
                    <div className="text-center">
                      <div className="w-20 h-20 bg-[#252542] border border-white/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <svg className="w-10 h-10 text-[#8243EA]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <h3 className="text-lg font-semibold text-white mb-2">Select a Data Source</h3>
                      <p className="text-gray-400 text-sm mb-4">Choose an Excel file to start asking questions</p>
                      {dataSources.length === 0 && (
                        <button
                          onClick={() => navigate('/dashboard/my-files')}
                          className="px-6 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg hover:opacity-90 transition-opacity"
                        >
                          Upload Files
                        </button>
                      )}
                    </div>
                  </div>
                ) : chatHistory.length === 0 && suggestedQuestions.length > 0 ? (
                  // Show suggested questions when no chat yet
                  <div className="space-y-4">
                    <p className="text-gray-400 text-sm font-semibold">Suggested Questions:</p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {suggestedQuestions.slice(0, 6).map((q, idx) => (
                        <button
                          key={idx}
                          onClick={() => handleAskQuestion(q)}
                          disabled={isAskingQuestion}
                          className="p-4 bg-[#252542] border border-white/10 rounded-xl text-left text-gray-300 text-sm hover:border-[#8243EA]/50 hover:bg-[#252542]/80 transition-all"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : chatHistory.length === 0 ? (
                  // Empty state when no suggested questions
                  <div className="h-full flex items-center justify-center">
                    <div className="text-center">
                      <div className="w-16 h-16 bg-[#252542] border border-white/10 rounded-xl flex items-center justify-center mx-auto mb-4">
                        <svg className="w-8 h-8 text-[#8243EA]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                        </svg>
                      </div>
                      <h3 className="text-white font-semibold mb-2">Ask a Question</h3>
                      <p className="text-gray-400 text-sm">Type your question below to analyze your data</p>
                    </div>
                  </div>
                ) : (
                  // Chat history
                  <div className="space-y-4">
                    {chatHistory.map((msg, idx) => (
                      <div
                        key={idx}
                        className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[80%] rounded-xl p-4 ${
                            msg.type === 'user'
                              ? 'bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white'
                              : 'bg-[#252542] border border-white/10 text-gray-200'
                          }`}
                        >
                          {msg.type === 'assistant' && msg.error ? (
                            <p className="text-red-300">{msg.content}</p>
                          ) : (
                            <pre className="whitespace-pre-wrap font-sans text-sm">{msg.content}</pre>
                          )}

                          {/* Show code used for assistant messages */}
                          {msg.type === 'assistant' && msg.codeUsed && !msg.error && (
                            <details className="mt-3 pt-3 border-t border-white/10">
                              <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-300">
                                View Code ({msg.executionTime}ms)
                              </summary>
                              <pre className="mt-2 p-3 bg-[#1a1a2e] rounded-lg text-xs text-gray-300 overflow-x-auto">
                                {msg.codeUsed}
                              </pre>
                            </details>
                          )}

                          {/* Show token usage and cost for assistant messages */}
                          {msg.type === 'assistant' && (msg.inputTokens || msg.outputTokens || msg.costUsd) && (
                            <div className="mt-2 pt-2 border-t border-white/10 flex items-center gap-3 text-xs text-gray-500">
                              {msg.inputTokens != null && <span>In: {msg.inputTokens.toLocaleString()}</span>}
                              {msg.outputTokens != null && <span>Out: {msg.outputTokens.toLocaleString()}</span>}
                              {msg.costUsd != null && <span className="text-emerald-500">${msg.costUsd.toFixed(5)}</span>}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}

                    {/* Loading indicator */}
                    {isAskingQuestion && (
                      <div className="flex justify-start">
                        <div className="bg-[#252542] border border-white/10 rounded-xl p-4">
                          <div className="flex items-center gap-2">
                            <div className="animate-spin h-4 w-4 border-2 border-[#8243EA] border-t-transparent rounded-full" />
                            <span className="text-gray-400 text-sm">Analyzing...</span>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Chat Input at Bottom */}
              <div className="p-4 border-t border-white/10">
                <div className="relative">
                  <input
                    type="text"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleAskQuestion();
                      }
                    }}
                    placeholder={
                      !selectedDataSourceId
                        ? 'Select a data source first...'
                        : !schemaInfo?.is_ready_for_queries
                        ? 'Process the file first...'
                        : 'Ask anything about your data...'
                    }
                    disabled={!selectedDataSourceId || !schemaInfo?.is_ready_for_queries || isAskingQuestion}
                    className="w-full px-5 py-4 bg-[#252542] border border-white/10 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-[#8243EA]/50 transition-colors pr-14 font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                  />
                  <button
                    onClick={() => handleAskQuestion()}
                    disabled={!question.trim() || !selectedDataSourceId || !schemaInfo?.is_ready_for_queries || isAskingQuestion}
                    className="absolute right-2 top-1/2 -translate-y-1/2 w-10 h-10 bg-gradient-to-r from-[#8243EA] to-[#6366F1] rounded-lg flex items-center justify-center hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
        );

      case 'my-files':
        return (
          <div className="h-full m-4 flex flex-col gap-4">
            <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl h-full overflow-hidden flex flex-col border border-white/10">
              {/* Header */}
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="grid grid-cols-2 gap-1">
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                  </div>
                  <span className="text-[#A78BFA] font-semibold">My Files ({dataSourceTotal})</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={fetchDataSources}
                    className="flex items-center gap-2 px-4 py-2 bg-white/5 border border-white/10 text-white rounded-lg text-sm font-semibold hover:bg-white/10 transition-colors"
                  >
                    Refresh
                  </button>
                  <button
                    onClick={() => {
                      setUploadError(null);
                      setUploadSuccess(null);
                      setIsCreateModalOpen(true);
                    }}
                    className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg text-sm font-semibold hover:opacity-90 transition-opacity"
                  >
                    Create Data Source
                  </button>
                </div>
              </div>

              {uploadError && (
                <div className="mx-4 mt-4 p-3 bg-red-500/10 border border-red-400/40 rounded-lg text-sm text-red-200">{uploadError}</div>
              )}
              {uploadSuccess && (
                <div className="mx-4 mt-4 p-3 bg-emerald-500/10 border border-emerald-400/40 rounded-lg text-sm text-emerald-200">{uploadSuccess}</div>
              )}

              {isDataSourcesLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="animate-spin h-7 w-7 border-4 border-[#8243EA] border-t-transparent rounded-full" />
                </div>
              ) : dataSources.length === 0 ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-20 h-20 bg-[#252542] border border-white/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                      <svg className="w-10 h-10 text-[#8243EA]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-white mb-2">No files uploaded</h3>
                    <p className="text-gray-400 text-sm">Upload your Excel files to create your first data source</p>
                  </div>
                </div>
              ) : (
                <div className="flex-1 overflow-auto p-4">
                  <div className="overflow-x-auto rounded-xl border border-white/10">
                    <table className="w-full min-w-[860px] text-sm">
                      <thead className="bg-[#252542] text-gray-300">
                        <tr>
                          <th className="text-left px-4 py-3 font-semibold">Name</th>
                          <th className="text-left px-4 py-3 font-semibold">File</th>
                          <th className="text-left px-4 py-3 font-semibold">Size</th>
                          <th className="text-left px-4 py-3 font-semibold">Tabs</th>
                          <th className="text-left px-4 py-3 font-semibold">Sheet Names</th>
                          <th className="text-left px-4 py-3 font-semibold">Created At</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dataSources.map((source) => (
                          <tr
                            key={source.id}
                            onClick={() => navigate(`/data-source/${source.id}`)}
                            className="border-t border-white/10 bg-[#1a1a2e]/40 hover:bg-[#252542]/60 cursor-pointer transition-colors"
                          >
                            <td className="px-4 py-3 text-white font-semibold">{source.name}</td>
                            <td className="px-4 py-3 text-gray-300">{source.original_file_name}</td>
                            <td className="px-4 py-3 text-gray-300">{formatFileSize(source.file_size_bytes)}</td>
                            <td className="px-4 py-3 text-gray-300">{source.sheet_count}</td>
                            <td className="px-4 py-3 text-gray-300 max-w-[320px] truncate" title={source.sheet_names.join(', ')}>
                              {source.sheet_names.join(', ')}
                            </td>
                            <td className="px-4 py-3 text-gray-400">{formatCreatedAt(source.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            {isCreateModalOpen && (
              <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
                <div className="w-full max-w-2xl bg-[#1a1a2e] border border-white/10 rounded-2xl">
                  <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                    <h3 className="text-white font-bold text-lg">Create Data Source</h3>
                    <button
                      onClick={() => setIsCreateModalOpen(false)}
                      className="text-gray-400 hover:text-white transition-colors"
                    >
                      Close
                    </button>
                  </div>

                  <div className="p-6 space-y-4">
                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-gray-400 font-semibold">Data Source Name</span>
                      <input
                        type="text"
                        value={dataSourceName}
                        onChange={(event) => setDataSourceName(event.target.value)}
                        placeholder="Quarterly Sales Workbook"
                        className="mt-2 w-full px-4 py-3 bg-[#252542] border border-white/10 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-[#8243EA]/50"
                      />
                    </label>

                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-gray-400 font-semibold">Excel File</span>
                      <input
                        type="file"
                        accept=".xlsx,.xls,.xlsm"
                        onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                        className="mt-2 w-full px-4 py-2.5 bg-[#252542] border border-white/10 rounded-xl text-gray-300 file:mr-4 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:bg-[#8243EA]/25 file:text-[#C4B5FD] file:font-semibold"
                      />
                      {selectedFileSummary && <p className="text-xs text-gray-400 mt-2">{selectedFileSummary}</p>}
                    </label>

                    <div className="flex justify-end gap-3 pt-2">
                      <button
                        onClick={() => setIsCreateModalOpen(false)}
                        className="px-4 py-2 rounded-lg border border-white/15 text-gray-300 hover:text-white hover:bg-white/5"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleUpload}
                        disabled={isUploadLoading}
                        className="px-5 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg font-semibold hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isUploadLoading ? 'Uploading...' : 'Create'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        );

      case 'conversations':
        return (
          <div className="h-full m-4 flex flex-col gap-4">
            {/* Usage Summary Card */}
            {usageSummary && (
              <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl border border-white/10 p-6">
                <h3 className="text-[#A78BFA] font-semibold mb-4">Usage Summary (Last {usageSummary.period_days} Days)</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div className="bg-[#252542] rounded-xl p-4">
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Total Calls</p>
                    <p className="text-2xl font-bold text-white mt-1">{usageSummary.total_calls.toLocaleString()}</p>
                  </div>
                  <div className="bg-[#252542] rounded-xl p-4">
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Input Tokens</p>
                    <p className="text-2xl font-bold text-white mt-1">{usageSummary.total_input_tokens.toLocaleString()}</p>
                  </div>
                  <div className="bg-[#252542] rounded-xl p-4">
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Output Tokens</p>
                    <p className="text-2xl font-bold text-white mt-1">{usageSummary.total_output_tokens.toLocaleString()}</p>
                  </div>
                  <div className="bg-[#252542] rounded-xl p-4">
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Total Cost</p>
                    <p className="text-2xl font-bold text-emerald-400 mt-1">${usageSummary.total_cost_usd.toFixed(4)}</p>
                  </div>
                </div>
              </div>
            )}

            <div className="bg-[#1a1a2e]/60 backdrop-blur-sm rounded-2xl flex-1 overflow-hidden flex flex-col border border-white/10">
              {/* Header */}
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="grid grid-cols-2 gap-1">
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                    <div className="w-2 h-2 border border-[#8243EA] rounded-sm" />
                  </div>
                  <span className="text-[#A78BFA] font-semibold">Conversation History ({conversationsTotal})</span>
                </div>
                <button
                  onClick={fetchConversations}
                  className="px-4 py-2 bg-white/5 border border-white/10 text-white rounded-lg text-sm font-semibold hover:bg-white/10 transition-colors"
                >
                  Refresh
                </button>
              </div>

              {isConversationsLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="animate-spin h-7 w-7 border-4 border-[#8243EA] border-t-transparent rounded-full" />
                </div>
              ) : conversations.length === 0 ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-20 h-20 bg-[#252542] border border-white/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                      <svg className="w-10 h-10 text-[#8243EA]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-white mb-2">No conversations yet</h3>
                    <p className="text-gray-400 text-sm mb-6">Start a chat to see your history here</p>
                    <button
                      onClick={() => navigate('/dashboard/ask-ai')}
                      className="px-6 py-2 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg hover:opacity-90 transition-opacity"
                    >
                      Start Chat
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex-1 overflow-auto p-4">
                  <div className="space-y-3">
                    {conversations.map((conv) => {
                      const dataSource = dataSources.find((ds) => ds.id === conv.data_source_id);
                      return (
                        <div
                          key={conv.id}
                          className="bg-[#252542] border border-white/10 rounded-xl p-4 hover:border-[#8243EA]/50 transition-colors"
                        >
                          <div className="flex items-start justify-between">
                            <div className="flex-1 min-w-0">
                              <h4 className="text-white font-semibold truncate">{conv.title}</h4>
                              <p className="text-gray-400 text-sm mt-1">
                                {dataSource?.name || 'Unknown data source'}
                              </p>
                              <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                                <span>{conv.message_count} messages</span>
                                <span>${conv.total_cost_usd.toFixed(4)}</span>
                                <span>
                                  {conv.last_message_at
                                    ? new Date(conv.last_message_at).toLocaleString()
                                    : new Date(conv.created_at).toLocaleString()}
                                </span>
                              </div>
                            </div>
                            <div className="flex items-center gap-2 ml-4">
                              <button
                                onClick={() => handleLoadConversation(conv.id)}
                                className="px-3 py-1.5 bg-gradient-to-r from-[#8243EA] to-[#6366F1] text-white rounded-lg text-sm hover:opacity-90 transition-opacity"
                              >
                                Continue
                              </button>
                              <button
                                onClick={() => handleDeleteConversation(conv.id)}
                                className="px-3 py-1.5 bg-red-500/10 border border-red-400/30 text-red-300 rounded-lg text-sm hover:bg-red-500/20 transition-colors"
                              >
                                Delete
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <Layout activeNavItem={section} onNavItemClick={handleNavClick}>
      {renderContent()}
    </Layout>
  );
}
