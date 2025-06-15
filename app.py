import os
import io
import streamlit as st
import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- Konfiguracja (taka sama jak wcześniej) ---
SCOPES = ["https://www.googleapis.com/auth/drive"]
# Nazwa pliku, w którym będziemy przechowywać historię na Dysku Google
DRIVE_FILE_NAME = "historia_czatu_drive.txt"

@st.cache_resource
def get_drive_service():
    """Funkcja do autoryzacji i tworzenia obiektu usługi Dysku."""
    creds = None
    # Plik token.json przechowuje tokeny dostępu użytkownika. Tworzy się automatycznie.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # Jeśli nie ma ważnych danych logowania, pozwól użytkownikowi się zalogować.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            # PONIŻEJ ZNAJDUJE SIĘ POPRAWIONA LINIA:
            creds = flow.run_local_server(port=0)
        # Zapisz dane logowania na przyszłość
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    
    try:
        service = build("drive", "v3", credentials=creds)
        print("Połączono z Google Drive")
        return service
    except HttpError as error:
        st.error(f"Wystąpił błąd podczas tworzenia usługi Dysku: {error}")
        return None

def get_file_id(service, file_name):
    """Funkcja do znajdowania ID pliku na Dysku po jego nazwie."""
    query = f"name='{file_name}' and trashed=false"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0].get('id') if files else None

def download_history(service, file_id):
    """Pobiera historię czatu z pliku na Dysku."""
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
    """Wysyła zaktualizowaną historię na Dysk, nadpisując plik."""
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

# Ta sekcja została uproszczona, żeby uniknąć błędów
# Inicjalizacja stanu sesji, jeśli jeszcze nie istnieje
if "messages" not in st.session_state:
    st.session_state.messages = []
if "history_loaded" not in st.session_state:
    st.session_state.history_loaded = False


# Ładowanie historii TYLKO raz, na początku
if not st.session_state.history_loaded:
    with st.spinner("Łączenie i wczytywanie pamięci z Dysku Google..."):
        # Konfiguracja API Gemini
        if 'GEMINI_API_KEY' not in os.environ:
             st.error("Błąd krytyczny: Brak klucza GEMINI_API_KEY.")
             st.stop()
        genai.configure(api_key=os.environ['GEMINI_API_KEY'])
        
        # Inicjalizacja modelu i czatu
        model = genai.GenerativeModel('gemini-1.5-flash')
        st.session_state.gemini_chat = model.start_chat(history=[])
        
        # Połączenie z Dyskiem
        drive_service = get_drive_service()
        st.session_state.drive_service = drive_service
        
        if drive_service:
            file_id = get_file_id(drive_service, DRIVE_FILE_NAME)
            st.session_state.file_id = file_id
            if file_id:
                history_text = download_history(drive_service, file_id)
                if history_text:
                    turns = history_text.strip().split('\n\n\n')
                    gemini_history = []
                    for turn in turns:
                        if 'Ty:' in turn and 'Gemini:' in turn:
                            user_part = turn.split('Ty:')[1].split('Gemini:')[0].strip()
                            model_part = turn.split('Gemini:')[1].strip()
                            st.session_state.messages.append({"role": "user", "content": user_part})
                            st.session_state.messages.append({"role": "assistant", "content": model_part})
                            gemini_history.append({'role': 'user', 'parts': [user_part]})
                            gemini_history.append({'role': 'model', 'parts': [model_part]})
                    st.session_state.gemini_chat.history = gemini_history
            st.success("Pamięć połączona z Dyskiem Google!")
        st.session_state.history_loaded = True


# Wyświetlanie historii czatu
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Pole do wpisywania tekstu na dole strony
if prompt := st.chat_input("Napisz coś..."):
    # Dodaj wiadomość użytkownika do historii i wyświetl
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Wygeneruj i wyświetl odpowiedź AI
    with st.chat_message("assistant"):
        with st.spinner("Myślę..."):
            try:
                response = st.session_state.gemini_chat.send_message(prompt)
                st.markdown(response.text)
                
                # Dodaj odpowiedź AI do historii
                st.session_state.messages.append({"role": "assistant", "content": response.text})

                # Zapisz całą nową historię na Dysku Google
                full_history_text = ""
                user_msg, assistant_msg = None, None
                for msg in st.session_state.messages:
                    if msg["role"] == "user":
                        user_msg = msg["content"]
                    elif msg["role"] == "assistant":
                        assistant_msg = msg["content"]
                        full_history_text += f"Ty: {user_msg}\n\nGemini: {assistant_msg}\n\n\n"
                
                upload_history(
                    st.session_state.drive_service,
                    st.session_state.file_id,
                    DRIVE_FILE_NAME,
                    full_history_text
                )
                # Zaktualizuj file_id, jeśli plik został dopiero co stworzony
                if not st.session_state.file_id:
                     st.session_state.file_id = get_file_id(st.session_state.drive_service, DRIVE_FILE_NAME)


            except Exception as e:
                st.error(f"Wystąpił błąd: {e}")