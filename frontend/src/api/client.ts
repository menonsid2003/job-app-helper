const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface ScoreOut {
  score: number;
  reasoning: string;
  matched_keywords: string[];
  missing_requirements: string[];
  role_category: string;
  red_flags: string[];
  work_authorization: {
    citizenship_required: boolean;
    security_clearance_required: boolean;
    sponsorship_mentioned: "yes" | "no" | "not_mentioned";
    hard_exclude: boolean;
  };
  scored_at: string;
  model_used: string;
}

export type JobStatus =
  | "discovered"
  | "scored"
  | "excluded"
  | "pursue"
  | "skip"
  | "snoozed"
  | "tailored"
  | "applied"
  | "interview"
  | "rejected"
  | "offer";

export const TRACKING_STATUS_OPTIONS: JobStatus[] = [
  "pursue",
  "tailored",
  "applied",
  "interview",
  "rejected",
  "offer",
];

// Human-facing labels — the underlying values above are the API/DB's
// vocabulary and stay as-is; this is purely a display-layer relabeling
// (e.g. "pursue" reads as "Track" everywhere in the UI).
export const STATUS_LABELS: Record<JobStatus, string> = {
  discovered: "Discovered",
  scored: "Scored",
  excluded: "Excluded",
  pursue: "Track",
  skip: "Exclude",
  snoozed: "Snoozed",
  tailored: "Tailored",
  applied: "Applied",
  interview: "Interview",
  rejected: "Rejected",
  offer: "Offer",
};

export interface JobOut {
  id: number;
  source: string;
  source_url: string;
  canonical_url: string | null;
  title: string;
  company: string;
  location: string;
  is_remote: boolean | null;
  salary_text: string | null;
  posted_date: string | null;
  first_seen: string;
  status: JobStatus;
  notes: string;
  last_updated: string;
  latest_score: ScoreOut | null;
}

export interface JobDetailOut extends JobOut {
  description: string;
}

export type PipelineRunStatus = "idle" | "running" | "done" | "error" | "stopped";

export interface PipelineStatus {
  status: PipelineRunStatus;
  started_at: string | null;
  finished_at: string | null;
  current_step: string;
  discovered: number;
  deduped_skipped: number;
  scored: number;
  excluded: number;
  skipped_low_relevance: number;
  logs: string[];
  error: string | null;
  stop_requested: boolean;
}

export interface FullPipelineStatus {
  status: PipelineRunStatus;
  started_at: string | null;
  finished_at: string | null;
  current_step: string;
  logs: string[];
  error: string | null;
  stop_requested: boolean;
  phase: "" | "discover_score" | "promote" | "auto_apply";
  phase_started_at: string | null;
  discovered: number;
  deduped_skipped: number;
  scored: number;
  excluded: number;
  skipped_low_relevance: number;
  promoted_count: number;
  score_threshold: number;
  submitted_count: number;
  failed_count: number;
  unsupported_count: number;
  cost_usd_this_run: number;
}

export interface WorkAuthCriteria {
  exclude_citizenship_required: boolean;
  exclude_security_clearance_required: boolean;
  exclude_if_sponsorship_explicitly_refused: boolean;
  hard_exclude_prefilter_keywords: string[];
}

export interface ScoringWeights {
  title_match: number;
  skills_overlap: number;
  location_fit: number;
  seniority_fit: number;
  salary_fit: number;
}

export interface JobSpyCriteria {
  enabled: boolean;
  sites: string[];
  results_wanted: number;
  hours_old: number;
  country_indeed: string;
}

export interface ApplicantProfile {
  full_name: string;
  email: string;
  phone: string;
  linkedin_url: string;
  requires_visa_sponsorship: boolean | null;
  city: string;
  target_role: string;
  years_of_experience: number | null;
  salary_expectation: number | null;
  work_authorization_note: string;
  signup_email: string;
  signup_password: string;
}

export interface GoogleSheetsCriteria {
  enabled: boolean;
  spreadsheet_url: string;
  sheet_name: string;
}

export interface CriteriaConfig {
  role_categories: string[];
  target_roles: string[];
  locations: string[];
  country_only: string[];
  seniority: string[];
  salary_min: number | null;
  must_have_keywords: string[];
  nice_to_have_keywords: string[];
  exclude_keywords: string[];
  exclude_companies: string[];
  prefer_full_time: boolean;
  work_authorization: WorkAuthCriteria;
  scoring_weights: ScoringWeights;
  auto_apply_enabled: boolean;
  gpu_schedule_window: string | null;
  auto_schedule_enabled: boolean;
  company_board_connectors_enabled: boolean;
  target_companies: Record<string, string[]>;
  exclude_location_keywords: string[];
  canonical_link_score_threshold: number;
  applicant_profile: ApplicantProfile;
  jobspy: JobSpyCriteria;
  google_sheets: GoogleSheetsCriteria;
}

export type ApplicationStatus = "submitted" | "failed" | "unsupported";

export interface ApplicationOut {
  id: number;
  job_id: number;
  job_title: string;
  job_company: string;
  resume_id: number | null;
  status: ApplicationStatus;
  method: "auto" | "manual" | "agent";
  notes: string;
  screenshot_path: string | null;
  submitted_at: string | null;
  created_at: string;
}

export interface AutoApplyStatus {
  status: PipelineRunStatus;
  started_at: string | null;
  finished_at: string | null;
  current_step: string;
  logs: string[];
  error: string | null;
  stop_requested: boolean;
  submitted_count: number;
  failed_count: number;
  unsupported_count: number;
}

export interface AgentApplyStatus extends AutoApplyStatus {
  // Token-usage-priced estimate from the claude CLI itself — populated the
  // same way under a Pro/Max subscription as under a metered API key, since
  // it reflects usage rather than what was actually billed. See the Agent
  // Apply tab's hint text.
  total_cost_usd: number;
}

export interface ScoringUsage {
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_creation_tokens: number;
  total_cost_usd: number;
  call_count: number;
  last_updated: string | null;
}

export interface AgentApplySetupProfileResult {
  opened: boolean;
  suggested_logins: string[];
  note: string;
}

export interface AgentApplyUsage {
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_creation_tokens: number;
  total_cost_usd: number;
  job_count: number;
  rate_limit_status: string | null;
  rate_limit_type: string | null;
  rate_limit_resets_at: string | null;
  last_updated: string | null;
}

export interface TailorAllStatus {
  status: PipelineRunStatus;
  started_at: string | null;
  finished_at: string | null;
  current_step: string;
  logs: string[];
  error: string | null;
  stop_requested: boolean;
  tailored_count: number;
  failed_count: number;
}

export interface LocationBackfillStatus {
  status: PipelineRunStatus;
  started_at: string | null;
  finished_at: string | null;
  current_step: string;
  logs: string[];
  error: string | null;
  stop_requested: boolean;
  updated_count: number;
  not_found_count: number;
  skipped_count: number;
}

export interface ResumeOut {
  id: number;
  job_id: number;
  version: number;
  diff_summary: string;
  created_at: string;
}

export interface DiffLine {
  type: "equal" | "added" | "removed";
  text: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export function fetchJobs(params: { status?: string; role_category?: string; source?: string } = {}) {
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined)) as Record<string, string>
  ).toString();
  return request<JobOut[]>(`/api/jobs${query ? `?${query}` : ""}`);
}

export function fetchExcludedJobs() {
  return request<JobOut[]>("/api/jobs/excluded");
}

export function fetchTrackingJobs() {
  return request<JobOut[]>("/api/jobs/tracking");
}

export function fetchJobDetail(id: number) {
  return request<JobDetailOut>(`/api/jobs/${id}`);
}

export function updateJob(
  id: number,
  update: { status?: JobStatus; notes?: string; company?: string; location?: string; is_remote?: boolean | null }
) {
  return request<JobDetailOut>(`/api/jobs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

export function runPipeline() {
  return request<PipelineStatus>("/api/pipeline/run", { method: "POST" });
}

export function stopPipeline() {
  return request<PipelineStatus>("/api/pipeline/stop", { method: "POST" });
}

export function rescoreAll(options: { force?: boolean; minScore?: number; maxScore?: number } = {}) {
  const params = new URLSearchParams();
  if (options.force) params.set("force", "true");
  if (options.minScore !== undefined) params.set("min_score", String(options.minScore));
  if (options.maxScore !== undefined) params.set("max_score", String(options.maxScore));
  const query = params.toString();
  return request<PipelineStatus>(`/api/pipeline/rescore${query ? `?${query}` : ""}`, { method: "POST" });
}

export function fetchPipelineStatus() {
  return request<PipelineStatus>("/api/pipeline/status");
}

export function runFullPipeline(scoreThreshold: number) {
  return request<FullPipelineStatus>(`/api/pipeline/full-run?score_threshold=${scoreThreshold}`, { method: "POST" });
}

export function stopFullPipeline() {
  return request<FullPipelineStatus>("/api/pipeline/full-run/stop", { method: "POST" });
}

export function fetchFullPipelineStatus() {
  return request<FullPipelineStatus>("/api/pipeline/full-run/status");
}

export function fetchCriteria() {
  return request<CriteriaConfig>("/api/criteria");
}

export function saveCriteria(criteria: CriteriaConfig) {
  return request<CriteriaConfig>("/api/criteria", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(criteria),
  });
}

export function fetchBaseResume() {
  return request<{ text: string }>("/api/resume/base");
}

export function saveBaseResume(text: string) {
  return request<{ text: string }>("/api/resume/base", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export function fetchExperienceBank() {
  return request<{ text: string }>("/api/resume/experience-bank");
}

export function saveExperienceBank(text: string) {
  return request<{ text: string }>("/api/resume/experience-bank", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export function tailorResume(jobId: number, correction?: string) {
  const query = correction ? `?${new URLSearchParams({ correction }).toString()}` : "";
  return request<ResumeOut>(`/api/jobs/${jobId}/resumes${query}`, { method: "POST" });
}

export function fetchResumesForJob(jobId: number) {
  return request<ResumeOut[]>(`/api/jobs/${jobId}/resumes`);
}

export function fetchResumeDiff(resumeId: number) {
  return request<DiffLine[]>(`/api/resumes/${resumeId}/diff`);
}

export function deleteResume(resumeId: number) {
  return fetch(`${API_BASE_URL}/api/resumes/${resumeId}`, { method: "DELETE" }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  });
}

export function resumePdfUrl(resumeId: number) {
  return `${API_BASE_URL}/api/resumes/${resumeId}/pdf`;
}

export function resumeTextUrl(resumeId: number) {
  return `${API_BASE_URL}/api/resumes/${resumeId}/text`;
}

export function fetchApplications() {
  return request<ApplicationOut[]>("/api/applications");
}

export function fetchAutoApplyStatus() {
  return request<AutoApplyStatus>("/api/auto-apply/status");
}

export function runAutoApply() {
  return request<AutoApplyStatus>("/api/auto-apply/run", { method: "POST" });
}

export function stopAutoApply() {
  return request<AutoApplyStatus>("/api/auto-apply/stop", { method: "POST" });
}

export function applicationScreenshotUrl(applicationId: number) {
  return `${API_BASE_URL}/api/applications/${applicationId}/screenshot`;
}

export function fetchAgentApplyStatus() {
  return request<AgentApplyStatus>("/api/agent-apply/status");
}

export function runAgentApply() {
  return request<AgentApplyStatus>("/api/agent-apply/run", { method: "POST" });
}

export function stopAgentApply() {
  return request<AgentApplyStatus>("/api/agent-apply/stop", { method: "POST" });
}

export function fetchAgentApplyUsage() {
  return request<AgentApplyUsage>("/api/agent-apply/usage");
}

export function fetchScoringUsage() {
  return request<ScoringUsage>("/api/llm-usage");
}

export function setupAgentApplyProfile() {
  return request<AgentApplySetupProfileResult>("/api/agent-apply/setup-profile", { method: "POST" });
}

export function fetchTailorAllStatus() {
  return request<TailorAllStatus>("/api/resumes/tailor-all/status");
}

export function runTailorAll() {
  return request<TailorAllStatus>("/api/resumes/tailor-all", { method: "POST" });
}

export function stopTailorAll() {
  return request<TailorAllStatus>("/api/resumes/tailor-all/stop", { method: "POST" });
}

export function fetchLocationBackfillStatus() {
  return request<LocationBackfillStatus>("/api/jobs/backfill-locations/status");
}

export function runLocationBackfill() {
  return request<LocationBackfillStatus>("/api/jobs/backfill-locations", { method: "POST" });
}

export function stopLocationBackfill() {
  return request<LocationBackfillStatus>("/api/jobs/backfill-locations/stop", { method: "POST" });
}
