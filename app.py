import os
import io
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- Konfiguracja (bez zmian) ---
DRIVE_FILE_NAME = "historia_czatu_drive.txt"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# --- NOWA, OSTATECZNA FUNKCJA LOGOWANIA DLA "ROBOTA" (bez zmian) ---
@st.cache_resource
def get_drive_service():
    try:
        creds_info = {
            "type": st.secrets.gcp_service_account.type,
            "project_id": st.secrets.gcp_service_account.project_id,
            "private_key_id": st.secrets.gcp_service_account.private_key_id,
            "private_key": st.secrets.gcp_service_account.private_key.replace('\\n', '\n'),
            "client_email": st.secrets.gcp_service_account.client_email,
            "client_id": st.secrets.gcp_service_account.client_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": st.secrets.gcp_service_account.client_x509_cert_url,
            "universe_domain": "googleapis.com"
        }
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Błąd podczas łączenia z Google Drive: {e}")
        st.error("Sprawdź, czy wszystkie wartości w sekcji [gcp_service_account] w 'Secrets' są poprawnie wklejone.")
        return None

# Reszta funkcji bez zmian...
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
        # Jeśli plik nie istnieje lub jest inny błąd HTTP, zwróć pusty string
        # To jest kluczowe, aby funkcja `upload_history` mogła dodać nową treść
        return ""

def upload_history(service, file_id, file_name, new_content_to_append):
    """
    Funkcja aktualizuje historię czatu na Google Drive.
    Jeśli plik istnieje, pobiera jego zawartość, dopisuje nową treść i wysyła z powrotem.
    Jeśli plik nie istnieje, tworzy nowy z podaną treścią.
    """
    try:
        existing_content = ""
        if file_id:
            # 1. Pobierz istniejącą zawartość pliku
            existing_content = download_history(service, file_id)

        # 2. Dopisz nową treść do istniejącej
        # Zmieniamy sposób tworzenia full_history_text, aby dopisywać tylko ostatnią turę rozmowy
        # a nie całą historię za każdym razem, bo to prowadzi do duplikacji.
        # W funkcji chat_input będziemy przekazywać tylko tę nową, ostatnią turę.
        updated_content = existing_content + new_content_to_append

        media = MediaIoBaseUpload(io.BytesIO(updated_content.encode('utf-8')),
                                  mimetype='text/plain',
                                  resumable=True)

        if file_id:
            # Jeśli plik istnieje, zaktualizuj go z nową, dopisaną treścią
            service.files().update(fileId=file_id, media_body=media).execute()
            st.write(f"Zaktualizowano plik na Dysku Google (ID: {file_id})")
        else:
            # Jeśli plik nie istnieje, utwórz go
            file_metadata = {'name': file_name, 'mimeType': 'text/plain'}
            response = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            st.session_state.file_id = response.get('id')
            st.write(f"Utworzono nowy plik na Dysku Google (ID: {st.session_state.file_id})")

    except HttpError as error:
        st.error(f"Wystąpił błąd podczas operacji na Google Drive: {error}")
    except Exception as e:
        st.error(f"Wystąpił nieoczekiwany błąd podczas przesyłania historii: {e}")

# --- Główna logika aplikacji Streamlit (zmiany tylko w sekcji `st.chat_input`) ---
st.set_page_config(page_title="Gemini z Pamięcią", page_icon="🧠")
st.title("🧠 Gemini z Pamięcią")
st.caption("Twoja prywatna rozmowa z AI, zapisywana na Twoim Dysku Google.")

try:
    genai.configure(api_key=st.secrets.GEMINI_API_KEY)
except Exception as e:
    st.error(f"Błąd konfiguracji Gemini API. Sprawdź swój klucz w Secrets. Błąd: {e}")
    st.stop()

if "messages" not in st.session_state: st.session_state.messages = []
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
    # Upewnij się, że historia do Gemini jest poprawnie formatowana
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

                # --- KLUCZOWA ZMIANA TUTAJ: Przygotowanie tylko ostatniej tury rozmowy ---
                # Pamiętamy, że 'messages' zawiera już ostatnią odpowiedź Geminiego
                last_user_msg = prompt
                last_assistant_msg = response.text
                
                # Formatujemy tylko ostatnią turę, którą chcemy dopisać
                latest_turn_text = f"Ty: {last_user_msg}\n\nGemini: {last_assistant_msg}\n\n\n"

                # Wywołaj upload_history z nową, ostatnią turą rozmowy
                upload_history(st.session_state.drive_service, st.session_state.file_id, DRIVE_FILE_NAME, latest_turn_text)

                # Opcjonalnie: upewnij się, że file_id jest aktualne, jeśli plik został dopiero co utworzony
                if not st.session_state.file_id:
                    st.session_state.file_id = get_file_id(st.session_state.drive_service, DRIVE_FILE_NAME)
            except Exception as e:
                st.error(f"Wystąpił błąd: {e}")