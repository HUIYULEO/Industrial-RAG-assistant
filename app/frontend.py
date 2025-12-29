# Frontend implementation with streamlit
import os
import streamlit as st
import requests

# config
# local API：ST_API_URL = "http://127.0.0.1:8000/chat"
ST_API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat")
st.set_page_config(page_title="Industrial AI assistang", page_icon="🤖")

# UI layout
st.title("🤖 Industrial Technical Assistant")
st.markdown("Ask questions about your **Warehouse control system** (Protocols, Functions).")

# chat interface
# initialize chat history in session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display previous message
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Input & logic
if prompt := st.chat_input("what is warehouse control system"):
    # Add user message to UI
    st.session_state.messages.append({"role": "user", "content":prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get AI Response
    with st.chat_message("assistant"):
        message_placeholder = st.empty() # create an empty container we can fill later
        message_placeholder.markdown("Thinking....") # temporary text

        try:
            # network call: this is the critical "Microservice" moment
            payload = {"question": prompt}
            response = requests.post(ST_API_URL, json=payload)

            if response.status_code == 200:
                # Extract the "answer" field from the JSON response
                answer = response.json().get("answer", "No answer found.")

                # Replace thinking with the actual answer
                message_placeholder.markdown(answer)

                # save the AI's reply to the memory so it stays on screen
                st.session_state.messages.append({"role":"assistant", "content": answer})
            else:
                # hanele 404/500 errors gracefully
                error_msg = f"Error {response.status_code}: {response.text}"
                message_placeholder.error(error_msg)
        
        except Exception as e:
            # handle connection errors
            message_placeholder.error(f"Connection Error: {e}")
            st.warning("Is your server running? ")