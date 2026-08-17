"""Warehouse Automation Design Review Assistant workspace."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


API_BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat").replace("/chat", "").rstrip("/")
SYSTEM_NAME = "AGV Fleet Manager / WCS"
DOCUMENT_TYPES = ["URS", "ES", "FS", "DS", "TECHNICAL_MANUAL", "INTEGRATION_GUIDE"]
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
}
NAV_ITEMS = ["Design Assistant", "Design Review", "Knowledge & Requirements"]

st.set_page_config(
    page_title="Warehouse Automation Design Review Assistant",
    page_icon="WA",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
      [data-testid="stHeader"] { background:#ffffff; }
      [data-testid="stAppViewContainer"] { background:#ffffff; }
      .block-container { max-width:1540px; padding:1.5rem 2.3rem 1.25rem; }
      [data-testid="stSidebar"] { background:#f7f9fd; border-right:1px solid #dce5f2; }
      [data-testid="stSidebar"] > div:first-child { padding-top:1.4rem; }
      h1, h2, h3 { color:#001965 !important; letter-spacing:-.02em; }
      h1 { font-size:1.9rem !important; margin-bottom:.1rem !important; }
      .workspace-name { color:#001965; font-size:1rem; font-weight:750; line-height:1.32; }
      .workspace-name span { color:#005ad2; font-weight:550; }
      .side-label { color:#6a7891; font-size:.7rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; margin:1.65rem 0 .45rem; }
      .side-rule { border-top:1px solid #dce5f2; margin:1.1rem 0 .8rem; }
      .page-caption { color:#536989; font-size:.93rem; margin-bottom:1.25rem; }
      .context-caption { color:#536989; font-size:.82rem; }
      .assistant-welcome { max-width:570px; margin:auto; }
      .assistant-welcome h2 { margin-bottom:.45rem !important; }
      .assistant-welcome p { color:#536989; font-size:1rem; line-height:1.55; }
      .source-excerpt { background:#eef5fe; border-left:3px solid #005ad2; color:#102b59; padding:.72rem .85rem; margin:.55rem 0; font-size:.85rem; line-height:1.45; }
      .reviewer-note { background:#f7f9fd; border-left:3px solid #005ad2; color:#102b59; padding:.75rem .85rem; margin:.7rem 0; font-size:.9rem; line-height:1.48; }
      .section-divider { border-top:1px solid #dce5f2; margin:1rem 0 1.15rem; }
      .stage { border-left:3px solid #dce5f2; padding:.7rem 0 .7rem .9rem; margin:.15rem 0; }
      .stage.current { border-left-color:#005ad2; background:#f4f8fe; }
      .stage-title { color:#001965; font-weight:700; font-size:.95rem; }
      .stage-copy { color:#536989; font-size:.84rem; line-height:1.4; margin-top:.15rem; }
      .matrix-status { color:#001965; font-weight:700; font-size:.8rem; text-transform:uppercase; letter-spacing:.02em; }
      .user-footer { border-top:1px solid #dce5f2; padding-top:.8rem; }
      .user-row { display:flex; align-items:center; gap:.65rem; color:#102b59; }
      .user-avatar { width:28px; height:28px; border-radius:50%; display:grid; place-items:center; background:#005ad2; color:#fff; font-size:.72rem; font-weight:700; }
      .user-name { font-size:.86rem; font-weight:700; line-height:1.15; }
      .user-role { color:#64738c; font-size:.73rem; margin-top:.12rem; }
      [data-testid="stSidebar"] .stRadio label { color:#203a68 !important; font-weight:650; padding:.4rem .2rem; }
      [data-testid="stSidebar"] .stRadio label:has(input:checked) { color:#001965 !important; }
      [data-testid="stSidebar"] .stButton button { text-align:left; border:0; background:transparent; color:#203a68; padding-left:.2rem; }
      [data-testid="stSidebar"] .stButton button:hover { background:#e8f0fb; color:#001965; }
      [data-testid="stDataFrame"] { border:1px solid #dce5f2; }
      [data-testid="stChatMessage"] { border-radius:0; }
      [data-testid="stChatInput"] textarea { border-color:#5e79ac !important; }
      [data-testid="stExpander"] { border-color:#dce5f2 !important; border-radius:3px; }
      .stButton button, [data-testid="stFormSubmitButton"] button { border-radius:4px; font-weight:650; }
    </style>
    """,
    unsafe_allow_html=True,
)


def api(method: str, path: str, *, show_errors: bool = True, **kwargs):
    """Call the local review API and preserve the optional authentication boundary."""
    headers = kwargs.pop("headers", {})
    if token := st.session_state.get("access_token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(method, f"{API_BASE_URL}{path}", headers=headers, timeout=120, **kwargs)
    except requests.RequestException as exc:
        if show_errors:
            st.error(f"The review service is unavailable: {exc}")
        return None
    if response.ok:
        return response.json()
    detail = response.text
    try:
        detail = response.json().get("detail", detail)
    except ValueError:
        pass
    if response.status_code == 401:
        st.session_state.pop("access_token", None)
        st.session_state.pop("user", None)
    if show_errors:
        st.error(f"{response.status_code}: {detail}")
    return None


def api_bytes(path: str) -> bytes | None:
    """Fetch a protected visual-evidence asset for inline review."""
    headers = {}
    if token := st.session_state.get("access_token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(f"{API_BASE_URL}{path}", headers=headers, timeout=120)
    except requests.RequestException as exc:
        st.error(f"Visual evidence could not be loaded: {exc}")
        return None
    if not response.ok:
        st.error(f"{response.status_code}: visual evidence could not be loaded")
        return None
    return response.content


@st.fragment(run_every="2s")
def render_live_analysis_progress(analysis_run_id: str) -> None:
    """Poll durable item states while Redis workers process the review."""
    progress = api("GET", f"/analysis-runs/{analysis_run_id}/progress", show_errors=False)
    if not progress:
        st.warning("Analysis progress is temporarily unavailable. It will resume when the service reconnects.")
        return

    total = progress["total_items"]
    completed_or_failed = progress["completed_items"] + progress["failed_items"]
    ratio = completed_or_failed / total if total else 0.0
    st.progress(ratio, text=(
        f"Run {progress['status']} · {completed_or_failed}/{total} items settled · "
        f"{progress['running_items']} active · {progress['failed_items']} failed"
    ))
    rows = [
        {
            "Requirement": item["requirement_code"],
            "Status": item["status"].replace("_", " ").title(),
            "Attempts": item["attempt_count"],
            "Last error": item["error_message"] or "",
        }
        for item in progress["items"]
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    findings = api("GET", f"/analysis-runs/{analysis_run_id}/findings", show_errors=False) or []
    if findings:
        st.caption(f"{len(findings)} candidate findings are ready for review.")

    if progress["status"] == "failed":
        st.error(progress["error_message"] or "One or more items failed.")
        if st.button("Retry failed items", key=f"retry_analysis_{analysis_run_id}"):
            retried = api("POST", f"/analysis-runs/{analysis_run_id}/retry")
            if retried:
                st.rerun()
    elif progress["status"] == "completed":
        st.session_state.active_analysis_run_id = None
        st.rerun()


def authenticate() -> dict:
    config = api("GET", "/auth/config", show_errors=False)
    if not config:
        st.error("Authentication service is unavailable. Check the backend connection and reload.")
        st.stop()
    st.session_state.visual_analysis_enabled = config["visual_analysis_enabled"]
    if config["authentication_required"] and not st.session_state.get("access_token"):
        # Streamlit widgets can only be assigned before they are rendered. A
        # successful registration sets these pending values and reruns once so
        # the user lands in the sign-in view with their email ready.
        next_auth_mode = st.session_state.pop("next_auth_mode", None)
        if next_auth_mode:
            st.session_state.auth_mode = next_auth_mode
        login_email = st.session_state.pop("next_login_email", None)
        if login_email:
            st.session_state.login_email = login_email
        _, login, _ = st.columns([1, 1.1, 1])
        with login:
            st.title("Sign in")
            st.caption("Warehouse Automation Design Review Assistant")
            mode = st.radio(
                "Authentication mode",
                ["Sign in", "Create account"],
                horizontal=True,
                label_visibility="collapsed",
                key="auth_mode",
            )
            if mode == "Sign in":
                registration_notice = st.session_state.pop("registration_notice", None)
                if registration_notice:
                    st.success(registration_notice)
                with st.form("login"):
                    email = st.text_input("Email", key="login_email")
                    password = st.text_input("Password", type="password")
                    submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
                if submitted:
                    result = api("POST", "/auth/login", json={"email": email, "password": password})
                    if result:
                        st.session_state.access_token = result["access_token"]
                        st.session_state.user = result["user"]
                        st.rerun()
            else:
                if not config["self_registration_enabled"]:
                    st.info("Account registration is managed by a workspace administrator.")
                else:
                    with st.form("registration"):
                        display_name = st.text_input("Name")
                        email = st.text_input("Work email")
                        password = st.text_input("Password", type="password", help="Use at least 12 characters.")
                        confirm_password = st.text_input("Confirm password", type="password")
                        submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
                    if submitted:
                        if password != confirm_password:
                            st.error("The passwords do not match.")
                        else:
                            registered = api("POST", "/auth/register", json={"display_name": display_name, "email": email, "password": password})
                            if registered:
                                st.session_state.next_auth_mode = "Sign in"
                                st.session_state.next_login_email = registered["email"]
                                st.session_state.registration_notice = "Account created. Sign in to continue."
                                st.rerun()
        st.stop()
    user = api("GET", "/auth/me", show_errors=False)
    if not user:
        st.error("Your session could not be verified. Please reload and sign in again.")
        st.stop()
    return user


def load_workspace() -> tuple[list[dict], list[dict], list[dict]]:
    return (
        api("GET", "/documents") or [],
        api("GET", "/requirement-baselines") or [],
        api("GET", "/review-packages") or [],
    )


def review_label(review: dict) -> str:
    return f"{review['name']} · {review['requirement_count']} requirements"


def source_location(item: dict) -> str:
    section = item.get("section") or "Source location not detected"
    return f"{section} · source unit {item['page']}" if item.get("page") else section


def set_view(view: str) -> None:
    st.session_state.active_view = view
    st.rerun()


def scope_selector(reviews: list[dict], key: str) -> str | None:
    if not reviews:
        st.selectbox("Review package", ["No review package available"], disabled=True, key=key)
        return None
    by_id = {review["id"]: review for review in reviews}
    current = st.session_state.get("active_review_id")
    if current not in by_id:
        current = next(iter(by_id))
    selected = st.selectbox(
        "Review package",
        list(by_id),
        index=list(by_id).index(current),
        format_func=lambda item: review_label(by_id[item]),
        key=key,
    )
    st.session_state.active_review_id = selected
    return selected


def render_assistant(reviews: list[dict]) -> None:
    heading, scope = st.columns([2.2, 1.4], vertical_alignment="bottom")
    with heading:
        st.title("Design Assistant")
        st.markdown("<div class='page-caption'>Ask in Chinese or English. Answers are returned in English and grounded in the active review package.</div>", unsafe_allow_html=True)
    with scope:
        active_review_id = scope_selector(reviews, "assistant_scope")
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    conversation, inspector = st.columns([3.1, 1.25], gap="large")
    with conversation:
        if not active_review_id:
            with st.container(height=490, vertical_alignment="center"):
                st.markdown(
                    """<div class='assistant-welcome'><h2>Start a controlled design review</h2>
                    <p>Import the approved URS/ES baseline, add the supplier FS/DS versions, then create a frozen review package. The assistant will only search within that selected scope.</p></div>""",
                    unsafe_allow_html=True,
                )
                actions = st.columns(3)
                if actions[0].button("1. Import URS / ES", type="primary", use_container_width=True):
                    set_view("Knowledge & Requirements")
                if actions[1].button("2. Add FS / DS", use_container_width=True):
                    set_view("Knowledge & Requirements")
                if actions[2].button("3. Create package", use_container_width=True):
                    set_view("Knowledge & Requirements")
            st.chat_input("Create a review package to enable evidence chat", disabled=True)
        else:
            with st.container(height=490):
                if context := st.session_state.pop("assistant_context", None):
                    st.info(context)
                if not st.session_state.chat_messages:
                    with st.chat_message("assistant"):
                        st.write("What would you like to review in the selected supplier design versions?")
                for message in st.session_state.chat_messages:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])
            prompt = st.chat_input("Ask about the selected FS/DS versions")
            if prompt:
                st.session_state.chat_messages.append({"role": "user", "content": prompt})
                with st.spinner("Retrieving evidence from the active review scope..."):
                    response = api("POST", "/design-review/chat", json={"question": prompt, "review_package_id": active_review_id})
                if response:
                    st.session_state.chat_messages.append(
                        {"role": "assistant", "content": response["answer"], "citations": response["citations"], "limitations": response["limitations"]}
                    )
                    st.session_state.latest_citations = response["citations"]
                    st.rerun()
    with inspector:
        st.subheader("Answer inspector")
        citations = st.session_state.get("latest_citations", [])
        if not citations:
            st.markdown("<div class='reviewer-note'>Cited source excerpts will appear here after an answer. Selecting a finding in Design Review will keep its evidence in context.</div>", unsafe_allow_html=True)
        else:
            st.caption(f"Citations from the latest answer · {len(citations)} source excerpts")
            for citation in citations:
                st.markdown(
                    f"<div class='source-excerpt'><b>{citation['document_title']} · v{citation['version']}</b><br>{source_location(citation)}<br><br>{citation['excerpt']}</div>",
                    unsafe_allow_html=True,
                )
        if st.button("Open Design Review", use_container_width=True, disabled=not active_review_id):
            set_view("Design Review")


def render_knowledge(documents: list[dict], baselines: list[dict], reviews: list[dict]) -> None:
    st.title("Knowledge & Requirements")
    st.markdown("<div class='page-caption'>Build a review scope in sequence: controlled URS/ES reference baseline first, supplier FS/DS versions second, then one frozen Review Package.</div>", unsafe_allow_html=True)
    left, right = st.columns([1.1, 2.3], gap="large")
    with left:
        st.markdown("<div class='stage current'><div class='stage-title'>1 · URS / ES baseline</div><div class='stage-copy'>Import the controlled requirement table. A Quality Document Library connector will replace local upload in production.</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='stage'><div class='stage-title'>2 · Supplier FS / DS</div><div class='stage-copy'>Register, parse and inspect supplier document versions.</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='stage'><div class='stage-title'>3 · Review Package</div><div class='stage-copy'>Freeze the baseline and selected FS/DS versions.</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='side-rule'></div>", unsafe_allow_html=True)
        st.metric("Requirement baselines", len(baselines))
        st.metric("Supplier document versions", len(documents))
        st.metric("Review packages", len(reviews))
    with right:
        st.subheader("1. Import requirement baseline")
        with st.expander("Import URS / ES baseline", expanded=not baselines):
            st.caption("Upload the controlled URS or ES table. Each row becomes a traceable requirement; no empty baseline is created first.")
            with st.form("baseline_import_form"):
                requirement_csv = st.file_uploader(
                    "URS / ES requirement table",
                    type=["csv"],
                    help="Required: 序号, 系统, Requirement, Reasonal/Impact, 是否critical. English headers are accepted too.",
                )
                name = st.text_input("Document label (optional)", placeholder="Derived from the uploaded file name")
                fallback_system = st.text_input("Fallback system (optional)", placeholder="Only needed if the table has no 系统 / System column")
                imported = st.form_submit_button("Import URS / ES baseline", type="primary", use_container_width=True)
            if imported:
                if requirement_csv is None:
                    st.warning("Select the controlled URS or ES requirement table first.")
                else:
                    result = api(
                        "POST",
                        "/requirement-baselines/import",
                        files={"file": (requirement_csv.name, requirement_csv.getvalue(), "text/csv")},
                        data={"name": name, "system": fallback_system},
                    )
                    if result:
                        st.success(f"Imported {result['imported_count']} requirements into {result['baseline']['name']}.")
                        st.rerun()
        if baselines:
            st.dataframe(pd.DataFrame([{"Baseline": b["name"], "System": b["system"], "Context": b["description"]} for b in baselines]), use_container_width=True, hide_index=True)

        st.subheader("2. Add supplier design versions")
        with st.expander("Add FS / DS source", expanded=bool(baselines) and not documents):
            with st.form("document_register"):
                first, second = st.columns(2)
                with first:
                    title = st.text_input("Document title", placeholder="Fleet Manager Functional Specification")
                    doc_type = st.selectbox("Document type", DOCUMENT_TYPES, index=2)
                    vendor = st.text_input("Supplier", placeholder="Supplier name")
                with second:
                    version = st.text_input("Version", placeholder="1.0")
                    source_url = st.text_input("Controlled repository URL", placeholder="Optional")
                    previous = {"": "No prior version"}
                    previous.update({item["id"]: f"{item['title']} · v{item['version']}" for item in documents})
                    supersedes = st.selectbox("Replaces version", list(previous), format_func=lambda item: previous[item])
                source_file = st.file_uploader("Source file", type=["pdf", "docx", "csv"])
                register = st.form_submit_button("Register and parse", type="primary")
            if register:
                if not all([title, version, source_file]):
                    st.warning("Document title, version, and source file are required.")
                else:
                    created = api("POST", "/documents", json={"title": title, "document_type": doc_type, "system": "fleet_manager_wcs", "vendor": vendor or None, "version": version, "status": "draft", "source_url": source_url or None, "file_name": source_file.name, "supersedes_version_id": supersedes or None})
                    if created:
                        suffix = Path(source_file.name).suffix.lower()
                        parsed = api("POST", f"/documents/{created['id']}/upload", files={"file": (source_file.name, source_file.getvalue(), MIME_TYPES[suffix])})
                        if parsed:
                            st.success(f"Parsed {parsed['chunk_count']} citable chunks. Inspect before indexing.")
                            st.rerun()
        if documents:
            st.dataframe(pd.DataFrame([{"Document": d["title"], "Type": d["document_type"], "Version": d["version"], "Parse status": d["ingestion_status"], "Chunks": d["chunk_count"]} for d in documents]), use_container_width=True, hide_index=True)
            document_by_id = {item["id"]: item for item in documents}
            document_id = st.selectbox("Inspect document version", list(document_by_id), format_func=lambda item: f"{document_by_id[item]['title']} · v{document_by_id[item]['version']}")
            visual_analysis_enabled = st.session_state.get("visual_analysis_enabled", False)
            actions = st.columns(4 if visual_analysis_enabled else 3)
            inspect, visual, index = actions[0], actions[1], actions[-1]
            if inspect.button("Preview parsed structure", use_container_width=True):
                chunks = api("GET", f"/documents/{document_id}/chunks")
                if chunks:
                    for chunk in chunks[:8]:
                        st.markdown(f"<div class='source-excerpt'><b>{source_location(chunk)}</b><br>{chunk['content']}</div>", unsafe_allow_html=True)
            if visual.button("View visual source pages", use_container_width=True):
                st.session_state.visual_preview_document_id = document_id
            if visual_analysis_enabled and actions[2].button("Analyse visual evidence", use_container_width=True):
                figures = api("POST", f"/documents/{document_id}/figures/analyse")
                if figures:
                    analysed = sum(item["analysis_status"] == "analysed" for item in figures)
                    st.success(f"Created {analysed} candidate visual evidence records. Re-index this document to include them in retrieval.")
            if index.button("Create retrieval index", type="primary", use_container_width=True):
                indexed = api("POST", f"/documents/{document_id}/index")
                if indexed:
                    st.success(f"Indexed {indexed['chunk_count']} chunks.")
            if st.session_state.get("visual_preview_document_id") == document_id:
                figures = api("GET", f"/documents/{document_id}/figures")
                if not figures:
                    st.info("No candidate visual pages were found. Text extraction remains available for this document.")
                else:
                    st.caption("Rendered source pages are preserved as evidence. Visual content is not automatically interpreted or included in retrieval.")
                    for figure in figures:
                        with st.expander(f"Page {figure['page']} · {figure['analysis_status'].replace('_', ' ').title()}"):
                            asset = api_bytes(f"/documents/{document_id}/figures/{figure['id']}/asset")
                            if asset:
                                st.image(asset, caption=f"Source page {figure['page']}", use_container_width=True)
                            if visual_analysis_enabled and figure["candidate_description"]:
                                st.markdown(f"<div class='source-excerpt'><b>Candidate interpretation</b><br>{figure['candidate_description']}</div>", unsafe_allow_html=True)
                                if figure["candidate_relationships"]:
                                    st.markdown("\n".join(f"- {item}" for item in figure["candidate_relationships"]))
                            else:
                                st.caption("This page is retained for engineering review; visual details are not automatically interpreted.")
                            if visual_analysis_enabled and figure["analysis_error"]:
                                st.caption(f"Reviewer note: {figure['analysis_error']}")

        st.subheader("3. Create Review Package")
        eligible = [d for d in documents if d["document_type"] in {"FS", "DS"} and d["ingestion_status"] in {"parsed_pending_index", "indexed"}]
        if baselines and eligible:
            with st.expander("Create frozen review scope", expanded=not reviews):
                baseline_by_id = {item["id"]: item for item in baselines}
                document_by_id = {item["id"]: item for item in eligible}
                with st.form("review_package_form"):
                    name = st.text_input("Review package name", placeholder="DR-001 Fleet Manager")
                    baseline_id = st.selectbox("Requirement baseline", list(baseline_by_id), format_func=lambda item: baseline_by_id[item]["name"])
                    document_ids = st.multiselect("Supplier FS / DS versions", list(document_by_id), format_func=lambda item: f"{document_by_id[item]['title']} · v{document_by_id[item]['version']}")
                    create = st.form_submit_button("Create Review Package", type="primary")
                if create:
                    package = api("POST", "/review-packages", json={"name": name, "system": "fleet_manager_wcs", "requirement_baseline_id": baseline_id, "design_document_version_ids": document_ids})
                    if package:
                        st.session_state.active_review_id = package["id"]
                        st.success("Review Package created. It is ready for Design Review.")
                        st.rerun()
        elif baselines:
            st.info("Add and parse at least one supplier FS or DS version to create a Review Package.")


def render_review(reviews: list[dict]) -> None:
    heading, scope = st.columns([2.2, 1.4], vertical_alignment="bottom")
    with heading:
        st.title("Design Review")
        st.markdown("<div class='page-caption'>Candidate findings support engineering review. They are not approval, compliance, or release decisions.</div>", unsafe_allow_html=True)
    with scope:
        active_review_id = scope_selector(reviews, "review_scope")
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
    if not active_review_id:
        st.info("Create a Review Package in Knowledge & Requirements before starting a Design Review.")
        if st.button("Open Knowledge & Requirements", type="primary"):
            set_view("Knowledge & Requirements")
        return

    selected = next(review for review in reviews if review["id"] == active_review_id)
    matrix, inspector = st.columns([3.15, 1.25], gap="large")
    with matrix:
        upper, action = st.columns([2.3, 1])
        upper.metric("Frozen requirements", selected["requirement_count"])
        action.metric("Selected supplier versions", len(selected["design_document_version_ids"]))
        if st.button("Generate candidate findings", type="primary"):
            run = api("POST", f"/review-packages/{active_review_id}/analyses")
            if run:
                st.session_state.last_analysis_id = run["id"]
                st.session_state.active_analysis_run_id = run["id"]
                st.rerun()
        active_run_id = st.session_state.get("active_analysis_run_id")
        if active_run_id:
            render_live_analysis_progress(active_run_id)
        else:
            analysis_id = st.session_state.get("last_analysis_id")
            findings = api("GET", f"/analysis-runs/{analysis_id}/findings") if analysis_id else []
            if findings:
                rows = [{"Requirement": f["requirement_code"], "Assessment": f["design_status"].replace("_", " ").title(), "Evidence summary": f["rationale"], "Reviewer action": f["suggested_reviewer_action"]} for f in findings]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                by_requirement = {f["requirement_code"]: f for f in findings}
                selected_code = st.selectbox("Selected finding", list(by_requirement), key="selected_finding")
                st.session_state.selected_finding = by_requirement[selected_code]
            else:
                st.markdown("<div class='reviewer-note'>Run the candidate-finding analysis to populate the coverage matrix for this frozen review scope.</div>", unsafe_allow_html=True)
    with inspector:
        st.subheader("Finding inspector")
        finding = st.session_state.get("selected_finding")
        if not finding:
            st.markdown("<div class='reviewer-note'>Select a Matrix row to view the reviewer action and source excerpts here.</div>", unsafe_allow_html=True)
            return
        st.markdown(f"<div class='matrix-status'>{finding['design_status'].replace('_', ' ')}</div>", unsafe_allow_html=True)
        st.markdown(f"**{finding['requirement_code']}**")
        st.write(finding["rationale"])
        if finding["gap"]:
            st.caption(f"Potential gap: {finding['gap']}")
        if finding["suggested_reviewer_action"]:
            st.markdown(f"**Reviewer action**  \n{finding['suggested_reviewer_action']}")
        st.caption("Citations")
        for evidence in finding["evidence"]:
            st.markdown(f"<div class='source-excerpt'><b>{evidence['document_title']} · v{evidence['version']}</b><br>{source_location(evidence)}<br><br>{evidence['excerpt']}</div>", unsafe_allow_html=True)
        if st.button("Discuss this requirement", type="primary", use_container_width=True):
            st.session_state.assistant_context = f"Discuss {finding['requirement_code']}: explain its evidence and the remaining reviewer question."
            st.session_state.latest_citations = finding["evidence"]
            set_view("Design Assistant")


def render_sidebar(user: dict) -> None:
    with st.sidebar:
        st.markdown("<div class='workspace-name'>Warehouse Automation<br><span>Design Review Assistant</span></div>", unsafe_allow_html=True)
        st.caption(SYSTEM_NAME)
        st.markdown("<div class='side-label'>Workspace</div>", unsafe_allow_html=True)
        choice = st.radio("Workspace navigation", NAV_ITEMS, index=NAV_ITEMS.index(st.session_state.active_view), label_visibility="collapsed")
        if choice != st.session_state.active_view:
            st.session_state.active_view = choice
            st.rerun()
        st.markdown("<div style='height:355px'></div>", unsafe_allow_html=True)
        st.markdown("<div class='user-footer'></div>", unsafe_allow_html=True)
        initials = "".join(part[0] for part in user["display_name"].split()[:2]).upper()
        st.markdown(
            f"<div class='user-row'><div class='user-avatar'>{initials}</div><div><div class='user-name'>{user['display_name']}</div><div class='user-role'>{user['role']}</div></div></div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("access_token") and st.button("Sign out", use_container_width=True):
            st.session_state.pop("access_token", None)
            st.session_state.pop("user", None)
            st.rerun()


if "active_view" not in st.session_state:
    st.session_state.active_view = "Design Assistant"
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

user = authenticate()
documents, baselines, reviews = load_workspace()
render_sidebar(user)

if st.session_state.active_view == "Design Assistant":
    render_assistant(reviews)
elif st.session_state.active_view == "Design Review":
    render_review(reviews)
else:
    render_knowledge(documents, baselines, reviews)
