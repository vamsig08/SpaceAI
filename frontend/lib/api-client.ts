/**
 * Typed API client for SpaceAI backend.
 * All endpoints return standardized response envelopes.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

interface PaginationMeta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

interface SingleResponse<T> {
  data: T;
}

interface PaginatedResponse<T> {
  data: T[];
  meta: PaginationMeta;
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...options?.headers },
      ...options,
    });
  } catch (e) {
    throw new Error("Cannot connect to SpaceAI server. Is the backend running?");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: { message: res.statusText } }));
    throw new Error(error.error?.message || `API error: ${res.status}`);
  }

  return res.json();
}

// ─── Analytics ─────────────────────────────────────────────────────────────────

export async function getOverview() {
  return fetchAPI<SingleResponse<StorageOverview>>("/analytics/overview");
}

export async function getCategories(scanId?: string) {
  const params = scanId ? `?scan_id=${scanId}` : "";
  return fetchAPI<SingleResponse<CategoryBreakdown>>(`/analytics/categories${params}`);
}

export async function getLargestFiles(scanId?: string, limit = 100) {
  const params = new URLSearchParams();
  if (scanId) params.set("scan_id", scanId);
  params.set("limit", String(limit));
  return fetchAPI<SingleResponse<LargestFilesResponse>>(`/analytics/largest-files?${params}`);
}

export async function getLargestFolders(scanId?: string, limit = 50) {
  const params = new URLSearchParams();
  if (scanId) params.set("scan_id", scanId);
  params.set("limit", String(limit));
  return fetchAPI<SingleResponse<LargestFoldersResponse>>(`/analytics/largest-folders?${params}`);
}

export async function getGrowth(period = 30) {
  return fetchAPI<SingleResponse<GrowthHistory>>(`/analytics/growth?period=${period}`);
}

// ─── Duplicates ────────────────────────────────────────────────────────────────

export async function getDuplicateSummary(scanId: string) {
  return fetchAPI<SingleResponse<DuplicateSummary>>(`/duplicates/summary?scan_id=${scanId}`);
}

export async function getDuplicateGroups(scanId: string, page = 1, pageSize = 50) {
  return fetchAPI<PaginatedResponse<DuplicateGroup>>(
    `/duplicates?scan_id=${scanId}&page=${page}&page_size=${pageSize}`
  );
}

export async function getDuplicateGroupDetail(groupId: string) {
  return fetchAPI<SingleResponse<DuplicateGroupDetail>>(`/duplicates/${groupId}`);
}

export async function getDuplicateCleanupPaths(scanId: string) {
  return fetchAPI<SingleResponse<{ paths: string[]; total_bytes: number; file_count: number }>>(`/duplicates/cleanup-paths?scan_id=${scanId}`);
}

// ─── Stale Files ───────────────────────────────────────────────────────────────

export async function getStaleSummary(scanId: string) {
  return fetchAPI<SingleResponse<StaleSummary>>(`/stale/summary?scan_id=${scanId}`);
}

export async function getStaleFiles(scanId: string, params?: Record<string, string>) {
  const qs = new URLSearchParams({ scan_id: scanId, ...params });
  return fetchAPI<PaginatedResponse<StaleFile>>(`/stale/files?${qs}`);
}

export async function getDevArtifacts(scanId: string) {
  return fetchAPI<SingleResponse<DevArtifactSummary>>(`/stale/dev-artifacts?scan_id=${scanId}`);
}

// ─── Workspaces ────────────────────────────────────────────────────────────────

export async function getWorkspaceSummary(scanId: string) {
  return fetchAPI<SingleResponse<WorkspaceSummary>>(`/workspaces/summary?scan_id=${scanId}`);
}

export async function getWorkspaces(scanId: string, params?: Record<string, string>) {
  const qs = new URLSearchParams({ scan_id: scanId, ...params });
  return fetchAPI<PaginatedResponse<WorkspaceEntry>>(`/workspaces?${qs}`);
}

export async function getAbandonedProjects(scanId: string) {
  return fetchAPI<SingleResponse<AbandonedProjectsResponse>>(`/workspaces/abandoned?scan_id=${scanId}`);
}

// ─── Recommendations ───────────────────────────────────────────────────────────

export async function getRecommendations(scanId: string, params?: Record<string, string>) {
  const qs = new URLSearchParams({ scan_id: scanId, ...params });
  return fetchAPI<PaginatedResponse<Recommendation>>(`/recommendations?${qs}`);
}

export async function updateRecommendation(recId: string, status: string, reason?: string) {
  return fetchAPI(`/recommendations/${recId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, dismissed_reason: reason }),
  });
}

// ─── Predictions ───────────────────────────────────────────────────────────────

export async function getExhaustion() {
  return fetchAPI<SingleResponse<ExhaustionResponse>>("/predictions/exhaustion");
}

export async function getGrowthRate() {
  return fetchAPI<SingleResponse<GrowthRateResponse>>("/predictions/growth-rate");
}

export async function generateForecast(diskCapacity?: number) {
  return fetchAPI<SingleResponse<ForecastResponse>>("/predictions/forecast", {
    method: "POST",
    body: JSON.stringify({ disk_capacity: diskCapacity }),
  });
}

// ─── Cleanup ───────────────────────────────────────────────────────────────────

export async function getCleanupActions(params?: Record<string, string>) {
  const qs = params ? `?${new URLSearchParams(params)}` : "";
  return fetchAPI<PaginatedResponse<CleanupAction>>(`/cleanup/actions${qs}`);
}

export async function proposeCleanup(body: ProposeCleanupRequest) {
  return fetchAPI<SingleResponse<CleanupAction>>("/cleanup/propose", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function approveCleanup(actionId: string) {
  return fetchAPI(`/cleanup/actions/${actionId}/approve`, { method: "POST" });
}

export async function executeCleanup(actionId: string) {
  return fetchAPI(`/cleanup/actions/${actionId}/execute`, { method: "POST" });
}

export async function rollbackCleanup(actionId: string) {
  return fetchAPI(`/cleanup/actions/${actionId}/rollback`, { method: "POST" });
}

export async function getAuditLog(params?: Record<string, string>) {
  const qs = params ? `?${new URLSearchParams(params)}` : "";
  return fetchAPI<PaginatedResponse<AuditLogEntry>>(`/cleanup/audit-log${qs}`);
}

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface StorageOverview {
  total_storage: number;
  used_storage: number;
  free_storage: number;
  file_count: number;
  dir_count: number;
  duplicate_waste: number;
  stale_files_size: number;
  recovery_opportunities: number;
  last_scan: string | null;
  snapshot_date: string | null;
}

export interface CategoryBreakdown {
  breakdown: Record<string, number>;
  total_bytes: number;
  file_count: number;
}

export interface LargestFilesResponse {
  files: { id: string; path: string; filename: string; size_bytes: number; category: string | null; extension: string | null }[];
  scan_id: string | null;
  total_count: number;
}

export interface LargestFoldersResponse {
  folders: { id: string; path: string; name: string; total_size_bytes: number; file_count: number }[];
  scan_id: string | null;
  total_count: number;
}

export interface GrowthHistory {
  data_points: { date: string; total_size_bytes: number; file_count: number }[];
  period_days: number;
  data_point_count: number;
  daily_growth_bytes: number;
}

export interface DuplicateSummary {
  total_groups: number;
  total_duplicate_files: number;
  total_wasted_bytes: number;
  top_extensions: string[];
}

export interface DuplicateGroup {
  id: string;
  sha256_hash: string;
  file_size_bytes: number;
  member_count: number;
  wasted_bytes: number;
  status: string;
  created_at: string;
}

export interface DuplicateGroupDetail extends DuplicateGroup {
  members: { id: string; file_id: string; path: string; is_keeper: boolean }[];
}

export interface StaleSummary {
  scan_id: string;
  classification: Record<string, { count: number; bytes: number }>;
  recoverable_bytes: number;
  risk_breakdown: Record<string, { count: number; bytes: number }>;
  total_stale_files: number;
}

export interface StaleFile {
  id: string;
  path: string;
  filename: string;
  size_bytes: number;
  category: string | null;
  stale_score: number | null;
  risk_level: string | null;
  accessed_at: string | null;
}

export interface DevArtifactSummary {
  scan_id: string;
  artifacts: Record<string, { count: number; bytes: number }>;
  total_recoverable_bytes: number;
  total_artifact_files: number;
}

export interface WorkspaceSummary {
  scan_id: string;
  total_workspaces: number;
  total_recoverable_bytes: number;
  safe_recoverable_bytes: number;
  inactive_workspaces: number;
  by_type: Record<string, { count: number; total_bytes: number; recoverable_bytes: number; safe_recoverable_bytes: number }>;
}

export interface WorkspaceEntry {
  id: string;
  path: string;
  name: string;
  workspace_type: string;
  total_size_bytes: number;
  recoverable_bytes: number;
  safe_recoverable_bytes: number;
  is_active: boolean;
  days_inactive: number | null;
  risk_level: string;
}

export interface AbandonedProjectsResponse {
  scan_id: string;
  abandoned_count: number;
  total_recoverable_bytes: number;
  projects: { path: string; name: string; workspace_type: string; total_size_bytes: number; recoverable_bytes: number; days_inactive: number | null }[];
}

export interface Recommendation {
  id: string;
  category: string;
  priority: string;
  title: string;
  description: string;
  explanation: string | null;
  recoverable_bytes: number;
  confidence: number;
  affected_count: number;
  status: string;
  created_at: string;
}

export interface ExhaustionResponse {
  exhaustion_date: string | null;
  days_until_full: number | null;
  daily_growth_bytes: number;
  weekly_growth_bytes: number;
  confidence: number;
  model_type: string | null;
}

export interface GrowthRateResponse {
  daily_growth_bytes: number;
  weekly_growth_bytes: number;
  monthly_growth_bytes: number;
  trend: string;
  confidence: number;
}

export interface ForecastResponse {
  model_type: string;
  daily_growth_bytes: number;
  weekly_growth_bytes: number;
  predicted_total_30d: number;
  predicted_total_90d: number;
  exhaustion_date: string | null;
  days_until_full: number | null;
  confidence: number;
  trend: string;
}

export interface CleanupAction {
  id: string;
  action_type: string;
  target_count: number;
  total_bytes: number;
  status: string;
  bytes_recovered: number;
  created_at: string;
  executed_at: string | null;
  completed_at: string | null;
}

export interface ProposeCleanupRequest {
  recommendation_id?: string;
  action_type: string;
  target_paths: string[];
  total_bytes: number;
}

export interface AuditLogEntry {
  id: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  description: string | null;
  bytes_affected: number;
  severity: string;
  created_at: string;
}
