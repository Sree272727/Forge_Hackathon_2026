import { API_BASE_URL } from './auth';

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

export interface DataSource {
  id: string;
  user_id: string;
  name: string;
  original_file_name: string;
  mime_type: string | null;
  file_extension: string;
  file_size_bytes: number;
  sheet_count: number;
  sheet_names: string[];
  file_checksum_sha256: string;
  meta_info: Record<string, unknown>;
  created_at: string;
}

export interface DataSourceListResponse {
  items: DataSource[];
  total: number;
}

export async function listDataSources(accessToken: string, skip = 0, limit = 50): Promise<DataSourceListResponse> {
  const response = await fetch(`${API_BASE_URL}/data-sources?skip=${skip}&limit=${limit}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.error?.message || 'Failed to fetch data sources');
  }

  return response.json();
}

export async function uploadDataSource(accessToken: string, name: string, file: File): Promise<DataSource> {
  const formData = new FormData();
  formData.append('name', name);
  formData.append('file', file);

  const response = await fetch(`${API_BASE_URL}/data-sources/upload`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.error?.message || 'Failed to upload file');
  }

  return response.json();
}

export async function getDataSource(accessToken: string, dataSourceId: string): Promise<DataSource> {
  const response = await fetch(`${API_BASE_URL}/data-sources/${dataSourceId}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.error?.message || 'Failed to fetch data source');
  }

  return response.json();
}

// Analysis types
export interface SheetInfo {
  name: string;
  row_count: number;
  column_count: number;
  formula_count: number;
  error_count: number;
  inferred_purpose: string | null;
  data_patterns: string[];
}

export interface WorkbookAnalysisSummary {
  total_rows: number | null;
  total_columns: number | null;
  has_formulas: boolean;
  has_errors: boolean;
  formula_categories: string[];
  error_types: string[];
  column_purposes: Record<string, number>;
}

export interface WorkbookAnalysis {
  file_name: string;
  sheet_count: number;
  total_formulas: number;
  total_errors: number;
  overall_purpose: string | null;
  sheets: SheetInfo[];
  summary: WorkbookAnalysisSummary | null;
}

export async function getDataSourceAnalysis(accessToken: string, dataSourceId: string): Promise<WorkbookAnalysis | null> {
  const response = await fetch(`${API_BASE_URL}/data-sources/${dataSourceId}/analysis`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error?.error?.message || 'Failed to fetch analysis');
  }

  const data = await response.json();
  return data || null;
}
