"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, json, streamSse } from "../lib/api";
import type { AnalysisProgress, AuthConfig, ChatAnswer, DocumentChunkContext, DocumentVersion, Finding, MatrixRow, Requirement, RequirementBaseline, ReviewPackage, User } from "../lib/types";

type View = "assistant" | "review" | "knowledge" | "help";
type Message = { role: "user" | "assistant"; content: string; citations?: ChatAnswer["citations"]; limitations?: string | null };
type Citation = ChatAnswer["citations"][number];
const CHAT_HISTORY_LIMIT = 40; // 20 question-and-answer rounds.

function isSavedMessage(value: unknown): value is Message {
  if (typeof value !== "object" || value === null) return false;
  const message = value as { role?: unknown; content?: unknown };
  return (message.role === "user" || message.role === "assistant") && typeof message.content === "string";
}

function readChatHistory(key: string): Message[] {
  try {
    const stored = window.localStorage.getItem(key);
    if (!stored) return [];
    const parsed: unknown = JSON.parse(stored);
    return Array.isArray(parsed) ? parsed.filter(isSavedMessage).slice(-CHAT_HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

const SYSTEM_OPTIONS = [
  { value: "amr_agv", label: "Autonomous Mobile Robots / Automated Guided Vehicles (AMR/AGV)" },
  { value: "fleet_manager", label: "Fleet Manager" },
  { value: "wcs", label: "Warehouse Control System (WCS)" },
] as const;

const DESIGN_DOCUMENT_TYPES = [
  { value: "FS", label: "Functional Specification (FS)" },
  { value: "SDS", label: "Software Design Specification (SDS)" },
  { value: "HDS", label: "Hardware Design Specification (HDS)" },
] as const;

function systemLabel(value: string) {
  return SYSTEM_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

function documentTypeLabel(value: string) {
  return DESIGN_DOCUMENT_TYPES.find((option) => option.value === value)?.label ?? value;
}

function SystemSelect({ name, defaultValue = "fleet_manager" }: { name: string; defaultValue?: string }) {
  return <select name={name} defaultValue={defaultValue}>{SYSTEM_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>;
}

function statusTone(status: string) {
  if (["completed", "covered"].includes(status)) return "good";
  if (["failed", "not_evidenced", "conflicting_evidence"].includes(status)) return "bad";
  if (["running", "retrying", "partially_covered", "review_required"].includes(status)) return "warn";
  return "neutral";
}

function Tag({ value }: { value: string }) {
  return <span className={`tag ${statusTone(value)}`}>{value.replaceAll("_", " ")}</span>;
}

function Notice({ children, kind = "neutral" }: { children: React.ReactNode; kind?: "neutral" | "error" | "success" }) {
  return <div className={`notice ${kind}`}>{children}</div>;
}

function AuthGate({ onAuthenticated }: { onAuthenticated: (token: string, user: User) => void }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api<AuthConfig>("/auth/config").then(setConfig).catch((reason) => setError(reason.message)); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(null);
    const form = new FormData(event.currentTarget);
    try {
      if (mode === "login") {
        const result = await api<{ access_token: string; user: User }>("/auth/login", json({ email: form.get("email"), password: form.get("password") }));
        onAuthenticated(result.access_token, result.user);
      } else {
        const password = String(form.get("password") ?? "");
        if (password !== String(form.get("confirmPassword") ?? "")) throw new Error("Passwords do not match.");
        const user = await api<User>("/auth/register", json({ display_name: form.get("displayName"), email: form.get("email"), password, department: form.get("department") }));
        const result = await api<{ access_token: string; user: User }>("/auth/login", json({ email: user.email, password }));
        onAuthenticated(result.access_token, result.user);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Authentication failed."); }
    finally { setBusy(false); }
  }

  return <main className="auth-shell">
    <section className="auth-mark"><p className="eyebrow">CONTROLLED EVIDENCE SYSTEM</p><h1>Design<br /><em>Review</em><br />Console</h1><p>Version-scoped technical evidence for warehouse automation engineering.</p><div className="auth-grid" aria-hidden="true" /></section>
    <section className="auth-card"><p className="eyebrow">SECURE WORKSPACE</p><h2>{mode === "login" ? "Enter the review room" : "Create local access"}</h2><p className="muted">Supplier design evidence stays constrained to the review package you select.</p>
      {error && <Notice kind="error">{error}</Notice>}
      <form onSubmit={submit} className="stack">
        {mode === "register" && <label>Display name<input name="displayName" required minLength={2} autoComplete="name" /></label>}
        {mode === "register" && <label>Department<select name="department" required defaultValue=""><option value="" disabled>Select department</option>{config?.departments.map((department) => <option key={department} value={department}>{department}</option>)}</select></label>}
        <label>Work email<input name="email" type="email" required autoComplete="email" /></label>
        <label>Password<input name="password" type="password" required minLength={12} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
        {mode === "register" && <label>Confirm password<input name="confirmPassword" type="password" required minLength={12} autoComplete="new-password" /></label>}
        <button className="primary" disabled={busy}>{busy ? "Checking access…" : mode === "login" ? "Sign in" : "Create account"}</button>
      </form>
      {config?.self_registration_enabled && <button className="text-button" onClick={() => setMode(mode === "login" ? "register" : "login")}>{mode === "login" ? "Need a local account?" : "Back to sign in"}</button>}
    </section>
  </main>;
}

function Assistant({ token, userId, reviews, activeReviewId }: { token: string; userId: string; reviews: ReviewPackage[]; activeReviewId: string | null }) {
  const historyKey = activeReviewId ? `industrial-rag-chat-history:${userId}:${activeReviewId}` : null;
  const [historyByKey, setHistoryByKey] = useState<Record<string, Message[]>>({});
  const [question, setQuestion] = useState(""); const [busy, setBusy] = useState(false); const [streamPhase, setStreamPhase] = useState<"retrieving" | "answering" | null>(null); const [error, setError] = useState<string | null>(null);
  const [sourceReader, setSourceReader] = useState<{ citation: Citation; context: DocumentChunkContext | null; error: string | null; view: "passage" | "pdf"; pdfUrl: string | null; pdfError: string | null; pdfLoading: boolean } | null>(null);
  const abortController = useRef<AbortController | null>(null);
  const pdfObjectUrl = useRef<string | null>(null);
  const activeReview = reviews.find((review) => review.id === activeReviewId);
  const messages = historyKey ? (historyByKey[historyKey] ?? []) : [];
  useEffect(() => {
    if (!historyKey || historyKey in historyByKey) return;
    setHistoryByKey((current) => ({ ...current, [historyKey]: readChatHistory(historyKey) }));
  }, [historyByKey, historyKey]);
  function updateMessages(update: (current: Message[]) => Message[]) {
    if (!historyKey) return;
    setHistoryByKey((current) => {
      const next = update(current[historyKey] ?? []).slice(-CHAT_HISTORY_LIMIT);
      window.localStorage.setItem(historyKey, JSON.stringify(next));
      return { ...current, [historyKey]: next };
    });
  }
  async function send(event: FormEvent) {
    event.preventDefault(); if (busy || !question.trim() || !activeReviewId) return;
    const input = question.trim(); const userMessage: Message = { role: "user", content: input }; const conversationHistory = [...messages, userMessage].slice(-12).map(({ role, content }) => ({ role, content })); setQuestion(""); updateMessages((current) => [...current, userMessage, { role: "assistant", content: "" }]); setBusy(true); setStreamPhase("retrieving"); setError(null);
    const controller = new AbortController();
    abortController.current = controller;
    try {
      await streamSse("/design-review/chat/stream", { ...json({ question: input, review_package_id: activeReviewId, conversation_history: conversationHistory }), signal: controller.signal }, token, ({ event: name, data }) => {
        if (name === "status" && typeof data === "object" && data !== null && "phase" in data) { const phase = (data as { phase: string }).phase; setStreamPhase(phase === "retrieving" ? "retrieving" : "answering"); return; }
        if (name === "token" && typeof data === "object" && data !== null && "text" in data) { const text = String((data as { text: unknown }).text); updateMessages((current) => { const next = [...current]; const latest = next.at(-1); if (latest?.role === "assistant") next[next.length - 1] = { ...latest, content: latest.content + text }; return next; }); return; }
        if (name === "final" && typeof data === "object" && data !== null) { const result = data as ChatAnswer; updateMessages((current) => { const next = [...current]; const latest = next.at(-1); if (latest?.role === "assistant") next[next.length - 1] = { role: "assistant", content: result.answer, citations: result.citations, limitations: result.limitations }; return next; }); return; }
        if (name === "error") { const detail = typeof data === "object" && data !== null && "detail" in data ? String((data as { detail: unknown }).detail) : "The answer could not be generated."; throw new Error(detail); }
      });
    } catch (reason) { updateMessages((current) => { const latest = current.at(-1); return latest?.role === "assistant" && !latest.content ? current.slice(0, -1) : current; }); if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "The answer could not be generated."); }
    finally { if (abortController.current === controller) abortController.current = null; setBusy(false); setStreamPhase(null); }
  }
  useEffect(() => () => { if (pdfObjectUrl.current) URL.revokeObjectURL(pdfObjectUrl.current); }, []);
  function stopGenerating() { abortController.current?.abort(); }
  function releasePdfObjectUrl() { if (pdfObjectUrl.current) { URL.revokeObjectURL(pdfObjectUrl.current); pdfObjectUrl.current = null; } }
  function closeSourceReader() { releasePdfObjectUrl(); setSourceReader(null); }
  async function openSourceReader(citation: Citation) {
    releasePdfObjectUrl();
    setSourceReader({ citation, context: null, error: null, view: "passage", pdfUrl: null, pdfError: null, pdfLoading: false });
    try {
      const context = await api<DocumentChunkContext>(`/documents/${citation.document_version_id}/chunks/${citation.chunk_id}/context`, {}, token);
      setSourceReader((current) => current?.citation.chunk_id === citation.chunk_id ? { ...current, context } : current);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "The source passage could not be opened.";
      setSourceReader((current) => current?.citation.chunk_id === citation.chunk_id ? { ...current, error: message } : current);
    }
  }
  async function openOriginalPdfPage() {
    if (!sourceReader) return;
    const citation = sourceReader.citation;
    setSourceReader((current) => current?.citation.chunk_id === citation.chunk_id ? { ...current, view: "pdf", pdfLoading: true, pdfError: null } : current);
    try {
      const response = await fetch(`/api/backend/documents/${citation.document_version_id}/source`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null);
        const detail = typeof payload === "object" && payload !== null && "detail" in payload ? String((payload as { detail: unknown }).detail) : "The original PDF could not be opened.";
        throw new Error(detail);
      }
      const url = URL.createObjectURL(await response.blob());
      setSourceReader((current) => {
        if (current?.citation.chunk_id !== citation.chunk_id) { URL.revokeObjectURL(url); return current; }
        releasePdfObjectUrl();
        pdfObjectUrl.current = url;
        return { ...current, pdfUrl: url, pdfLoading: false };
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "The original PDF could not be opened.";
      setSourceReader((current) => current?.citation.chunk_id === citation.chunk_id ? { ...current, pdfLoading: false, pdfError: message } : current);
    }
  }
  const citedAnswers = messages.map((message, index) => ({ message, index })).filter(({ message }) => message.role === "assistant" && (message.citations?.length ?? 0) > 0).reverse();
  return <section className="workspace-grid assistant-grid"><div className="panel conversation-panel"><div className="panel-header"><div><p className="eyebrow">EVIDENCE Q&A</p><h2>Ask inside the boundary</h2></div>{activeReview && <span className="scope-chip">{activeReview.name}</span>}</div>
    <div className="conversation">{messages.length ? messages.map((message, index) => { const isStreamingAnswer = busy && message.role === "assistant" && index === messages.length - 1; const placeholder = streamPhase === "retrieving" ? "Searching the selected evidence…" : "Writing the evidence-grounded answer…"; return <article key={index} className={`message ${message.role}`}><span>{message.role === "user" ? "ENGINEER" : "ASSISTANT"}</span><p>{message.content || (isStreamingAnswer ? placeholder : "")}</p>{isStreamingAnswer && !message.content && <small>Sources and citations will appear when the answer is complete.</small>}{message.limitations && <small>Limitation: {message.limitations}</small>}</article>; }) : <p className="muted">The latest 20 question-and-answer rounds are retained for this review package in this browser.</p>}</div>
    {error && <Notice kind="error">{error}</Notice>}<form className="composer" onSubmit={send}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={activeReviewId ? "Ask a technical question about the selected design versions…" : "Select a review package in the header first."} disabled={!activeReviewId} />{busy ? <button type="button" className="secondary" onClick={stopGenerating}>Stop generating</button> : <button className="primary" disabled={!activeReviewId}>Send</button>}</form>
  </div><aside className="panel evidence-panel"><p className="eyebrow">CITATION LEDGER</p><h3>Evidence from this conversation</h3>{citedAnswers.length ? citedAnswers.map(({ message, index }, groupIndex) => <details className="citation-group" key={index} open={groupIndex === 0}><summary>Answer {Math.floor(index / 2) + 1} · {message.citations?.length ?? 0} cited passage(s)</summary>{message.citations?.map((citation) => <article className="citation" key={`${index}-${citation.chunk_id}`}><b>{citation.document_title} <small>v{citation.version}</small></b><span>{citation.section ?? "Unsectioned"} · p.{citation.page ?? "—"}</span><p>{citation.excerpt}</p><button type="button" className="citation-read" onClick={() => void openSourceReader(citation)}>Read in source</button></article>)}</details>) : <p className="muted">Citations appear here after a grounded answer.</p>}</aside>{sourceReader && <div className="modal-backdrop source-reader-backdrop" role="presentation" onMouseDown={closeSourceReader}><section className="modal-card source-reader" role="dialog" aria-modal="true" aria-labelledby="source-reader-title" onMouseDown={(event) => event.stopPropagation()}><div className="panel-header"><div><p className="eyebrow">SOURCE READER</p><h2 id="source-reader-title">{sourceReader.citation.document_title}</h2><p className="muted">v{sourceReader.citation.version} · {sourceReader.citation.section ?? "Unsectioned"} · page {sourceReader.citation.page ?? "—"}</p></div><button type="button" className="secondary" onClick={closeSourceReader}>Close</button></div><div className="source-reader-tabs"><button type="button" className={sourceReader.view === "passage" ? "source-reader-tab active" : "source-reader-tab"} onClick={() => setSourceReader((current) => current ? { ...current, view: "passage" } : current)}>Passage text</button><button type="button" className={sourceReader.view === "pdf" ? "source-reader-tab active" : "source-reader-tab"} onClick={() => void openOriginalPdfPage()}>Original PDF · p.{sourceReader.citation.page ?? 1}</button></div>{sourceReader.view === "pdf" ? sourceReader.pdfError ? <Notice kind="error">{sourceReader.pdfError}</Notice> : sourceReader.pdfLoading || !sourceReader.pdfUrl ? <Notice>Loading original PDF page…</Notice> : <iframe className="pdf-page-viewer" title={`${sourceReader.citation.document_title} page ${sourceReader.citation.page ?? 1}`} src={`${sourceReader.pdfUrl}#page=${Math.max(1, sourceReader.citation.page ?? 1)}`} /> : sourceReader.error ? <Notice kind="error">{sourceReader.error}</Notice> : !sourceReader.context ? <Notice>Opening the original passage…</Notice> : <div className="source-passages">{sourceReader.context.chunks.map((chunk) => <article key={chunk.id} className={`source-passage ${chunk.id === sourceReader.context?.requested_chunk_id ? "source-passage-active" : ""}`}><div><span>Passage {chunk.chunk_index + 1}</span><small>{chunk.section ?? "Unsectioned"} · p.{chunk.page}</small></div><pre>{chunk.content}</pre></article>)}</div>}</section></div>}</section>;
}

function LiveRun({ token, runId, onSettled }: { token: string; runId: string; onSettled: () => void }) {
  const [progress, setProgress] = useState<AnalysisProgress | null>(null); const [findings, setFindings] = useState<Finding[]>([]); const [error, setError] = useState<string | null>(null); const [retrying, setRetrying] = useState(false);
  const pollingStopped = useRef(false);
  const refresh = useCallback(async () => { try { const [nextProgress, nextFindings] = await Promise.all([api<AnalysisProgress>(`/analysis-runs/${runId}/progress`, {}, token), api<Finding[]>(`/analysis-runs/${runId}/findings`, {}, token)]); setProgress(nextProgress); setFindings(nextFindings); const allItemsSettled = nextProgress.total_items > 0 && nextProgress.completed_items + nextProgress.failed_items === nextProgress.total_items; if (allItemsSettled && !pollingStopped.current) { pollingStopped.current = true; onSettled(); } } catch (reason) { setError(reason instanceof Error ? reason.message : "Progress is unavailable."); } }, [onSettled, runId, token]);
  useEffect(() => { pollingStopped.current = false; void refresh(); const timer = window.setInterval(() => { if (!pollingStopped.current) void refresh(); }, 2000); return () => window.clearInterval(timer); }, [refresh]);
  async function retry() { setRetrying(true); setError(null); pollingStopped.current = false; try { await api(`/analysis-runs/${runId}/retry`, { method: "POST" }, token); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Retry could not be queued."); } finally { setRetrying(false); } }
  if (!progress) return <Notice>Connecting to the worker queue…</Notice>;
  const settled = progress.completed_items + progress.failed_items;
  const failedItems = progress.items.filter((item) => item.status === "failed");
  return <section className="panel live-run"><div className="panel-header"><div><p className="eyebrow">ASYNC ANALYSIS</p><h2>Requirement workboard</h2></div><Tag value={progress.status} /></div><div className="progress-line"><span style={{ width: `${progress.total_items ? (settled / progress.total_items) * 100 : 0}%` }} /></div><div className="metrics"><div><b>{settled}</b><span>settled</span></div><div><b>{progress.running_items}</b><span>active</span></div><div><b>{progress.failed_items}</b><span>failed</span></div></div>
    {error && <Notice kind="error">{error}</Notice>}
    {failedItems.length > 0 && <section className="failure-panel" aria-live="polite"><div className="failure-panel-header"><div><p className="eyebrow">FAILURE DIAGNOSTICS</p><h3>{failedItems.length} requirement{failedItems.length === 1 ? "" : "s"} need attention</h3><p>The recorded error is retained below. Retrying queues only the failed requirements; completed findings remain unchanged.</p></div><button className="secondary" disabled={retrying} onClick={retry}>{retrying ? "Queueing…" : `Retry ${failedItems.length} failed requirement${failedItems.length === 1 ? "" : "s"}`}</button></div>{progress.error_message && <Notice kind="error">{progress.error_message}</Notice>}<div className="failure-list">{failedItems.map((item) => <details className="failure-entry" key={item.id} open><summary><code>{item.requirement_code}</code><span>Attempt {item.attempt_count}</span></summary><pre>{item.error_message ?? "No diagnostic message was returned by the worker."}</pre></details>)}</div></section>}
    <div className="item-list">{progress.items.map((item) => <div className="item-row" key={item.id}><code>{item.requirement_code}</code><Tag value={item.status} /><span>attempt {item.attempt_count}</span>{item.error_message && <small title={item.error_message}>{item.error_message}</small>}</div>)}</div>
    {findings.length > 0 && <p className="muted">{findings.length} finding{findings.length === 1 ? "" : "s"} already available while remaining items finish.</p>}
  </section>;
}

function Review({ token, reviews, activeReviewId, onRunCreated }: { token: string; reviews: ReviewPackage[]; activeReviewId: string | null; onRunCreated: (id: string) => void }) {
  const [runId, setRunId] = useState<string | null>(null); const [rows, setRows] = useState<MatrixRow[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const active = reviews.find((review) => review.id === activeReviewId);
  useEffect(() => {
    setRunId(null); setRows([]); setError(null);
    if (!active) return;
    api<{ id: string }[]>(`/review-packages/${active.id}/analyses`, {}, token)
      .then((runs) => setRunId(runs[0]?.id ?? null))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Previous review runs are unavailable."));
  }, [active?.id, token]);
  async function start() { if (!active) return; setBusy(true); setError(null); try { const run = await api<{ id: string }>(`/review-packages/${active.id}/analyses`, { method: "POST" }, token); setRunId(run.id); setRows([]); onRunCreated(run.id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to queue the review."); } finally { setBusy(false); } }
  async function loadMatrix() { if (!runId) return; try { setRows(await api<MatrixRow[]>(`/analysis-runs/${runId}/matrix`, {}, token)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Traceability matrix is unavailable."); } }
  async function exportMatrix() { if (!runId) return; try { const response = await fetch(`/api/backend/analysis-runs/${runId}/export.xlsx`, { headers: { Authorization: `Bearer ${token}` } }); if (!response.ok) throw new Error("Export failed."); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "urs-traceability-matrix.xlsx"; link.click(); URL.revokeObjectURL(url); } catch (reason) { setError(reason instanceof Error ? reason.message : "Matrix export failed."); } }
  if (!active) return <Notice>Select a Review Package to begin a controlled coverage analysis.</Notice>;
  return <section className="stack"><section className="review-hero"><div><p className="eyebrow">FROZEN REVIEW PACKAGE</p><h2>{active.name}</h2><p>{active.requirement_count} controlled requirements · {active.design_document_version_ids.length} supplier design version(s)</p></div><div className="review-action"><button className="primary" onClick={start} disabled={busy || !!runId} title={runId ? "The existing run is retained. Retry failed requirements from the diagnostics panel below." : undefined}>{busy ? "Queueing…" : "Generate candidate findings"}</button>{runId && <small>The selected run is retained. Use the diagnostics panel to retry failed requirements.</small>}</div></section>{error && <Notice kind="error">{error}</Notice>}
    {runId ? <LiveRun token={token} runId={runId} onSettled={() => void loadMatrix()} /> : <Notice>Each requirement is delivered as an independent Redis worker job. The final matrix includes completed, evidence-insufficient, and failed items.</Notice>}
    {rows.length > 0 && <section className="panel"><div className="panel-header"><div><p className="eyebrow">TRACEABILITY MATRIX</p><h2>Every frozen URS, with evidence or a visible exception</h2></div><div className="row"><button className="secondary" onClick={() => void loadMatrix()}>Refresh matrix</button><button className="secondary" onClick={() => void exportMatrix()}>Export Excel</button></div></div><div className="finding-list">{rows.map((row) => <article className="finding" key={row.requirement_code}><div><code>{row.requirement_code}</code><Tag value={row.design_status ?? (row.analysis_status === "failed" ? "technical exception" : row.analysis_status)} /></div><p>{row.requirement_text}</p><small>{row.status_definition ?? "No candidate conclusion is available for this URS; human review is required."}</small>{row.rationale && <p>{row.rationale}</p>}{row.gap && <small>Potential gap: {row.gap}</small>}{row.technical_error && <Notice kind="error">Technical exception: {row.technical_error}</Notice>}<details><summary>{row.evidence.length} cited passage(s)</summary>{row.evidence.map((evidence) => <div className="citation" key={evidence.chunk_id}><b>{evidence.document_title} <small>v{evidence.version}</small></b><span>{evidence.section ?? "Unsectioned"} · p.{evidence.page ?? "—"}</span><p>{evidence.excerpt}</p></div>)}</details>{row.audit_points.length > 0 && <details><summary>{row.audit_points.length} internal audit point(s)</summary>{row.audit_points.map((point) => <article className="citation" key={point.point_id}><b>{point.point_id} · {point.design_status.replaceAll("_", " ")}</b><span>{point.review_point}</span><p>{point.rationale}</p>{point.evidence.map((evidence) => <small key={evidence.chunk_id}>{evidence.document_title} v{evidence.version} · {evidence.section ?? "Unsectioned"} · p.{evidence.page ?? "—"}</small>)}</article>)}</details>}</article>)}</div></section>}
  </section>;
}

function Knowledge({ token, documents, baselines, reviews, refresh, selectReview }: { token: string; documents: DocumentVersion[]; baselines: RequirementBaseline[]; reviews: ReviewPackage[]; refresh: () => Promise<void>; selectReview: (id: string) => void }) {
  const [notice, setNotice] = useState<string | null>(null); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [selectedBaseline, setSelectedBaseline] = useState<RequirementBaseline | null>(null); const [requirements, setRequirements] = useState<Requirement[]>([]); const [requirementsLoading, setRequirementsLoading] = useState(false);
  async function documentUpload(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const file = form.get("file") as File | null; const pdfPassword = String(form.get("pdfPassword") || ""); let created: DocumentVersion | null = null; setBusy(true); setError(null); try { created = await api<DocumentVersion>("/documents", json({ title: form.get("title"), document_type: form.get("documentType"), system: form.get("system"), vendor: form.get("vendor") || null, version: form.get("version"), status: "draft", file_name: file?.name || null }), token); if (file && file.size) { const upload = new FormData(); upload.append("file", file); if (pdfPassword) upload.append("pdf_password", pdfPassword); await api(`/documents/${created.id}/upload`, { method: "POST", body: upload }, token); } setNotice("Source registered and parsed. Inspect it, then create the retrieval index."); await refresh(); formElement.reset(); } catch (reason) { const message = reason instanceof Error ? reason.message : "Document upload failed."; if (created) { try { await api(`/documents/${created.id}/archive`, json({ reason: `Automatic archive after upload failure: ${message}` }), token); await refresh(); setNotice("Upload failed. The document registration was archived and retained for audit."); } catch { setNotice("Upload failed. The document registration could not be archived automatically."); } } setError(message); } finally { setBusy(false); } }
  async function importBaseline(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setBusy(true); setError(null); try { await api("/requirement-baselines/import", { method: "POST", body: form }, token); setNotice("Controlled requirement table imported."); await refresh(); formElement.reset(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Requirement import failed."); } finally { setBusy(false); } }
  async function createPackage(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const ids = form.getAll("documentIds").map(String); setBusy(true); setError(null); try { const review = await api<ReviewPackage>("/review-packages", json({ name: form.get("name"), system: form.get("system"), requirement_baseline_id: form.get("baselineId"), design_document_version_ids: ids }), token); selectReview(review.id); setNotice("Review Package frozen. It is ready for chat and analysis."); await refresh(); formElement.reset(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Review Package could not be created."); } finally { setBusy(false); } }
  async function indexDocument(id: string) { setBusy(true); try { await api(`/documents/${id}/index`, { method: "POST" }, token); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Indexing failed."); } finally { setBusy(false); } }
  async function reparseDocument(document: DocumentVersion) { const pdfPassword = window.prompt("PDF password, if the stored source is encrypted. Leave blank otherwise:"); if (pdfPassword === null) return; const body = new FormData(); if (pdfPassword) body.append("pdf_password", pdfPassword); setBusy(true); setError(null); try { await api(`/documents/${document.id}/reparse`, { method: "POST", body }, token); setNotice("Source re-parsed with the current chunk settings. Inspect it, then create the new index."); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Re-parsing failed."); } finally { setBusy(false); } }
  async function archiveDocument(document: DocumentVersion) { const reason = window.prompt("Reason for archiving this document registration:"); if (reason === null) return; if (reason.trim().length < 3) { setError("An archive reason must contain at least 3 characters."); return; } setBusy(true); setError(null); try { await api(`/documents/${document.id}/archive`, json({ reason: reason.trim() }), token); setNotice("Document registration archived. Its audit record remains available."); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Document archive failed."); } finally { setBusy(false); } }
  async function viewRequirements(baseline: RequirementBaseline) { setSelectedBaseline(baseline); setRequirements([]); setRequirementsLoading(true); setError(null); try { setRequirements(await api<Requirement[]>(`/requirement-baselines/${baseline.id}/requirements`, {}, token)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Requirements are unavailable."); } finally { setRequirementsLoading(false); } }
  const activeDocuments = documents.filter((document) => document.status !== "archived");
  const archivedDocuments = documents.filter((document) => document.status === "archived");
  const designDocuments = documents.filter((document) => DESIGN_DOCUMENT_TYPES.some((type) => type.value === document.document_type) && document.status !== "archived" && document.ingestion_status === "indexed");
  return <section className="stack">
    <section className="workspace-intro">
      <div>
        <p className="eyebrow">START HERE / CONTROLLED KNOWLEDGE</p>
        <h2>Turn controlled requirements and design specifications into reviewable evidence.</h2>
        <p>This workspace keeps requirement baselines, design-specification versions, chat answers, and asynchronous findings inside one frozen review scope.</p>
      </div>
      <ol>
        <li><b>Import</b><span>a controlled requirement baseline.</span></li>
        <li><b>Register</b><span>and index a design specification.</span></li>
        <li><b>Freeze</b><span>a Review Package for evidence chat and candidate findings.</span></li>
      </ol>
    </section>
    <section className="knowledge-grid">
      <div className="stack">
        {notice && <Notice kind="success">{notice}</Notice>}
        {error && <Notice kind="error">{error}</Notice>}
        <section className="panel">
          <p className="eyebrow">01 / CONTROLLED REQUIREMENTS</p>
          <h2>Import a requirement baseline</h2>
          <form className="form-grid" onSubmit={importBaseline}>
            <label>Baseline name<input name="name" placeholder="Fleet Manager Requirements v1.0" /></label>
            <label>System<SystemSelect name="system" /></label>
            <label className="wide">CSV file<input name="file" type="file" accept=".csv,text/csv" required /></label>
            <button className="primary" disabled={busy}>Import requirement table</button>
          </form>
          <div className="baseline-list">{baselines.length ? baselines.map((baseline) => <div className="baseline-row" key={baseline.id}><div><b>{baseline.name}</b><span>{systemLabel(baseline.system)}</span></div><button className="secondary" disabled={busy} onClick={() => void viewRequirements(baseline)}>View imported URS</button></div>) : <p className="muted">No controlled requirement baseline has been imported.</p>}</div>
        </section>
        <section className="panel">
          <p className="eyebrow">02 / DESIGN EVIDENCE</p>
          <h2>Register and parse a design specification</h2>
          <form className="form-grid" onSubmit={documentUpload}>
            <label>Document title<input name="title" required /></label>
            <label>Version<input name="version" placeholder="1.0" required /></label>
            <label>Specification type<select name="documentType" defaultValue="FS">{DESIGN_DOCUMENT_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>
            <label>System<SystemSelect name="system" /></label>
            <label>Vendor<input name="vendor" /></label>
            <label className="wide">PDF, DOCX, or CSV file<input name="file" type="file" accept=".pdf,.docx,.csv" required /></label>
            <label className="wide">PDF password <small>Only needed for encrypted PDFs. Used for this parse only; it is never stored.</small><input name="pdfPassword" type="password" autoComplete="new-password" /></label>
            <button className="primary" disabled={busy}>Parse source</button>
          </form>
          <div className="doc-list">{activeDocuments.length ? activeDocuments.map((document) => <div className="doc-row" key={document.id}><div><b>{document.title}</b><span>{documentTypeLabel(document.document_type)} · {systemLabel(document.system)} · v{document.version} · {document.chunk_count} chunks</span></div><Tag value={document.status} /><Tag value={document.ingestion_status} />{document.ingestion_status === "parsed_pending_index" && <button className="secondary" disabled={busy} onClick={() => void indexDocument(document.id)}>Create index</button>}<button className="secondary" disabled={busy} onClick={() => void reparseDocument(document)}>Reparse source</button><button className="secondary" disabled={busy} onClick={() => void archiveDocument(document)}>Archive</button></div>) : <p className="muted">No active design specification has been registered.</p>}</div>
          {archivedDocuments.length > 0 && <details className="archived-sources"><summary><span>ARCHIVED SOURCES</span><b>{archivedDocuments.length}</b><small>Retained for audit; excluded from active source selection.</small></summary><div className="doc-list archived-document-list">{archivedDocuments.map((document) => <div className="doc-row" key={document.id}><div><b>{document.title}</b><span>{documentTypeLabel(document.document_type)} · {systemLabel(document.system)} · v{document.version}</span><small>Archive reason: {document.archived_reason ?? "No reason recorded."}</small></div><Tag value="archived" /></div>)}</div></details>}
        </section>
      </div>
      <aside className="stack">
        <section className="panel">
          <p className="eyebrow">03 / FROZEN SCOPE</p>
          <h2>Create Review Package</h2>
          <form className="stack" onSubmit={createPackage}>
            <label>Name<input name="name" placeholder="Fleet Manager Review 001" required /></label>
            <label>System<SystemSelect name="system" /></label>
            <label>Requirement baseline<select name="baselineId" required defaultValue=""><option value="" disabled>Select a baseline</option>{baselines.map((baseline) => <option key={baseline.id} value={baseline.id}>{baseline.name} — {systemLabel(baseline.system)}</option>)}</select></label>
            <fieldset><legend>Indexed design specifications</legend>{designDocuments.map((document) => <label className="check" key={document.id}><input type="checkbox" name="documentIds" value={document.id} disabled={document.ingestion_status !== "indexed"} />{document.title} <small>{documentTypeLabel(document.document_type)} · v{document.version}</small></label>)}</fieldset>
            <button className="primary" disabled={busy}>Freeze review scope</button>
          </form>
        </section>
        <section className="panel">
          <p className="eyebrow">ACTIVE PACKAGES</p>
          {reviews.length ? reviews.map((review) => <button className="package-link" key={review.id} onClick={() => selectReview(review.id)}><b>{review.name}</b><span>{systemLabel(review.system)} · {review.requirement_count} requirements</span></button>) : <p className="muted">No Review Package yet.</p>}
        </section>
      </aside>
    </section>
    {selectedBaseline && <div className="modal-backdrop" role="presentation" onMouseDown={() => setSelectedBaseline(null)}><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="urs-dialog-title" onMouseDown={(event) => event.stopPropagation()}><div className="panel-header"><div><p className="eyebrow">CONTROLLED REQUIREMENTS</p><h2 id="urs-dialog-title">{selectedBaseline.name}</h2><p className="muted">{systemLabel(selectedBaseline.system)} · {requirements.length} imported requirement{requirements.length === 1 ? "" : "s"}</p></div><button className="secondary" onClick={() => setSelectedBaseline(null)}>Close</button></div>{requirementsLoading ? <Notice>Loading imported URS…</Notice> : <div className="urs-table-wrap"><table className="urs-table"><thead><tr><th>URS ID</th><th>Requirement</th><th>Rationale / impact</th><th>Critical</th><th>Priority</th><th>Category</th></tr></thead><tbody>{requirements.map((requirement) => <tr key={requirement.id}><td><code>{requirement.requirement_code}</code></td><td>{requirement.requirement_text}</td><td>{requirement.rationale_impact ?? "—"}</td><td>{requirement.is_critical ? "Yes" : "No"}</td><td>{requirement.priority ?? "—"}</td><td>{requirement.category ?? "—"}</td></tr>)}</tbody></table>{requirements.length === 0 && <p className="muted">No requirements are available in this baseline.</p>}</div>}</section></div>}
  </section>;
}

function Help({ onNavigate }: { onNavigate: (view: View) => void }) {
  const modules: { id: View; step: string; title: string; body: string; action: string }[] = [
    { id: "knowledge", step: "01", title: "Knowledge Control", body: "Import a controlled requirement baseline, register Functional, Software Design, or Hardware Design Specifications, then freeze the exact scope used for review.", action: "Build a review scope" },
    { id: "assistant", step: "02", title: "Evidence Chat", body: "Ask technical questions against the selected frozen package. Answers expose the supporting document passages.", action: "Ask an evidence question" },
    { id: "review", step: "03", title: "Design Review", body: "Queue a requirement-level analysis. Workers publish completed findings while remaining items continue in the background.", action: "Run a coverage review" },
  ];
  return <section className="help-layout"><section className="help-hero"><p className="eyebrow">ABOUT THIS WORKSPACE</p><h2>Evidence first. Decisions remain with the engineer.</h2><p>Warehouse Automation Design Review Console helps engineering teams compare controlled requirements with supplier design evidence. It creates candidate findings and citations; it does not make approval, compliance, or release decisions.</p></section><section className="help-grid">{modules.map((module) => <article className="help-card" key={module.id}><span>{module.step}</span><h3>{module.title}</h3><p>{module.body}</p><button className="text-button" onClick={() => onNavigate(module.id)}>{module.action} →</button></article>)}</section><section className="panel help-notes"><p className="eyebrow">OPERATING PRINCIPLES</p><div><p><b>Frozen scope</b><span>Chat and analysis only use the selected Review Package.</span></p><p><b>Traceable evidence</b><span>Findings retain their citations and source locations for reviewer inspection.</span></p><p><b>Visible progress</b><span>Redis workers update each requirement independently; failed items can be retried.</span></p></div></section></section>;
}

export default function Home() {
  const [token, setToken] = useState<string | null>(null); const [user, setUser] = useState<User | null>(null); const [view, setView] = useState<View>("knowledge"); const [documents, setDocuments] = useState<DocumentVersion[]>([]); const [baselines, setBaselines] = useState<RequirementBaseline[]>([]); const [reviews, setReviews] = useState<ReviewPackage[]>([]); const [activeReviewId, setActiveReviewId] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [error, setError] = useState<string | null>(null);
  const clearSession = useCallback(() => { window.localStorage.removeItem("industrial-rag-token"); setToken(null); setUser(null); setDocuments([]); setBaselines([]); setReviews([]); setActiveReviewId(null); setError(null); }, []);
  const refresh = useCallback(async () => { if (!token) return; setLoading(true); try { const [nextDocuments, nextBaselines, nextReviews] = await Promise.all([api<DocumentVersion[]>("/documents", {}, token), api<RequirementBaseline[]>("/requirement-baselines", {}, token), api<ReviewPackage[]>("/review-packages", {}, token)]); setDocuments(nextDocuments); setBaselines(nextBaselines); setReviews(nextReviews); setActiveReviewId((current) => current && nextReviews.some((review) => review.id === current) ? current : nextReviews[0]?.id ?? null); } catch (reason) { if (reason instanceof ApiError && reason.status === 401) { clearSession(); return; } setError(reason instanceof Error ? reason.message : "Workspace data unavailable."); } finally { setLoading(false); } }, [clearSession, token]);
  useEffect(() => { const saved = window.localStorage.getItem("industrial-rag-token"); if (saved) { setToken(saved); api<User>("/auth/me", {}, saved).then(setUser).catch(clearSession); } }, [clearSession]);
  useEffect(() => { void refresh(); }, [refresh]);
  const activeReview = useMemo(() => reviews.find((review) => review.id === activeReviewId), [activeReviewId, reviews]);
  const viewMeta: Record<View, { eyebrow: string; title: string }> = { knowledge: { eyebrow: "CONTROLLED SOURCE WORKSPACE", title: "Knowledge Control" }, assistant: { eyebrow: "SCOPED EVIDENCE CHAT", title: "Design Assistant" }, review: { eyebrow: "ASYNCHRONOUS COVERAGE REVIEW", title: "Design Review" }, help: { eyebrow: "WORKSPACE GUIDE", title: "How this console works" } };
  function signIn(nextToken: string, nextUser: User) { window.localStorage.setItem("industrial-rag-token", nextToken); setToken(nextToken); setUser(nextUser); }
  function signOut() { clearSession(); }
  if (!token || !user) return <AuthGate onAuthenticated={signIn} />;
  return <main className="app-shell"><aside className="rail"><div className="brand"><span className="brand-mark">DR</span><div><b>Warehouse Automation</b><small>Design Review Console</small></div></div><nav>{([ ["knowledge", "Knowledge control"], ["assistant", "Evidence chat"], ["review", "Review matrix"], ["help", "How it works"] ] as [View, string][]).map(([id, label], index) => <button key={id} className={view === id ? "nav-active" : ""} onClick={() => setView(id)}><span>0{index + 1}</span>{label}</button>)}</nav><div className="rail-footer"><b>{user.display_name}</b><small>{user.role}</small><button className="text-button" onClick={signOut}>Sign out</button></div></aside><section className="main-stage"><header className="topbar"><div><p className="eyebrow">{viewMeta[view].eyebrow}</p><h1>{viewMeta[view].title}</h1></div><div className="scope-select"><label>Active review package<select value={activeReviewId ?? ""} onChange={(event) => setActiveReviewId(event.target.value || null)}><option value="">No active scope</option>{reviews.map((review) => <option value={review.id} key={review.id}>{review.name}</option>)}</select></label>{activeReview && <small>{activeReview.requirement_count} requirements · {activeReview.design_document_version_ids.length} sources</small>}</div></header>{error && <Notice kind="error">{error}</Notice>}{loading && <div className="loading-bar" />}{view === "assistant" && <Assistant token={token} userId={user.id} reviews={reviews} activeReviewId={activeReviewId} />}{view === "review" && <Review token={token} reviews={reviews} activeReviewId={activeReviewId} onRunCreated={() => undefined} />}{view === "knowledge" && <Knowledge token={token} documents={documents} baselines={baselines} reviews={reviews} refresh={refresh} selectReview={(id) => { setActiveReviewId(id); setView("review"); }} />}{view === "help" && <Help onNavigate={setView} />}</section></main>;
}
