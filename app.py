import os
import io
import json
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- Konfiguracja ---
DRIVE_FILE_NAME = "historia_czatu_drive.txt"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# --- NOWA, PROSTSZA FUNKCJA LOGOWANIA DLA "ROBOTA" ---
@st.cache_resource
def get_drive_service():
    try:
        # Ładujemy dane z sekretów Streamlit
        creds_json_str = st.secrets["GCP_CREDENTIALS"]
        creds_info = json.loads(creds_json_str)
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Błąd podczas łączenia z Google Drive przez Service Account: {e}")
        return None

# --- Funkcje do obsługi plików (bez większych zmian) ---
def get_file_id(service, file_name):
    query = f"name='{file_name}' and trashed=false"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0].get('id') if files else None

def download_history(service, file_id):
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode('utf-8')
    except HttpError:
        return ""

def upload_history(service, file_id, file_name, content):
    media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain', resumable=True)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        file_metadata = {'name': file_name}
        response = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        st.session_state.file_id = response.get('id')

# --- Główna logika aplikacji Streamlit ---
st.set_page_config(page_title="Gemini z Pamięcią", page_icon="🧠")
st.title("🧠 Gemini z Pamięcią")
st.caption("Twoja prywatna rozmowa z AI, zapisywana na Twoim Dysku Google.")

try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"Błąd konfiguracji Gemini API. Sprawdź swój klucz w Secrets. Błąd: {e}")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "history_loaded" not in st.session_state:
    with st.spinner("Łączenie i wczytywanie pamięci z Dysku Google..."):
        drive_service = get_drive_service()
        if drive_service:
            st.session_state.drive_service = drive_service
            file_id = get_file_id(drive_service, DRIVE_FILE_NAME)
            st.session_state.file_id = file_id
            if file_id:
                history_text = download_history(drive_service, file_id)
                if history_text:
                    turns = history_text.strip().split('\n\n\n')
                    for turn in turns:
                        if 'Ty:' in turn and 'Gemini:' in turn:
                            user_part = turn.split('Ty:')[1].split('Gemini:')[0].strip()
                            model_part = turn.split('Gemini:')[1].strip()
                            st.session_state.messages.append({"role": "user", "content": user_part})
                            st.session_state.messages.append({"role": "assistant", "content": model_part})
            st.success("Pamięć połączona z Dyskiem Google!")
        else:
            st.error("Nie udało się połączyć z usługą Dysku Google.")
            st.stop()
    st.session_state.history_loaded = True

if "gemini_chat" not in st.session_state:
    model = genai.GenerativeModel('gemini-1.5-flash')
    gemini_history = [{'role': 'user' if msg['role'] == 'user' else 'model', 'parts': [msg['content']]} for msg in st.session_state.messages]
    st.session_state.gemini_chat = model.start_chat(history=gemini_history)

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Napisz coś..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Myślę..."):
            try:
                response = st.session_state.gemini_chat.send_message(prompt)
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                full_history_text = ""
                user_msg, assistant_msg = None, None
                for msg in st.session_state.messages:
                    if msg["role"] == "user": user_msg = msg["content"]
                    elif msg["role"] == "assistant":
                        assistant_msg = msg["content"]
                        full_history_text += f"Ty: {user_msg}\n\nGemini: {assistant_msg}\n\n\n"
                upload_history(st.session_state.drive_service, st.session_state.file_id, DRIVE_FILE_NAME, full_history_text)
                if not st.session_state.file_id:
                     st.session_state.file_id = get_file_id(st.session_state.drive_service, DRIVE_FILE_NAME)
            except Exception as e: st.error(f"Wystąpił błąd: {e}")