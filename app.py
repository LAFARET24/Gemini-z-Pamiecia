import os.path
import io
import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import json # Dodajemy import json
import streamlit as st # DODAJ TĘ LINIJKĘ NA POCZĄTKU

# --- Konfiguracja ---
# Zakres uprawnień, o które prosimy. Dajemy pełny dostęp do Dysku.
SCOPES = ["https://www.googleapis.com/auth/drive"]
# Nazwa pliku, w którym będziemy przechowywać historię na Dysku Google
DRIVE_FILE_NAME = "historia_czatu_drive.txt"

def get_drive_service():
    """Funkcja do autoryzacji i tworzenia obiektu usługi Dysku."""
    creds = None

    # Zmieniamy odczyt tokenów i danych uwierzytelniających z st.secrets
    if "google_credentials" in st.secrets:
        # Próbujemy odczytać refresh_token z sekretów
        refresh_token = st.secrets["google_credentials"]["refresh_token"]
        
        # Tworzymy obiekt Credentials z sekretów
        creds_data = {
            "token": refresh_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": st.secrets["google_credentials"]["client_id"],
            "client_secret": st.secrets["google_credentials"]["client_secret"],
            "scopes": SCOPES
        }
        creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        
    # Jeśli nadal nie ma ważnych danych logowania lub są nieważne, spróbuj odświeżyć
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Ta część kodu jest dla lokalnego uruchamiania.
            # W Streamlit Cloud powinniśmy mieć już token z secrets.
            # Jeśli jednak chcesz, aby to działało lokalnie ORAZ w chmurze,
            # musiałbyś stworzyć osobną logikę. Na potrzeby Streamlit Cloud
            # polegamy na 'refresh_token' z 'st.secrets'.
            # Jeśli aplikacja ma działać tylko w Streamlit Cloud,
            # możesz usunąć ten blok 'else:' lub dostosować go.
            # W tej wersji zmieniamy go, aby korzystał z credentials_json z sekretów
            
            # Tworzymy config JSON z danych w secrets
            client_config = {
                "web": { # Jeśli w credentials.json masz sekcję 'web' lub 'installed'
                    "client_id": st.secrets["google_credentials"]["client_id"],
                    "client_secret": st.secrets["google_credentials"]["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": ["http://localhost"] # Streamlit Cloud nie używa, ale wymagane przez flow
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            
            # W przypadku Streamlit Cloud, flow.run_local_server() nie zadziała.
            # To jest scenariusz dla pierwszej autoryzacji lokalnie.
            # W Streamlit zakładamy, że refresh_token już istnieje w secrets.
            # Jeśli nie ma refresh_token, Streamlit nie jest w stanie się autoryzować.
            # Dla uproszczenia w Streamlit Cloud, będziemy polegać na tym, że refresh_token jest zawsze w secrets.
            # Możesz usunąć cały blok 'else', jeśli aplikacja ma działać TYLKO w Streamlit Cloud
            # i ZAWSZE z refresh_token w secrets.
            
            # Ten blok nie jest potrzebny, jeśli refresh_token jest zawsze w secrets.
            # W Streamlit nie ma interaktywnego logowania przeglądarkowego.
            # creds = flow.run_local_server(port=0)
            
    # W Streamlit Cloud NIE ZAPISUJEMY token.json na dysk serwera.
    # Sekrety są już zapisane w panelu Streamlit.
    # with open("token.json", "w") as token:
    #     token.write(creds.to_json())
    
    try:
        service = build("drive", "v3", credentials=creds)
        return service
    except HttpError as error:
        st.error(f"Wystąpił błąd podczas tworzenia usługi Dysku: {error}") # Zmieniono print na st.error
        return None

def get_file_id(service, file_name):
    """Funkcja do znajdowania ID pliku na Dysku po jego nazwie."""
    # ... reszta funkcji bez zmian ...
    query = f"name='{file_name}' and trashed=false"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    if files:
        return files[0].get('id')
    return None

def download_history(service, file_id):
    """Pobiera historię czatu z pliku na Dysku."""
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode('utf-8')
    except HttpError as error:
        st.error(f"Wystąpił błąd podczas pobierania pliku: {error}") # Zmieniono print na st.error
        return ""

def upload_history(service, file_id, file_name, content):
    """Wysyła zaktualizowaną historię na Dysk, nadpisując plik."""
    media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain', resumable=True)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        file_metadata = {'name': file_name}
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()

# --- Główna część aplikacji ---

# 1. Uzyskaj dostęp do Dysku Google
st.write("Łączenie z Dyskiem Google...") # Zmieniono print na st.write
drive_service = get_drive_service()

if not drive_service:
    st.error("Nie udało się połączyć z Dyskiem Google. Sprawdź sekrety.") # Zmieniono input na st.error
    st.stop() # Zatrzymuje działanie aplikacji w Streamlit
st.write("Połączono z Dyskiem Google.") # Zmieniono print na st.write

# 2. Skonfiguruj Gemini
# Upewnij się, że masz ustawioną zmienną środowiskową GEMINI_API_KEY
# Zmieniamy to, aby czytać z st.secrets
if 'GEMINI_API_KEY' not in st.secrets:
    st.error("Błąd: Nie znaleziono klucza API dla Gemini w Streamlit secrets.") # Zmieniono print na st.error
    st.stop() # Zatrzymuje działanie aplikacji w Streamlit
genai.configure(api_key=st.secrets['GEMINI_API_KEY']) # Zmieniono odczyt z os.environ na st.secrets
model = genai.GenerativeModel('gemini-1.5-flash')
chat = model.start_chat(history=[])

# 3. Wczytaj historię, jeśli istnieje
st.write("Sprawdzanie historii czatu na Dysku...") # Zmieniono print na st.write
file_id = get_file_id(drive_service, DRIVE_FILE_NAME)
current_history_text = ""
if file_id:
    st.write("Znaleziono historię, wczytywanie...") # Zmieniono print na st.write
    current_history_text = download_history(drive_service, file_id)
    # Prosta konwersja tekstu na format historii Gemini
    if current_history_text:
        history_list = []
        # Dzielimy tekst na tury rozmowy (oddzielone podwójnym enterem)
        turns = current_history_text.strip().split('\n\n\n')
        for turn in turns:
            if 'Ty:' in turn and 'Gemini:' in turn:
                user_part = turn.split('Ty:')[1].split('Gemini:')[0].strip()
                model_part = turn.split('Gemini:')[1].strip()
                history_list.append({'role': 'user', 'parts': [user_part]})
                history_list.append({'role': 'model', 'parts': [model_part]})
        chat.history = history_list
        st.write(f"Historia wczytana. Liczba tur: {len(chat.history) // 2}") # Zmieniono print na st.write
else:
    st.write("Nie znaleziono historii. Zaczynamy nową rozmowę.") # Zmieniono print na st.write


# 4. Rozpocznij czat
st.write("\nCześć! Jestem Twoim osobistym asystentem z pamięcią w chmurze.") # Zmieniono print na st.write
st.write("Wpisz 'koniec', aby zakończyć rozmowę.") # Zmieniono print na st.write

# Zastępujemy input() w Streamlit polem tekstowym
prompt_uzytkownika = st.text_input("Ty:", key="user_prompt")

if prompt_uzytkownika.lower() == 'koniec':
    st.write("Do zobaczenia!")
    st.stop() # Zatrzymuje aplikację po zakończeniu

# Przenosimy logikę wysyłania wiadomości do if prompt_uzytkownika,
# aby nie wysyłać pustych zapytań przy starcie
if prompt_uzytkownika:
    try:
        with st.spinner("Gemini myśli..."): # Dodajemy spinner
            response = chat.send_message(prompt_uzytkownika)
        st.write(f"Gemini: {response.text}")
        
        # Zapisz nową konwersację na Dysku
        new_turn = f"Ty: {prompt_uzytkownika}\n\nGemini: {response.text}\n\n\n"
        current_history_text += new_turn
        upload_history(drive_service, file_id, DRIVE_FILE_NAME, current_history_text)
        if not file_id: # Jeśli plik został dopiero co stworzony, pobierz jego nowe ID
            file_id = get_file_id(drive_service, DRIVE_FILE_NAME)

    except Exception as e:
        st.error(f"\nWystąpił błąd: {e}") # Zmieniono print na st.error
