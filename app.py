import os
import io
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from gtts import gTTS
import tempfile
import time 

# Importowanie biblioteki do obsługi WebRTC (nagrywanie mikrofonu)
from streamlit_webrtc import webrtc_streamer, WebRtcMode, RTCConfiguration

# Potrzebujemy pydub do konwersji surowych bajtów audio na WebM/WAV, jeśli to konieczne
# Pydub wymaga zainstalowanego ffmpeg na systemie (Streamlit Cloud to ma)
# pip install pydub
from pydub import AudioSegment
from pydub.playback import play # Opcjonalnie do testowania lokalnie

# --- Konfiguracja Aplikacji i Streamlit ---
st.set_page_config(page_title="Gemini z Pamięcią i Notatkami", page_icon="🎤", layout="wide")

DRIVE_FILE_NAME = "historia_czatu_drive.txt"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# --- Inicjalizacja API, Stanu Sesji i Historii ---
try:
    genai.configure(api_key=st.secrets.GEMINI_API_KEY)
except Exception as e:
    st.error(f"Błąd konfiguracji Gemini API. Sprawdź swój klucz w Secrets. Błąd: {e}")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = [] # Historia wyświetlana w UI
if "gemini_history" not in st.session_state:
    st.session_state.gemini_history = [] # Pełna historia dla modelu Gemini (kontekst)
if "history_loaded" not in st.session_state:
    pass 

if "gemini_chat" not in st.session_state:
    pass


# --- Funkcje Pomocnicze dla Google Drive ---
@st.cache_resource
def get_drive_service():
    """Autoryzuje i zwraca obiekt usługi Google Drive."""
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
            "universe_domain": "googleapis.com"
        }
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Błąd podczas łączenia z Google Drive: {e}")
        st.error("Sprawdź, czy wszystkie wartości w sekcji [gcp_service_account] w 'Secrets' są poprawnie wklejone.")
        return None

def get_file_id(service, file_name):
    """Zwraca ID pliku na Dysku Google, jeśli istnieje."""
    query = f"name='{file_name}' and trashed=false"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0].get('id') if files else None

def download_history(service, file_id):
    """Pobiera historię czatu z Google Drive."""
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode('utf-8')
    except HttpError:
        return "" # Zwróć pusty string, jeśli plik nie istnieje lub jest błąd 404

def upload_history(service, file_id, file_name, content_to_save):
    """Zapisuje historię czatu do Google Drive."""
    try:
        media = MediaIoBaseUpload(io.BytesIO(content_to_save.encode('utf-8')),
                                  mimetype='text/plain',
                                  resumable=True)
        if file_id:
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {'name': file_name, 'mimeType': 'text/plain'}
            response = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            st.session_state.file_id = response.get('id') # Zapisz nowe ID pliku
    except HttpError as error:
        if error.resp.status == 404:
            st.warning(f"Wystąpił błąd 404 (plik nie znaleziony) dla ID: {file_id}. Spróbuję utworzyć nowy plik.")
            upload_history(service, None, file_name, content_to_save) # Spróbuj utworzyć nowy
        else:
            st.error(f"Wystąpił błąd podczas operacji na Google Drive: {error}")
    except Exception as e:
        st.error(f"Wystąpił nieoczekiwany błąd podczas przesyłania historii: {e}")

# --- Funkcja do syntezy mowy (TTS) ---
def text_to_speech(text, lang='pl'):
    """Konwertuje tekst na mowę i zwraca ścieżkę do pliku MP3."""
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            tts.save(fp.name)
            audio_path = fp.name
        return audio_path
    except Exception as e:
        st.error(f"Błąd podczas generowania mowy: {e}")
        return None

# --- Główna Logika Aplikacji Streamlit ---

# Zakomentuj lub usuń, jeśli nie masz tych plików
# st.image("moje_logo.png", width=48)
# st.image("baner.png", width=200)

st.title("🧠 Gemini: Twój Asystent Głosowy i Notatnik")
st.caption("Mów lub pisz. Twoja prywatna rozmowa z AI jest zapisywana na Twoim Dysku Google.")


# --- PRZETWARZANIE ŁADOWANIA HISTORII ---
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
                    gemini_history_from_drive = []
                    turns = history_text.strip().split('\n\n\n')
                    for turn in turns:
                        if 'Ty:' in turn and 'Gemini:' in turn:
                            user_part = turn.split('Ty:')[1].split('Gemini:')[0].strip()
                            model_part = turn.split('Gemini:')[1].strip()
                            gemini_history_from_drive.append({'role': 'user', 'parts': [user_part]})
                            gemini_history_from_drive.append({'role': 'model', 'parts': [model_part]})
                    st.session_state.gemini_history = gemini_history_from_drive
            st.success("Pamięć połączona i wczytana w tle!")
        else:
            st.error("Nie udało się połączyć z usługą Dysku Google.")
            st.stop()
    st.session_state.history_loaded = True

# Inicjalizacja czatu Gemini po załadowaniu historii
if "gemini_chat" not in st.session_state:
    model = genai.GenerativeModel('gemini-1.5-flash')
    st.session_state.gemini_chat = model.start_chat(history=st.session_state.gemini_history)


# --- Wyświetlanie Historii Czatu w Interfejsie ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "audio_response" in message and message["audio_response"]:
            st.audio(message["audio_response"]) 

# --- OBSŁUGA GŁOSU I TEKSTU (ZMODYFIKOWANA DLA STREAMLIT-WEBRTC Z GŁĘBSZĄ OBRÓBKĄ AUDIO) ---
st.markdown("---")
st.write("Użyj przycisków Start/Stop mikrofonu lub wpisz tekst poniżej:")

# Konfiguracja WebRTC dla audio (bez wideo)
webrtc_ctx = webrtc_streamer(
    key="mic_audio_input",
    mode=WebRtcMode.SENDONLY, # Wysyłamy tylko audio z mikrofonu
    audio_receiver_size=2048, # Zwiększony bufor dla płynniejszego zbierania
    media_stream_constraints={"video": False, "audio": True}, # Tylko audio
    # RTC_CONFIGURATION = RTCConfiguration( {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]} ) # Opcjonalnie do rozwiązania problemów z NAT
    async_processing=True # Ważne: pozwala na asynchroniczne przetwarzanie
)

# Inicjalizacja bufora audio w sesji
if "audio_buffer_webrtc" not in st.session_state:
    st.session_state.audio_buffer_webrtc = io.BytesIO()
if "webrtc_last_audio_hash" not in st.session_state: # Użyjemy hasha do unikalnego identyfikowania nagrań
    st.session_state.webrtc_last_audio_hash = None


# Zbiera dane audio z mikrofonu, gdy nagrywanie jest aktywne
if webrtc_ctx.state.playing and webrtc_ctx.audio_receiver:
    try:
        # Zbieramy ramki audio i zapisujemy je do bufora
        # webrtc_ctx.audio_receiver.get_queued_frames() zwraca listę AudioFrame
        # Każda AudioFrame zawiera surowe bajty PCM.
        frames = webrtc_ctx.audio_receiver.get_queued_frames()
        if frames:
            for frame in frames:
                # 'to_ndarray()' konwertuje ramkę na numpy array, 'tobytes()' na surowe bajty
                st.session_state.audio_buffer_webrtc.write(frame.to_ndarray().tobytes())
            # st.write(f"Zebrałem {st.session_state.audio_buffer_webrtc.tell()} bajtów") # Debugowanie
    except Exception as e:
        st.warning(f"Błąd podczas zbierania ramek audio WebRTC: {e}")

# Jeśli użytkownik zatrzymał nagrywanie ORAZ bufor zawiera dane, przetwarzamy je
# webrtc_ctx.state.playing == False oznacza, że nagrywanie zostało zatrzymane
# audio_buffer_webrtc.tell() > 0 sprawdza, czy w ogóle coś zostało nagrane
if webrtc_ctx.state.playing == False and st.session_state.audio_buffer_webrtc.tell() > 0:
    # Pobieramy zebrane surowe bajty audio (PCM)
    raw_audio_bytes = st.session_state.audio_buffer_webrtc.getvalue()
    
    # Haszujemy bajty, aby unikalnie zidentyfikować to nagranie i przetworzyć tylko raz
    import hashlib
    current_audio_hash = hashlib.md5(raw_audio_bytes).hexdigest()

    if st.session_state.webrtc_last_audio_hash != current_audio_hash:
        st.session_state.webrtc_last_audio_hash = current_audio_hash
        
        st.info("Konwertuję nagranie do formatu WebM (Opus) i transkrybuję...")
        try:
            # Konwersja surowych bajtów PCM do formatu WebM (Opus) za pomocą pydub
            # Zakładamy domyślne parametry (np. 48kHz, mono), które są standardowe dla WebRTC
            audio_segment = AudioSegment(
                raw_audio_bytes, 
                sample_width=frame.sample_width, # Użyj sample_width z ostatniej ramki
                frame_rate=frame.sample_rate,   # Użyj sample_rate z ostatniej ramki
                channels=frame.channels         # Użyj channels z ostatniej ramki
            )
            
            # Eksport do WebM (Opus) do obiektu BytesIO
            webm_audio_bytes_io = io.BytesIO()
            audio_segment.export(webm_audio_bytes_io, format="webm", codec="libopus")
            webm_audio_bytes = webm_audio_bytes_io.getvalue()

            # Przetwarzamy przekonwertowane audio
            process_prompt("audio", webm_audio_bytes)
            
        except Exception as e:
            st.error(f"Błąd podczas konwersji audio (pydub/ffmpeg): {e}")
            st.session_state.messages.append({"role": "user", "content": "🎤 *Błąd konwersji audio*"})
            # Wyczyść bufor nawet po błędzie konwersji
            st.session_state.audio_buffer_webrtc = io.BytesIO() 
            st.rerun() # Wymuszenie reroll

    # Wyczyść bufor po przetworzeniu, niezależnie od wyniku
    st.session_state.audio_buffer_webrtc = io.BytesIO() 
    # Opcjonalnie: st.rerun() jest już wywoływane w process_prompt

# Pole do wpisywania tekstu
text_prompt = st.text_input("...lub wpisz swoje pytanie tutaj:", key="text_input_bottom")

# --- Funkcja pomocnicza do przetwarzania promptu (głosowego/tekstowego) ---
def process_prompt(prompt_type, input_data):
    user_prompt_content = None

    if prompt_type == "audio" and input_data:
        # st.info("Przetwarzam Twoje nagranie i transkrybuję...") # Już wyświetlone wcześniej
        try:
            # Format audio z webrtc_streamer to WebM (Opus)
            audio_file_data = {"mime_type": "audio/webm", "data": input_data} 
            
            temp_model = genai.GenerativeModel('gemini-1.5-flash')
            transcription_chat = temp_model.start_chat(history=[])
            
            transcription_response = transcription_chat.send_message([
                audio_file_data, 
                "Proszę, przetranskrybuj tę mowę na tekst. Nie dodawaj żadnych innych informacji ani komentarzy."
            ])
            
            transcribed_text = transcription_response.text.strip()
            
            if transcribed_text:
                st.session_state.messages.append({"role": "user", "content": f"🎤 {transcribed_text}"})
                user_prompt_content = transcribed_text 
            else:
                st.warning("Nie udało się przetranskrybować nagrania. Spróbuj ponownie.")
                st.session_state.messages.append({"role": "user", "content": "🎤 *Błąd transkrypcji*"})
                user_prompt_content = None 

        except Exception as e:
            st.error(f"Błąd podczas transkrypcji nagrania: {e}")
            st.session_state.messages.append({"role": "user", "content": "🎤 *Błąd transkrypcji*"})
            user_prompt_content = None
    
    elif prompt_type == "text" and input_data:
        st.session_state.messages.append({"role": "user", "content": input_data})
        user_prompt_content = input_data

    # Jeśli mamy coś do wysłania do Gemini, robimy to natychmiast
    if user_prompt_content:
        with st.chat_message("assistant"):
            with st.spinner("Myślę..."):
                try:
                    response = st.session_state.gemini_chat.send_message(user_prompt_content)
                    gemini_response_text = response.text
                    st.markdown(gemini_response_text)
                    
                    audio_response_path = text_to_speech(gemini_response_text)
                    if audio_response_path:
                        st.audio(audio_response_path)
                    
                    st.session_state.messages.append({"role": "assistant", "content": gemini_response_text, "audio_response": audio_response_path})

                    # --- Zapisywanie Pełnej Historii do Dysku Google ---
                    full_history_to_save = ""
                    chat_history_from_model = st.session_state.gemini_chat.history
                    
                    for i in range(0, len(chat_history_from_model), 2):
                        if i + 1 < len(chat_history_from_model):
                            user_part_obj = chat_history_from_model[i].parts[0]
                            user_msg_for_save = user_part_obj.text if hasattr(user_part_obj, 'text') else f"*{user_part_obj.mime_type}*"
                            
                            assistant_part_obj = chat_history_from_model[i+1].parts[0]
                            assistant_msg_for_save = assistant_part_obj.text if hasattr(assistant_part_obj, 'text') else f"*{assistant_part_obj.mime_type}*"
                            
                            full_history_to_save += f"Ty: {user_msg_for_save}\n\nGemini: {assistant_msg_for_save}\n\n\n"
                        
                    if st.session_state.get("drive_service"):
                        upload_history(st.session_state.drive_service, st.session_state.get("file_id"), DRIVE_FILE_NAME, full_history_to_save)

                except Exception as e:
                    st.error(f"Wystąpił błąd podczas komunikacji z Gemini lub generowania głosu: {e}")
                
        # st.rerun() jest wywoływane przez webrtc_ctx.audio_receiver.last_buffered_audio
        # lub po wysłaniu promptu tekstowego.
        st.session_state.text_input = "" 
        
# --- Wywoływanie funkcji przetwarzającej na podstawie akcji użytkownika ---

# Jeśli użytkownik wprowadził tekst i nacisnął Enter (przeniesiono na koniec, aby nie kolidowało)
if text_prompt:
    if "last_text_prompt" not in st.session_state or st.session_state.last_text_prompt != text_prompt:
        st.session_state.last_text_prompt = text_prompt
        process_prompt("text", text_prompt)
        st.rerun()