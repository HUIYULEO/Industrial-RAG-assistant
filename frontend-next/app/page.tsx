"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, json } from "../lib/api";
import type { AnalysisProgress, AuthConfig, ChatAnswer, DocumentVersion, Finding, MatrixRow, RequirementBaseline, ReviewPackage, User } from "../lib/types";

type View = "assistant" | "review" | "knowledge" | "help";
type Message = { role: "user" | "assistant"; content: string; citations?: ChatAnswer["citations"]; limitations?: string | null };

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

function Assistant({ token, reviews, activeReviewId }: { token: string; reviews: ReviewPackage[]; activeReviewId: string | null }) {
  const [messages, setMessages] = useState<Message[]>([{ role: "assistant", content: "Choose a frozen review package, then ask a question about the selected FS/DS evidence." }]);
  const [question, setQuestion] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const activeReview = reviews.find((review) => review.id === activeReviewId);
  async function send(event: FormEvent) {
    event.preventDefault(); if (!question.trim() || !activeReviewId) return;
    const input = question.trim(); setQuestion(""); setMessages((current) => [...current, { role: "user", content: input }]); setBusy(true); setError(null);
    try { const result = await api<ChatAnswer>("/design-review/chat", json({ question: input, review_package_id: activeReviewId }), token); setMessages((current) => [...current, { role: "assistant", content: result.answer, citations: result.citations, limitations: result.limitations }]); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The answer could not be generated."); }
    finally { setBusy(false); }
  }
  return <section className="workspace-grid assistant-grid"><div className="panel conversation-panel"><div className="panel-header"><div><p className="eyebrow">EVIDENCE Q&A</p><h2>Ask inside the boundary</h2></div>{activeReview && <span className="scope-chip">{activeReview.name}</span>}</div>
    <div className="conversation">{messages.map((message, index) => <article key={index} className={`message ${message.role}`}><span>{message.role === "user" ? "ENGINEER" : "ASSISTANT"}</span><p>{message.content}</p>{message.limitations && <small>Limitation: {message.limitations}</small>}</article>)}</div>
    {error && <Notice kind="error">{error}</Notice>}<form className="composer" onSubmit={send}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={activeReviewId ? "Ask a technical question about the selected design versions…" : "Select a review package in the header first."} disabled={!activeReviewId || busy} /><button className="primary" disabled={!activeReviewId || busy}>{busy ? "Retrieving…" : "Send"}</button></form>
  </div><aside className="panel evidence-panel"><p className="eyebrow">CITATION LEDGER</p><h3>Latest supporting evidence</h3>{[...messages].reverse().find((message) => message.citations)?.citations?.map((citation) => <article className="citation" key={citation.chunk_id}><b>{citation.document_title} <small>v{citation.version}</small></b><span>{citation.section ?? "Unsectioned"} · p.{citation.page ?? "—"}</span><p>{citation.excerpt}</p></article>) ?? <p className="muted">Citations appear here after a grounded answer.</p>}</aside></section>;
}

function LiveRun({ token, runId, onSettled }: { token: string; runId: string; onSettled: () => void }) {
  const [progress, setProgress] = useState<AnalysisProgress | null>(null); const [findings, setFindings] = useState<Finding[]>([]); const [error, setError] = useState<string | null>(null); const [retrying, setRetrying] = useState(false);
  const refresh = useCallback(async () => { try { const [nextProgress, nextFindings] = await Promise.all([api<AnalysisProgress>(`/analysis-runs/${runId}/progress`, {}, token), api<Finding[]>(`/analysis-runs/${runId}/findings`, {}, token)]); setProgress(nextProgress); setFindings(nextFindings); if (["completed", "failed"].includes(nextProgress.status)) onSettled(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Progress is unavailable."); } }, [onSettled, runId, token]);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 2000); return () => window.clearInterval(timer); }, [refresh]);
  async function retry() { setRetrying(true); try { await api(`/analysis-runs/${runId}/retry`, { method: "POST" }, token); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Retry could not be queued."); } finally { setRetrying(false); } }
  if (!progress) return <Notice>Connecting to the worker queue…</Notice>;
  const settled = progress.completed_items + progress.failed_items;
  return <section className="panel live-run"><div className="panel-header"><div><p className="eyebrow">ASYNC ANALYSIS</p><h2>Requirement workboard</h2></div><Tag value={progress.status} /></div><div className="progress-line"><span style={{ width: `${progress.total_items ? (settled / progress.total_items) * 100 : 0}%` }} /></div><div className="metrics"><div><b>{settled}</b><span>settled</span></div><div><b>{progress.running_items}</b><span>active</span></div><div><b>{progress.failed_items}</b><span>failed</span></div></div>
    {error && <Notice kind="error">{error}</Notice>}{progress.status === "failed" && <div className="row"><Notice kind="error">{progress.error_message ?? "One or more requirement items failed."}</Notice><button className="secondary" disabled={retrying} onClick={retry}>{retrying ? "Queueing…" : "Retry failed items"}</button></div>}
    <div className="item-list">{progress.items.map((item) => <div className="item-row" key={item.id}><code>{item.requirement_code}</code><Tag value={item.status} /><span>attempt {item.attempt_count}</span>{item.error_message && <small>{item.error_message}</small>}</div>)}</div>
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
  return <section className="stack"><section className="review-hero"><div><p className="eyebrow">FROZEN REVIEW PACKAGE</p><h2>{active.name}</h2><p>{active.requirement_count} controlled requirements · {active.design_document_version_ids.length} supplier design version(s)</p></div><button className="primary" onClick={start} disabled={busy || !!runId}>{busy ? "Queueing…" : "Generate candidate findings"}</button></section>{error && <Notice kind="error">{error}</Notice>}
    {runId ? <LiveRun token={token} runId={runId} onSettled={() => void loadMatrix()} /> : <Notice>Each requirement is delivered as an independent Redis worker job. The final matrix includes completed, evidence-insufficient, and failed items.</Notice>}
    {rows.length > 0 && <section className="panel"><div className="panel-header"><div><p className="eyebrow">TRACEABILITY MATRIX</p><h2>Every frozen URS, with evidence or a visible exception</h2></div><div className="row"><button className="secondary" onClick={() => void loadMatrix()}>Refresh matrix</button><button className="secondary" onClick={() => void exportMatrix()}>Export Excel</button></div></div><div className="finding-list">{rows.map((row) => <article className="finding" key={row.requirement_code}><div><code>{row.requirement_code}</code><Tag value={row.design_status ?? (row.analysis_status === "failed" ? "technical exception" : row.analysis_status)} /></div><p>{row.requirement_text}</p><small>{row.status_definition ?? "No candidate conclusion is available for this URS; human review is required."}</small>{row.rationale && <p>{row.rationale}</p>}{row.gap && <small>Potential gap: {row.gap}</small>}{row.technical_error && <Notice kind="error">Technical exception: {row.technical_error}</Notice>}<details><summary>{row.evidence.length} cited passage(s)</summary>{row.evidence.map((evidence) => <div className="citation" key={evidence.chunk_id}><b>{evidence.document_title} <small>v{evidence.version}</small></b><span>{evidence.section ?? "Unsectioned"} · p.{evidence.page ?? "—"}</span><p>{evidence.excerpt}</p></div>)}</details>{row.audit_points.length > 0 && <details><summary>{row.audit_points.length} internal audit point(s)</summary>{row.audit_points.map((point) => <article className="citation" key={point.point_id}><b>{point.point_id} · {point.design_status.replaceAll("_", " ")}</b><span>{point.review_point}</span><p>{point.rationale}</p>{point.evidence.map((evidence) => <small key={evidence.chunk_id}>{evidence.document_title} v{evidence.version} · {evidence.section ?? "Unsectioned"} · p.{evidence.page ?? "—"}</small>)}</article>)}</details>}</article>)}</div></section>}
  </section>;
}

function Knowledge({ token, documents, baselines, reviews, refresh, selectReview }: { token: string; documents: DocumentVersion[]; baselines: RequirementBaseline[]; reviews: ReviewPackage[]; refresh: () => Promise<void>; selectReview: (id: string) => void }) {
  const [notice, setNotice] = useState<string | null>(null); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function documentUpload(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); const file = form.get("file") as File | null; setBusy(true); setError(null); try { const created = await api<DocumentVersion>("/documents", json({ title: form.get("title"), document_type: form.get("documentType"), system: form.get("system"), vendor: form.get("vendor") || null, version: form.get("version"), status: "draft", file_name: file?.name || null }), token); if (file && file.size) { const upload = new FormData(); upload.append("file", file); await api(`/documents/${created.id}/upload`, { method: "POST", body: upload }, token); } setNotice("Source registered and parsed. Inspect it, then create the retrieval index."); await refresh(); event.currentTarget.reset(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Document upload failed."); } finally { setBusy(false); } }
  async function importBaseline(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(null); try { await api("/requirement-baselines/import", { method: "POST", body: form }, token); setNotice("Controlled requirement table imported."); await refresh(); event.currentTarget.reset(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Requirement import failed."); } finally { setBusy(false); } }
  async function createPackage(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); const ids = form.getAll("documentIds").map(String); setBusy(true); setError(null); try { const review = await api<ReviewPackage>("/review-packages", json({ name: form.get("name"), system: form.get("system"), requirement_baseline_id: form.get("baselineId"), design_document_version_ids: ids }), token); selectReview(review.id); setNotice("Review Package frozen. It is ready for chat and analysis."); await refresh(); event.currentTarget.reset(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Review Package could not be created."); } finally { setBusy(false); } }
  async function indexDocument(id: string) { setBusy(true); try { await api(`/documents/${id}/index`, { method: "POST" }, token); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Indexing failed."); } finally { setBusy(false); } }
  return <section className="stack"><section className="workspace-intro"><div><p className="eyebrow">START HERE / CONTROLLED KNOWLEDGE</p><h2>Turn controlled requirements and supplier designs into reviewable evidence.</h2><p>This workspace keeps URS/ES requirements, FS/DS source versions, chat answers, and asynchronous findings inside one frozen review scope.</p></div><ol><li><b>Import</b><span>the approved URS or ES baseline.</span></li><li><b>Register</b><span>the supplier FS / DS versions and create their retrieval index.</span></li><li><b>Freeze</b><span>a Review Package for evidence chat and candidate-finding analysis.</span></li></ol></section><section className="knowledge-grid"><div className="stack">{notice && <Notice kind="success">{notice}</Notice>}{error && <Notice kind="error">{error}</Notice>}<section className="panel"><p className="eyebrow">01 / CONTROLLED REQUIREMENTS</p><h2>Import URS or ES</h2><form className="form-grid" onSubmit={importBaseline}><label>Baseline name<input name="name" placeholder="AGV Fleet Manager URS v1.0" /></label><label>System<input name="system" placeholder="Fleet Manager" /></label><label className="wide">CSV source<input name="file" type="file" accept=".csv,text/csv" required /></label><button className="primary" disabled={busy}>Import requirement table</button></form></section>
    <section className="panel"><p className="eyebrow">02 / SUPPLIER EVIDENCE</p><h2>Register and parse a design source</h2><form className="form-grid" onSubmit={documentUpload}><label>Document title<input name="title" required /></label><label>Version<input name="version" placeholder="1.0" required /></label><label>Type<select name="documentType" defaultValue="FS"><option>FS</option><option>DS</option></select></label><label>System<input name="system" defaultValue="fleet_manager_wcs" required /></label><label>Vendor<input name="vendor" /></label><label className="wide">PDF, DOCX, or CSV<input name="file" type="file" accept=".pdf,.docx,.csv" required /></label><button className="primary" disabled={busy}>Parse source</button></form><div className="doc-list">{documents.map((document) => <div className="doc-row" key={document.id}><div><b>{document.title}</b><span>{document.document_type} · v{document.version} · {document.chunk_count} chunks</span></div><Tag value={document.ingestion_status} />{document.ingestion_status === "parsed_pending_index" && <button className="secondary" disabled={busy} onClick={() => void indexDocument(document.id)}>Create index</button>}</div>)}</div></section>
  </div><aside className="stack"><section className="panel"><p className="eyebrow">03 / FROZEN SCOPE</p><h2>Create Review Package</h2><form className="stack" onSubmit={createPackage}><label>Name<input name="name" placeholder="DR-001 Fleet Manager" required /></label><label>System<input name="system" defaultValue="fleet_manager_wcs" required /></label><label>Requirement baseline<select name="baselineId" required defaultValue=""><option value="" disabled>Select baseline</option>{baselines.map((baseline) => <option key={baseline.id} value={baseline.id}>{baseline.name}</option>)}</select></label><fieldset><legend>Indexed FS / DS versions</legend>{documents.filter((document) => document.document_type === "FS" || document.document_type === "DS").map((document) => <label className="check" key={document.id}><input type="checkbox" name="documentIds" value={document.id} disabled={document.ingestion_status !== "indexed"} />{document.title} <small>v{document.version}</small></label>)}</fieldset><button className="primary" disabled={busy}>Freeze review scope</button></form></section><section className="panel"><p className="eyebrow">ACTIVE PACKAGES</p>{reviews.length ? reviews.map((review) => <button className="package-link" key={review.id} onClick={() => selectReview(review.id)}><b>{review.name}</b><span>{review.requirement_count} requirements</span></button>) : <p className="muted">No Review Package yet.</p>}</section></aside></section></section>;
}

function Help({ onNavigate }: { onNavigate: (view: View) => void }) {
  const modules: { id: View; step: string; title: string; body: string; action: string }[] = [
    { id: "knowledge", step: "01", title: "Knowledge Control", body: "Import a controlled URS/ES baseline, register supplier FS/DS versions, then freeze the exact scope used for review.", action: "Build a review scope" },
    { id: "assistant", step: "02", title: "Evidence Chat", body: "Ask technical questions against the selected frozen package. Answers expose the supporting document passages.", action: "Ask an evidence question" },
    { id: "review", step: "03", title: "Design Review", body: "Queue a requirement-level analysis. Workers publish completed findings while remaining items continue in the background.", action: "Run a coverage review" },
  ];
  return <section className="help-layout"><section className="help-hero"><p className="eyebrow">ABOUT THIS WORKSPACE</p><h2>Evidence first. Decisions remain with the engineer.</h2><p>Warehouse Automation Design Review Console helps engineering teams compare controlled requirements with supplier design evidence. It creates candidate findings and citations; it does not make approval, compliance, or release decisions.</p></section><section className="help-grid">{modules.map((module) => <article className="help-card" key={module.id}><span>{module.step}</span><h3>{module.title}</h3><p>{module.body}</p><button className="text-button" onClick={() => onNavigate(module.id)}>{module.action} →</button></article>)}</section><section className="panel help-notes"><p className="eyebrow">OPERATING PRINCIPLES</p><div><p><b>Frozen scope</b><span>Chat and analysis only use the selected Review Package.</span></p><p><b>Traceable evidence</b><span>Findings retain their citations and source locations for reviewer inspection.</span></p><p><b>Visible progress</b><span>Redis workers update each requirement independently; failed items can be retried.</span></p></div></section></section>;
}

export default function Home() {
  const [token, setToken] = useState<string | null>(null); const [user, setUser] = useState<User | null>(null); const [view, setView] = useState<View>("knowledge"); const [documents, setDocuments] = useState<DocumentVersion[]>([]); const [baselines, setBaselines] = useState<RequirementBaseline[]>([]); const [reviews, setReviews] = useState<ReviewPackage[]>([]); const [activeReviewId, setActiveReviewId] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => { if (!token) return; setLoading(true); try { const [nextDocuments, nextBaselines, nextReviews] = await Promise.all([api<DocumentVersion[]>("/documents", {}, token), api<RequirementBaseline[]>("/requirement-baselines", {}, token), api<ReviewPackage[]>("/review-packages", {}, token)]); setDocuments(nextDocuments); setBaselines(nextBaselines); setReviews(nextReviews); setActiveReviewId((current) => current && nextReviews.some((review) => review.id === current) ? current : nextReviews[0]?.id ?? null); } catch (reason) { setError(reason instanceof Error ? reason.message : "Workspace data unavailable."); } finally { setLoading(false); } }, [token]);
  useEffect(() => { const saved = window.localStorage.getItem("industrial-rag-token"); if (saved) { setToken(saved); api<User>("/auth/me", {}, saved).then(setUser).catch(() => window.localStorage.removeItem("industrial-rag-token")); } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const activeReview = useMemo(() => reviews.find((review) => review.id === activeReviewId), [activeReviewId, reviews]);
  const viewMeta: Record<View, { eyebrow: string; title: string }> = { knowledge: { eyebrow: "CONTROLLED SOURCE WORKSPACE", title: "Knowledge Control" }, assistant: { eyebrow: "SCOPED EVIDENCE CHAT", title: "Design Assistant" }, review: { eyebrow: "ASYNCHRONOUS COVERAGE REVIEW", title: "Design Review" }, help: { eyebrow: "WORKSPACE GUIDE", title: "How this console works" } };
  function signIn(nextToken: string, nextUser: User) { window.localStorage.setItem("industrial-rag-token", nextToken); setToken(nextToken); setUser(nextUser); }
  function signOut() { window.localStorage.removeItem("industrial-rag-token"); setToken(null); setUser(null); setDocuments([]); setBaselines([]); setReviews([]); }
  if (!token || !user) return <AuthGate onAuthenticated={signIn} />;
  return <main className="app-shell"><aside className="rail"><div className="brand"><span className="brand-mark">DR</span><div><b>Warehouse Automation</b><small>Design Review Console</small></div></div><nav>{([ ["knowledge", "Knowledge control"], ["assistant", "Evidence chat"], ["review", "Review matrix"], ["help", "How it works"] ] as [View, string][]).map(([id, label], index) => <button key={id} className={view === id ? "nav-active" : ""} onClick={() => setView(id)}><span>0{index + 1}</span>{label}</button>)}</nav><div className="rail-footer"><b>{user.display_name}</b><small>{user.role}</small><button className="text-button" onClick={signOut}>Sign out</button></div></aside><section className="main-stage"><header className="topbar"><div><p className="eyebrow">{viewMeta[view].eyebrow}</p><h1>{viewMeta[view].title}</h1></div><div className="scope-select"><label>Active review package<select value={activeReviewId ?? ""} onChange={(event) => setActiveReviewId(event.target.value || null)}><option value="">No active scope</option>{reviews.map((review) => <option value={review.id} key={review.id}>{review.name}</option>)}</select></label>{activeReview && <small>{activeReview.requirement_count} requirements · {activeReview.design_document_version_ids.length} sources</small>}</div></header>{error && <Notice kind="error">{error}</Notice>}{loading && <div className="loading-bar" />}{view === "assistant" && <Assistant token={token} reviews={reviews} activeReviewId={activeReviewId} />}{view === "review" && <Review token={token} reviews={reviews} activeReviewId={activeReviewId} onRunCreated={() => undefined} />}{view === "knowledge" && <Knowledge token={token} documents={documents} baselines={baselines} reviews={reviews} refresh={refresh} selectReview={(id) => { setActiveReviewId(id); setView("review"); }} />}{view === "help" && <Help onNavigate={setView} />}</section></main>;
}
