import streamlit as st


SESSION_KEY = "gemini_chat_messages"


def init_chat_state():
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = []


def add_user_message(text: str):
    st.session_state[SESSION_KEY].append({
        "role": "user",
        "content": text
    })


def add_assistant_message(text: str, meta=None):
    st.session_state[SESSION_KEY].append({
        "role": "assistant",
        "content": text,
        "meta": meta
    })


def clear_chat():
    st.session_state[SESSION_KEY] = []


def get_messages_for_api():
    messages = []
    for msg in st.session_state.get(SESSION_KEY, []):
        messages.append({
            "role": msg["role"],
            "parts": [msg["content"]]
        })
    return messages
