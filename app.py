import os
import io
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- Konfiguracja Aplikacji ---
DRIVE_FILE_NAME = "historia_czatu_drive.txt" # Nazwa pliku historii na Dysku Google
SCOPES = ["https://www.googleapis.com/auth/drive"] # Zakresy dostępu do Google Drive

# --- Funkcje Pomocnicze dla Google Drive ---

@st.cache_resource # Używamy cache_resource, aby usługa Google Drive była inicjalizowana tylko raz
def get_drive_service():
    """
    Inicjalizuje i zwraca usługę Google Drive API.
    Pobiera dane uwierzytelniające z sekretów Streamlit.
    """
    try:
        # Tworzymy słownik z danymi logowania z sekretów Streamlit
        creds_info = {
            "type": st.secrets.gcp_service_account.type,
            "project_id": st.secrets.gcp_service_account.project_id,
            "private_key_id": st.secrets.gcp_service_account.private_key_id,
            "private_key": st.secrets.gcp_service_account.private_key.replace('\\n', '\n'), # Ważne dla klucza prywatnego
            "client_email": st.secrets.gcp_service_account.client_email,
            "client_id": st.secrets.gcp_service_account.client_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": st.secrets.gcp_service_account.client_x509_cert_url,
            "universe_domain": "googleapis.com"
        }
        # Tworzymy obiekt Credentials z danych serwisowych
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        # Budujemy usługę Drive API w wersji v3
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Błąd podczas łączenia z Google Drive: {e}")
        st.error("Sprawdź, czy wszystkie wartości w sekcji [gcp_service_account] w 'Secrets' są poprawnie wklejone.")
        return None

def get_file_id(service, file_name):
    """
    Wyszukuje ID pliku na Dysku Google po jego nazwie.
    Zwraca ID pliku lub None, jeśli plik nie istnieje.
    """
    query = f"name='{file_name}' and trashed=false" # Zapytanie: szukaj pliku o danej nazwie, który nie jest w koszu
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0].get('id') if files else None

def download_history(service, file_id):
    """
    Pobiera zawartość pliku z Dysku Google o podanym ID.
    Zwraca zawartość pliku jako string (UTF-8).
    """
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode('utf-8')
    except HttpError as error:
        # Przechwytujemy błąd HTTP, np. gdy plik nie istnieje (404)
        # Zwracamy pusty string, co będzie sygnałem dla aplikacji, że historia nie została pobrana
        return ""

def upload_history(service, file_id, file_name, content_to_save):
    """
    Aktualizuje lub tworzy plik historii na Google Drive z podaną zawartością.
    Jeśli file_id istnieje, plik jest aktualizowany. Jeśli nie, tworzony jest nowy.
    """
    try:
        # Przygotowanie treści do przesłania jako obiekt MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(content_to_save.encode('utf-8')),
                                  mimetype='text/plain',
                                  resumable=True)

        if file_id:
            # Jeśli mamy ID pliku, aktualizujemy jego zawartość
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # Jeśli nie mamy ID pliku (bo nie istniał lub został usunięty), tworzymy nowy
            file_metadata = {'name': file_name, 'mimeType': 'text/plain'}
            response = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            st.session_state.file_id = response.get('id') # Zapisujemy nowe ID w stanie sesji

    except HttpError as error:
        # Specyficzna obsługa błędu 404 (File Not Found) podczas próby aktualizacji
        if error.resp.status == 404:
            st.warning(f"Wystąpił błąd 404 (plik nie znaleziony) dla ID: {file_id}. Spróbuję utworzyć nowy plik.")
            # Wywołujemy upload_history rekurencyjnie, tym razem z file_id=None, aby wymusić utworzenie nowego pliku
            upload_history(service, None, file_name, content_to_save)
        else:
            st.error(f"Wystąpił błąd podczas operacji na Google Drive: {error}")
    except Exception as e:
        st.error(f"Wystąpił nieoczekiwany błąd podczas przesyłania historii: {e}")

# --- Główna Logika Aplikacji Streamlit ---

# Ustawienia strony Streamlit (tytuł zakładki, ikona, układ)
st.set_page_config(
    page_title="Gemini z Pamięcią",
    page_icon="moje_logo.png", # Używamy tego samego pliku logo jako favicon (48x48px)
    layout="wide" # Ustawienie układu strony na szeroki (alternatywnie "centered")
)

# --- ELEMENTY WIZUALNE: LOGO i BANER ---
st.image("moje_logo.png", width=48) # Wyświetlamy logo na stronie, ustawiając szerokość na 48px
st.image("baner.png", width=200, height=130) # Wyświetlamy baner, ustawiając konkretne wymiary

# Tytuł i opis aplikacji
st.title("🧠 Gemini z Pamięcią")
st.caption("Twoja prywatna rozmowa z AI, zapisywana na Twoim Dysku Google.")

# --- Inicjalizacja API Gemini ---
try:
    genai.configure(api_key=st.secrets.GEMINI_API_KEY)
except Exception as e:
    st.error(f"Błąd konfiguracji Gemini API. Sprawdź swój klucz w Secrets. Błąd: {e}")
    st.stop() # Zatrzymuje aplikację, jeśli API key jest błędny

# --- Ładowanie Historii z Dysku Google przy Starcie Aplikacji ---
# Sprawdzamy, czy historia została już załadowana w bieżącej sesji Streamlit
if "messages" not in st.session_state: st.session_state.messages = []
if "history_loaded" not in st.session_state:
    with st.spinner("Łączenie i wczytywanie pamięci z Dysku Google..."):
        drive_service = get_drive_service() # Pobieramy usługę Drive API
        if drive_service:
            st.session_state.drive_service = drive_service
            
            # Próbujemy znaleźć ID pliku historii na starcie aplikacji
            file_id_on_startup = get_file_id(drive_service, DRIVE_FILE_NAME)
            st.session_state.file_id = file_id_on_startup # Zapisujemy znalezione ID w stanie sesji

            history_text = ""
            if file_id_on_startup:
                try:
                    history_text = download_history(drive_service, file_id_on_startup)
                except HttpError as error:
                    # Jeśli plik nie znaleziono pod starym ID podczas startu, wyczyść ID w sesji
                    if error.resp.status == 404:
                        st.warning(f"Plik o ID {file_id_on_startup} nie został znaleziony podczas startu. Możliwe, że został usunięty ręcznie. Utworzę nowy plik przy pierwszej interakcji.")
                        st.session_state.file_id = None # Wyczyść stare ID, aby przy zapisie utworzyć nowy plik
                    else:
                        st.error(f"Błąd podczas pobierania historii przy starcie: {error}")
                
                # Jeśli historia została pomyślnie pobrana, parsujemy ją i dodajemy do st.session_state.messages
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
            st.stop() # Zatrzymuje aplikację, jeśli połączenie z Drive się nie powiedzie
    st.session_state.history_loaded = True # Oznaczamy, że historia została załadowana

# --- Inicjalizacja modelu Gemini i jego historii czatu ---
if "gemini_chat" not in st.session_state:
    model = genai.GenerativeModel('gemini-1.5-flash') # Tworzymy instancję modelu Gemini Flash
    # Konwertujemy załadowaną historię Streamlit do formatu oczekiwanego przez API Gemini
    gemini_history = [{'role': 'user' if msg['role'] == 'user' else 'model', 'parts': [msg['content']]} for msg in st.session_state.messages]
    st.session_state.gemini_chat = model.start_chat(history=gemini_history) # Rozpoczynamy czat z wczytaną historią

# --- Wyświetlanie Historii Czatu w Interfejsie ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- Obsługa Nowych Wiadomości Użytkownika ---
if prompt := st.chat_input("Napisz coś..."): # Pole do wpisywania wiadomości przez użytkownika
    st.session_state.messages.append({"role": "user", "content": prompt}) # Dodaj wiadomość użytkownika do sesji
    with st.chat_message("user"):
        st.markdown(prompt) # Wyświetl wiadomość użytkownika

    with st.chat_message("assistant"):
        with st.spinner("Myślę..."): # Wskaźnik ładowania
            try:
                response = st.session_state.gemini_chat.send_message(prompt) # Wyślij wiadomość do Gemini
                st.markdown(response.text) # Wyświetl odpowiedź Gemini
                st.session_state.messages.append({"role": "assistant", "content": response.text}) # Dodaj odpowiedź Gemini do sesji

                # --- Zapisywanie Pełnej Historii do Dysku Google ---
                full_history_text = ""
                # Iterujemy po całej historii w st.session_state.messages, aby zapisać ją w pliku
                # Zapewnia to, że plik na Dysku zawsze zawiera całą, aktualną rozmowę
                for i in range(0, len(st.session_state.messages), 2):
                    if i + 1 < len(st.session_state.messages):
                        user_msg = st.session_state.messages[i]["content"]
                        assistant_msg = st.session_state.messages[i+1]["content"]
                        full_history_text += f"Ty: {user_msg}\n\nGemini: {assistant_msg}\n\n\n"
                
                # Wywołaj funkcję upload_history z całą zbudowaną treścią
                upload_history(st.session_state.drive_service, st.session_state.file_id, DRIVE_FILE_NAME, full_history_text)

            except Exception as e:
                st.error(f"Wystąpił błąd: {e}")