"""
Warehouse Automation Design Decision Assistant - Frontend
Professional Streamlit UI with project context form and design assessment display
"""
import os
import uuid
import streamlit as st
import requests

# Configuration
API_BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat").replace("/chat", "")
CHAT_API_URL = f"{API_BASE_URL}/chat"

# Page config MUST be first Streamlit command
st.set_page_config(
    page_title="Warehouse Automation Design Assistant",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for professional look
st.markdown("""
<style>
    /* Main header styling */
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1f77b4;
        text-align: center;
        padding: 0.8rem 0;
        border-bottom: 3px solid #1f77b4;
        margin-bottom: 1rem;
    }

    /* Info card styling */
    .info-card {
        background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
        padding: 1.2rem;
        border-radius: 10px;
        margin: 0.8rem 0;
        border-left: 4px solid #1f77b4;
    }

    /* Assessment card styling */
    .assessment-card {
        background: #fff;
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin: 0.5rem 0;
    }

    /* Recommendation badges */
    .badge-proceed {
        background: #28a745;
        color: white;
        padding: 0.3rem 0.8rem;
        border-radius: 15px;
        font-weight: 600;
    }
    .badge-caution {
        background: #ffc107;
        color: #333;
        padding: 0.3rem 0.8rem;
        border-radius: 15px;
        font-weight: 600;
    }
    .badge-rethink {
        background: #dc3545;
        color: white;
        padding: 0.3rem 0.8rem;
        border-radius: 15px;
        font-weight: 600;
    }

    /* Confidence meter */
    .confidence-high { color: #28a745; font-weight: 600; }
    .confidence-medium { color: #ffc107; font-weight: 600; }
    .confidence-low { color: #dc3545; font-weight: 600; }

    /* Sidebar styling */
    .sidebar-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #333;
        margin-bottom: 0.5rem;
    }

    /* Source citation */
    .source-citation {
        font-size: 0.85rem;
        color: #666;
        background: #f8f9fa;
        padding: 0.5rem;
        border-radius: 5px;
        margin-top: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]
if "web_search_count" not in st.session_state:
    st.session_state.web_search_count = 0
if "project_context" not in st.session_state:
    st.session_state.project_context = {}

# ============== SIDEBAR: Project Configuration ==============
with st.sidebar:
    st.markdown('<p class="sidebar-header">📋 Project Configuration</p>', unsafe_allow_html=True)
    st.markdown("---")

    # Project Context Form
    warehouse_type = st.selectbox(
        "🏗️ Warehouse Type",
        ["Greenfield", "Brownfield"],
        help="Greenfield: New construction | Brownfield: Retrofit existing facility"
    )

    throughput = st.text_input(
        "📦 Throughput Target",
        placeholder="e.g., 1000 orders/hour",
        help="Expected order volume per hour"
    )

    automation_scope = st.multiselect(
        "🤖 Automation Scope",
        ["AGV", "Conveyor", "ASRS", "Shuttle", "Sorter", "Pick Station"],
        help="Select automation technologies being considered"
    )

    regulatory = st.multiselect(
        "⚖️ Regulatory Constraints",
        ["GxP/FDA", "ISO Standards", "Safety (EN/OSHA)", "Cold Chain", "Hazmat", "None"],
        help="Compliance requirements for your facility"
    )

    integration = st.radio(
        "🔌 Integration Complexity",
        ["Low", "Medium", "High"],
        help="How complex is integration with existing systems?",
        horizontal=True
    )

    budget_range = st.selectbox(
        "💰 Budget Range $",
        ["< 1M", "1M - 5M", "5M - 10M", "10M - 50M", "> 50M", "Not specified"],
        help="Approximate project budget"
    )

    timeline = st.selectbox(
        "📅 Timeline",
        ["< 6 months", "6-12 months", "12-24 months", "> 24 months", "Not specified"],
        help="Expected implementation timeline"
    )

    # Update project context
    st.session_state.project_context = {
        "warehouse_type": warehouse_type.lower(),
        "throughput_target": throughput,
        "automation_scope": automation_scope,
        "regulatory_constraints": regulatory,
        "integration_complexity": integration.lower(),
        "budget_range": budget_range,
        "timeline": timeline
    }

    st.markdown("---")

    # Web Search Usage (Free Tier)
    remaining_searches = 5 - st.session_state.web_search_count
    if remaining_searches > 2:
        st.info(f"🌐 Web searches remaining: {remaining_searches}/5")
    elif remaining_searches > 0:
        st.warning(f"🌐 Web searches remaining: {remaining_searches}/5")
    else:
        st.error("🌐 Web search limit reached (0/5)")

    st.markdown("---")

    # Session Management
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Reset Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.web_search_count = 0
            st.session_state.session_id = str(uuid.uuid4())[:8]
            st.rerun()
    with col2:
        if st.button("📋 Clear Form", use_container_width=True):
            st.session_state.project_context = {}
            st.rerun()

    st.markdown("---")
    st.caption(f"Session ID: {st.session_state.session_id}")

# ============== MAIN CONTENT AREA ==============
st.markdown('<h1 class="main-header">🏭 Warehouse Automation Design Assistant</h1>', unsafe_allow_html=True)

# Two-column layout
col_chat, col_summary = st.columns([2, 1])

with col_chat:
    st.subheader("💬 Design Discussion")

    # Chat container with fixed height for scrolling
    chat_container = st.container()

    with chat_container:
        # Display chat history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

                # Show sources and confidence if available (for assistant messages)
                if message["role"] == "assistant" and "metadata" in message:
                    metadata = message["metadata"]

                    # Confidence score
                    if "confidence_score" in metadata and metadata["confidence_score"]:
                        score = metadata["confidence_score"]
                        if score >= 0.8:
                            conf_class = "confidence-high"
                            conf_label = "High"
                        elif score >= 0.5:
                            conf_class = "confidence-medium"
                            conf_label = "Medium"
                        else:
                            conf_class = "confidence-low"
                            conf_label = "Low"
                        st.markdown(f'<span class="{conf_class}">Confidence: {conf_label} ({score:.0%})</span>', unsafe_allow_html=True)

                    # Sources
                    if "sources" in metadata and metadata["sources"]:
                        with st.expander("📚 View Sources"):
                            for source in metadata["sources"]:
                                st.markdown(f"- {source}")

    # Chat input
    if prompt := st.chat_input("Ask about warehouse automation design decisions..."):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Get AI response
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            message_placeholder.markdown("🔍 Analyzing your design question...")

            try:
                # Build payload with project context
                payload = {
                    "question": prompt,
                    "session_id": st.session_state.session_id
                }

                # Add project context to the question if available
                context = st.session_state.project_context
                if any(context.values()):
                    context_str = f"\n\n[Project Context: Type={context.get('warehouse_type', 'N/A')}, " \
                                  f"Scope={', '.join(context.get('automation_scope', [])) or 'N/A'}, " \
                                  f"Integration={context.get('integration_complexity', 'N/A')}, " \
                                  f"Regulatory={', '.join(context.get('regulatory_constraints', [])) or 'None'}]"
                    payload["question"] = prompt + context_str

                response = requests.post(CHAT_API_URL, json=payload, timeout=60)

                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "No answer found.")
                    sources = data.get("sources", [])
                    confidence = data.get("confidence_score")
                    retrieved_chunks = data.get("retrieved_chunks", 0)
                    web_search_used = data.get("web_search_used", False)
                    web_searches_remaining = data.get("web_searches_remaining", 5)

                    # Update web search count in session state
                    if web_search_used:
                        st.session_state.web_search_count = 5 - web_searches_remaining

                    # Display the answer
                    message_placeholder.markdown(answer)

                    # Show web search indicator if used
                    if web_search_used:
                        st.info("🌐 Web search was used to enhance this response")

                    # Show confidence
                    if confidence:
                        if confidence >= 0.8:
                            conf_class = "confidence-high"
                            conf_label = "High"
                        elif confidence >= 0.5:
                            conf_class = "confidence-medium"
                            conf_label = "Medium"
                        else:
                            conf_class = "confidence-low"
                            conf_label = "Low"
                        st.markdown(f'<span class="{conf_class}">Confidence: {conf_label} ({confidence:.0%})</span>', unsafe_allow_html=True)

                    # Show sources
                    if sources:
                        with st.expander("📚 View Sources"):
                            for source in sources:
                                st.markdown(f"- {source}")

                    # Save to history with metadata
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "metadata": {
                            "sources": sources,
                            "confidence_score": confidence,
                            "retrieved_chunks": retrieved_chunks,
                            "web_search_used": web_search_used
                        }
                    })
                else:
                    error_msg = f"⚠️ Error {response.status_code}: {response.text}"
                    message_placeholder.error(error_msg)

            except requests.exceptions.Timeout:
                message_placeholder.error("⏱️ Request timed out. Please try again.")
            except requests.exceptions.ConnectionError:
                message_placeholder.error("🔌 Cannot connect to backend. Is the server running?")
                st.info("💡 Try: `docker-compose up` or `uvicorn app.main:app --reload`")
            except Exception as e:
                message_placeholder.error(f"❌ Error: {str(e)}")

with col_summary:
    st.subheader("📊 Project Summary")

    # Display current project context
    context = st.session_state.project_context

    st.markdown('<div class="info-card">', unsafe_allow_html=True)

    st.markdown(f"**🏗️ Type:** {context.get('warehouse_type', 'Not set').title()}")
    st.markdown(f"**📦 Throughput:** {context.get('throughput_target') or 'Not set'}")

    scope = context.get('automation_scope', [])
    st.markdown(f"**🤖 Scope:** {', '.join(scope) if scope else 'Not set'}")

    regulatory = context.get('regulatory_constraints', [])
    st.markdown(f"**⚖️ Regulatory:** {', '.join(regulatory) if regulatory else 'None'}")

    st.markdown(f"**🔌 Integration:** {context.get('integration_complexity', 'Not set').title()}")
    st.markdown(f"**💰 Budget:** {context.get('budget_range', 'Not specified')}")
    st.markdown(f"**📅 Timeline:** {context.get('timeline', 'Not specified')}")

    st.markdown('</div>', unsafe_allow_html=True)

    # Design Tips
    st.subheader("💡 Design Tips")

    tips = []
    if context.get('warehouse_type') == 'brownfield':
        tips.append("🔧 **Brownfield projects** require careful assessment of existing infrastructure constraints.")
    if 'AGV' in context.get('automation_scope', []):
        tips.append("🚗 **AGVs** need floor conditions assessment and traffic management planning.")
    if 'ASRS' in context.get('automation_scope', []):
        tips.append("📦 **ASRS systems** require structural analysis and fire suppression planning.")
    if 'GxP/FDA' in context.get('regulatory_constraints', []):
        tips.append("💊 **GxP compliance** requires validation documentation and change control procedures.")
    if context.get('integration_complexity') == 'high':
        tips.append("⚠️ **High integration complexity** may need extended testing phases.")

    if tips:
        for tip in tips[:3]:  # Show max 3 tips
            st.markdown(tip)
    else:
        st.info("Configure your project in the sidebar to get tailored design tips.")

    # Quick Actions
    st.subheader("⚡ Quick Questions")

    quick_questions = [
        "What are the key risks with my automation scope?",
        "How should I prioritize implementation phases?",
        "What integration challenges should I expect?",
        "What validation steps are recommended?"
    ]

    for q in quick_questions:
        if st.button(q, key=f"quick_{q[:20]}", use_container_width=True):
            st.session_state.messages.append({"role": "user", "content": q})
            st.rerun()

# Footer
st.markdown("---")
st.caption("🏭 Warehouse Automation Design Decision Assistant | Powered by RAG + GPT-4o " \
            "| Please contract luhy0629@outlook.com if you have any questions")
