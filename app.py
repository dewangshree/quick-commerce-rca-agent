import streamlit as st
import requests

API_URL = "http://localhost:8000/chat"

st.set_page_config(page_title="QC RCA Agent", page_icon="🚴", layout="wide")

st.title("🚴 Quick-Commerce RCA Agent")
st.caption("Ask about store/city performance, OR2A breaches, and root causes — 2026-04-22")

# Session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Sidebar with sample questions
with st.sidebar:
    st.header("Sample Questions")
    samples = [
        "How did Bangalore do on 2026-04-22?",
        "Why did STORE_101 underperform that day?",
        "Walk me through the morning hours at STORE_101.",
        "What about STORE_102?",
        "How did STORE_103 do?",
        "Which stores had the worst breach rates?",
        "What is OR2A?",
    ]
    for q in samples:
        if st.button(q, use_container_width=True):
            st.session_state.pending_input = q

    st.divider()
    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Handle pre-filled input from sidebar buttons
pending = st.session_state.pop("pending_input", None)

# Chat input
user_input = st.chat_input("Ask about store performance or RCA...") or pending

if user_input and user_input.strip():
    # Add user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Call API
    with st.chat_message("assistant"):
        with st.spinner("Analyzing..."):
            try:
                resp = requests.post(
                    API_URL,
                    json={"messages": st.session_state.messages},
                    timeout=60,
                )
                if resp.status_code == 200:
                    reply = resp.json()["reply"]
                else:
                    reply = f"⚠️ API error {resp.status_code}: {resp.text}"
            except requests.exceptions.ConnectionError:
                reply = "⚠️ Cannot connect to backend. Make sure the FastAPI server is running on port 8000.\n\nRun: `python main.py`"
            except Exception as e:
                reply = f"⚠️ Error: {str(e)}"

        st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
