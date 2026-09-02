export type User = { id: string; organization_id: string; email: string; display_name: string; role: string };
export type AuthConfig = { authentication_required: boolean; self_registration_enabled: boolean; visual_analysis_enabled: boolean; departments: string[] };
export type DocumentIngestionStatus =
  | "registered"
  | "parsing"
  | "parsed_pending_index"
  | "failed"
  | "index_queued"
  | "indexing"
  | "indexed"
  | "index_failed";
export type DocumentVersion = {
  id: string; title: string; document_type: string; system: string; vendor?: string | null; version: string;
  status: string; ingestion_status: DocumentIngestionStatus; ingestion_error?: string | null; chunk_count: number; page_count?: number | null;
  archived_at?: string | null; archived_by_user_id?: string | null; archived_reason?: string | null;
};
export type RequirementBaseline = { id: string; name: string; system: string; description?: string | null };
export type Requirement = {
  id: string; requirement_code: string; requirement_text: string; source_row?: string | null;
  requirement_system?: string | null; rationale_impact?: string | null; is_critical: boolean;
  priority?: string | null; category?: string | null; source_section?: string | null;
};
export type ReviewPackage = { id: string; owner_user_id: string; organization_id: string; name: string; system: string; requirement_count: number; design_document_version_ids: string[] };
export type AnalysisStrategy = "original" | "decomposed";
export type AnalysisRunSummary = { id: string; status: string; strategy: AnalysisStrategy; strategy_version: string };
export type Finding = {
  id: string; requirement_code: string; requirement_text: string; design_status: string; rationale: string;
  gap?: string | null; suggested_reviewer_action?: string | null;
  evidence: { chunk_id: string; document_title: string; version: string; page?: number | null; section?: string | null; excerpt: string }[];
  audit_points: AuditPoint[];
};
export type AuditPoint = {
  point_id: string; source_excerpt: string; review_point: string; design_status: string;
  status_definition: string; rationale: string;
  evidence: { chunk_id: string; document_title: string; version: string; page?: number | null; section?: string | null; excerpt: string }[];
};
export type MatrixRow = {
  requirement_code: string; requirement_text: string; rationale_impact?: string | null; is_critical: boolean;
  priority?: string | null; category?: string | null; analysis_status: string; technical_error?: string | null;
  design_status?: string | null; status_definition?: string | null; rationale?: string | null;
  gap?: string | null; suggested_reviewer_action?: string | null;
  evidence: { chunk_id: string; document_title: string; version: string; page?: number | null; section?: string | null; excerpt: string }[];
  audit_points: AuditPoint[];
};
export type AnalysisProgress = {
  id: string; status: string; strategy: AnalysisStrategy; strategy_version: string; error_message?: string | null; total_items: number; queued_items: number;
  running_items: number; completed_items: number; failed_items: number;
  items: { id: string; requirement_code: string; status: string; attempt_count: number; error_message?: string | null }[];
};
export type ChatAnswer = {
  answer: string; retrieval_query: string; limitations?: string | null;
  citations: { chunk_id: string; document_version_id: string; document_title: string; version: string; page?: number | null; section?: string | null; excerpt: string }[];
};
export type DocumentChunk = { id: string; chunk_index: number; page: number; section?: string | null; element_type: string; source_metadata?: Record<string, unknown> | null; content: string };
export type DocumentChunkContext = { document_version_id: string; requested_chunk_id: string; chunks: DocumentChunk[] };
