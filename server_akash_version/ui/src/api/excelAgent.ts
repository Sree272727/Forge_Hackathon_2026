import { API_BASE_URL } from './auth';
import { ApiError } from './dataSources';

// Types for Excel Agent API

export interface ProcessDataSourceResponse {
  schema_id: string;
  data_source_id: string;
  processing_status: string;
  is_ready_for_queries: boolean;
  workbook_purpose: string | null;
  total_sections: number;
  total_merged_regions: number;
  detected_colors: string[];
  queryable_questions: string[];
  data_quality_notes: string[];
  processing_error: string | null;
  processed_at: string | null;
}

export interface ExcelSchemaResponse {
  id: string;
  data_source_id: string;
  processing_status: string;
  is_ready_for_queries: boolean;
  workbook_purpose: string | null;
  manifest: Record<string, unknown>;
  semantic_schema: Record<string, unknown>;
  detected_colors: string[];
  total_sections: number;
  total_merged_regions: number;
  queryable_questions: string[];
  data_quality_notes: string[];
  processing_error: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

export interface SchemaInfoResponse {
  data_source_id: string;
  processing_status: string;
  is_ready_for_queries: boolean;
  workbook_purpose: string | null;
  sheet_count: number;
  queryable_questions_count: number;
  has_data_quality_notes: boolean;
}

export interface AskQuestionResponse {
  success: boolean;
  answer: unknown;
  code_used: string | null;
  iterations: number | null;
  error: string | null;
  execution_time_ms: number;
  query_id: string;
  conversation_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
}

export interface SuggestedQuestionsResponse {
  questions: string[];
  data_source_id: string;
}

export interface QueryHistoryItem {
  id: string;
  question: string;
  answer: unknown;
  code_used: string | null;
  success: boolean;
  error_message: string | null;
  execution_time_ms: number | null;
  iterations_used: number;
  created_at: string;
}

export interface QueryHistoryResponse {
  items: QueryHistoryItem[];
  total: number;
}

// Conversation types
export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  code_used: string | null;
  execution_time_ms: number | null;
  is_error: boolean;
  error_message: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface Conversation {
  id: string;
  data_source_id: string;
  title: string;
  is_active: boolean;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  messages: ConversationMessage[];
}

export interface ConversationListItem {
  id: string;
  data_source_id: string;
  title: string;
  total_cost_usd: number;
  message_count: number;
  created_at: string;
  last_message_at: string | null;
}

export interface ConversationListResponse {
  items: ConversationListItem[];
  total: number;
}

export interface UsageSummaryResponse {
  period_days: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  total_calls: number;
  by_call_type: Record<string, { cost: number; count: number }>;
}

// API Functions

export async function processDataSource(
  accessToken: string,
  dataSourceId: string,
  forceReprocess = false
): Promise<ProcessDataSourceResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/process`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ force_reprocess: forceReprocess }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to process data source');
  }

  return response.json();
}

export async function getExcelSchema(
  accessToken: string,
  dataSourceId: string
): Promise<ExcelSchemaResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/schema`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get schema');
  }

  return response.json();
}

export async function getSchemaInfo(
  accessToken: string,
  dataSourceId: string
): Promise<SchemaInfoResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/schema/info`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get schema info');
  }

  return response.json();
}

export async function askQuestion(
  accessToken: string,
  dataSourceId: string,
  question: string,
  conversationId?: string | null
): Promise<AskQuestionResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/ask`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        question,
        conversation_id: conversationId || null,
      }),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to ask question');
  }

  return response.json();
}

export async function getSuggestedQuestions(
  accessToken: string,
  dataSourceId: string
): Promise<SuggestedQuestionsResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/questions/suggested`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get suggested questions');
  }

  return response.json();
}

export async function getQueryHistory(
  accessToken: string,
  dataSourceId: string,
  limit = 50
): Promise<QueryHistoryResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/data-sources/${dataSourceId}/queries/history?limit=${limit}`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get query history');
  }

  return response.json();
}

// Conversation API functions

export async function listConversations(
  accessToken: string,
  dataSourceId?: string | null,
  skip = 0,
  limit = 50
): Promise<ConversationListResponse> {
  let url = `${API_BASE_URL}/excel-agent/conversations?skip=${skip}&limit=${limit}`;
  if (dataSourceId) {
    url += `&data_source_id=${dataSourceId}`;
  }

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to list conversations');
  }

  return response.json();
}

export async function getConversation(
  accessToken: string,
  conversationId: string
): Promise<Conversation> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/conversations/${conversationId}`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get conversation');
  }

  return response.json();
}

export async function deleteConversation(
  accessToken: string,
  conversationId: string
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/conversations/${conversationId}`,
    {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to delete conversation');
  }
}

export async function getUsageSummary(
  accessToken: string,
  days = 30
): Promise<UsageSummaryResponse> {
  const response = await fetch(
    `${API_BASE_URL}/excel-agent/usage/summary?days=${days}`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.detail || 'Failed to get usage summary');
  }

  return response.json();
}
