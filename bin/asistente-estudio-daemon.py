#!/usr/bin/env python3
import ctypes
import os
import sys
import time
import signal
import subprocess
import threading
import select
import re
import atexit
import json
import base64
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from ctypes import (
    c_char_p, c_int, c_ulong, c_long, c_void_p,
    POINTER, byref, c_ubyte, Structure
)
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

DATA_DIR = os.path.expanduser("~/.local/share/asistente-estudio")
os.makedirs(DATA_DIR, exist_ok=True)

PID_FILE = os.path.join(DATA_DIR, "daemon.pid")
LOG_FILE = os.path.join(DATA_DIR, "historial.txt")
VOICE_FILE = os.path.join(DATA_DIR, "voice.enabled")
VOICE_MODE_FILE = os.path.join(DATA_DIR, "voice.mode")
VOICE_NEURAL_FILE = os.path.join(DATA_DIR, "voice.neural")
NOTIF_MUTED_FILE = os.path.join(DATA_DIR, "notifications.muted")

running = True
is_processing = False
proc_lock = threading.Lock()
audio_lock = threading.Lock()
notif_lock = threading.Lock()
active_notifications = set()
active_audio_proc = None

# Load native X11 & XFixes libraries (zero Wayland surface overhead)
x11 = ctypes.cdll.LoadLibrary('libX11.so.6')
xfixes = ctypes.cdll.LoadLibrary('libXfixes.so.3')

x11.XOpenDisplay.argtypes = [c_char_p]
x11.XOpenDisplay.restype = c_void_p
x11.XCloseDisplay.argtypes = [c_void_p]
x11.XCloseDisplay.restype = c_int
x11.XDefaultRootWindow.argtypes = [c_void_p]
x11.XDefaultRootWindow.restype = c_ulong
x11.XCreateSimpleWindow.argtypes = [
    c_void_p, c_ulong, c_int, c_int, c_int, c_int, c_int, c_ulong, c_ulong
]
x11.XCreateSimpleWindow.restype = c_ulong
x11.XDestroyWindow.argtypes = [c_void_p, c_ulong]
x11.XDestroyWindow.restype = c_int
x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
x11.XInternAtom.restype = c_ulong
x11.XConvertSelection.argtypes = [c_void_p, c_ulong, c_ulong, c_ulong, c_ulong, c_ulong]
x11.XConvertSelection.restype = c_int
x11.XPending.argtypes = [c_void_p]
x11.XPending.restype = c_int
x11.XFlush.argtypes = [c_void_p]
x11.XFlush.restype = c_int
x11.XNextEvent.argtypes = [c_void_p, c_void_p]
x11.XNextEvent.restype = c_int
x11.XConnectionNumber.argtypes = [c_void_p]
x11.XConnectionNumber.restype = c_int
x11.XFree.argtypes = [c_void_p]
x11.XFree.restype = c_int
x11.XDeleteProperty.argtypes = [c_void_p, c_ulong, c_ulong]
x11.XDeleteProperty.restype = c_int
x11.XGetWindowProperty.argtypes = [
    c_void_p, c_ulong, c_ulong, c_long, c_long, c_int, c_ulong,
    POINTER(c_ulong), POINTER(c_int), POINTER(c_ulong), POINTER(c_ulong), POINTER(POINTER(c_ubyte))
]
x11.XGetWindowProperty.restype = c_int

xfixes_event_base = c_int()
xfixes_error_base = c_int()
xfixes.XFixesQueryExtension.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
xfixes.XFixesQueryExtension.restype = c_int
xfixes.XFixesSelectSelectionInput.argtypes = [c_void_p, c_ulong, c_ulong, c_ulong]
xfixes.XFixesSelectSelectionInput.restype = None

class XEvent(Structure):
    _fields_ = [('type', c_int), ('pad', c_ulong * 24)]

# --- HELPERS: NOTIFICACIONES Y AUDIO NATIVO ---
def is_notif_enabled():
    return not os.path.exists(NOTIF_MUTED_FILE)

def notify(title, message, icon="accessories-dictionary", timeout_ms=25000, urgency="normal", force=False):
    if not force and not is_notif_enabled():
        return None
    try:
        env = os.environ.copy()
        user_home = os.path.expanduser("~")
        env["PATH"] = f"{user_home}/.gemini/antigravity-cli/bin:{user_home}/.local/bin:/usr/local/bin:/usr/bin:" + env.get("PATH", "")
        res = subprocess.run(
            [
                "notify-send",
                "-p",
                title,
                message,
                "-a", "Systema",
                "-i", icon,
                "-t", str(timeout_ms),
                "-u", urgency
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip().isdigit():
            nid = int(res.stdout.strip())
            with notif_lock:
                active_notifications.add(nid)
            return nid
    except Exception:
        pass
    return None

def close_all_notifications():
    with notif_lock:
        ids_to_close = list(active_notifications)
        active_notifications.clear()

    for nid in ids_to_close:
        try:
            subprocess.Popen([
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.CloseNotification",
                str(nid)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    if ids_to_close:
        highest = max(ids_to_close)
        for nid in range(max(1, highest - 15), highest + 5):
            try:
                subprocess.Popen([
                    "gdbus", "call", "--session",
                    "--dest", "org.freedesktop.Notifications",
                    "--object-path", "/org/freedesktop/Notifications",
                    "--method", "org.freedesktop.Notifications.CloseNotification",
                    str(nid)
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

def is_voice_enabled():
    return os.path.exists(VOICE_FILE)

def get_voice_mode():
    if os.path.exists(VOICE_MODE_FILE):
        try:
            with open(VOICE_MODE_FILE, "r", encoding="utf-8") as f:
                mode = f.read().strip().lower()
                if mode in ["whisper", "normal"]:
                    return mode
        except Exception:
            pass
    return "normal"

def get_neural_voice():
    if os.path.exists(VOICE_NEURAL_FILE):
        try:
            with open(VOICE_NEURAL_FILE, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return "es-MX-JorgeNeural"

def speak_answer(text):
    if not is_voice_enabled():
        return

    clean_speech = re.sub(r'<[^>]+>', '', text)
    clean_speech = re.sub(r'[^\w\s.,;:áéíóúÁÉÍÓÚñÑüÜ¿?¡!-]', ' ', clean_speech)
    clean_speech = re.sub(r'\s+', ' ', clean_speech).strip()
    if not clean_speech:
        return

    # Si contiene varias preguntas (P1, P2 o Pregunta 1...), leer la secuencia completa
    if re.search(r'pregunta \d+|p\d+[:.]', clean_speech, re.IGNORECASE) or "•" in clean_speech:
        if len(clean_speech) > 600:
            cutoff = clean_speech[:600].rfind('.')
            speech_text = clean_speech[:cutoff+1] if cutoff > 100 else clean_speech[:600]
        else:
            speech_text = clean_speech
    else:
        first_sentence = re.split(r'[.\n]', clean_speech)[0].strip()
        speech_text = first_sentence if len(first_sentence) > 8 else clean_speech[:200]

    def _worker():
        global active_audio_proc
        try:
            with audio_lock:
                if active_audio_proc and active_audio_proc.poll() is None:
                    try:
                        active_audio_proc.kill()
                    except Exception:
                        pass

                audio_path = None
                # 1. Intentar voz neuronal hiperrealista (Microsoft Azure Neural TTS)
                try:
                    import asyncio, edge_tts
                    voice_name = get_neural_voice()
                    mp3_path = "/tmp/asistente_voice.mp3"
                    async def _synthesize():
                        comm = edge_tts.Communicate(speech_text, voice_name, rate="+8%")
                        await comm.save(mp3_path)
                    asyncio.run(_synthesize())
                    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 100:
                        audio_path = mp3_path
                except Exception as e_neural:
                    print(f"[DAEMON] Neural TTS no disponible ({e_neural}), usando respaldo nativo.", flush=True)

                # 2. Respaldo offline nativo si falla la conexión
                if not audio_path:
                    wav_path = "/tmp/asistente_voice.wav"
                    res = subprocess.run(
                        ["espeak-ng", "-v", "es-419", "-s", "155", "-a", "100", "-p", "50", speech_text, "-w", wav_path],
                        capture_output=True,
                        timeout=5
                    )
                    if res.returncode == 0 and os.path.exists(wav_path):
                        audio_path = wav_path

                # 3. Reproducir audio con PipeWire
                if audio_path:
                    active_audio_proc = subprocess.Popen(
                        ["pw-play", audio_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
        except Exception as e:
            print(f"[DAEMON] Error al reproducir voz: {e}", flush=True)

    threading.Thread(target=_worker, daemon=True).start()

def repeat_last_voice():
    global active_audio_proc
    try:
        with audio_lock:
            if active_audio_proc and active_audio_proc.poll() is None:
                try:
                    active_audio_proc.kill()
                except Exception:
                    pass

            for path in ["/tmp/asistente_voice.mp3", "/tmp/asistente_voice.wav"]:
                if os.path.exists(path) and os.path.getsize(path) > 100:
                    active_audio_proc = subprocess.Popen(
                        ["pw-play", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    return
    except Exception as e:
        print(f"[DAEMON] Error al repetir voz: {e}", flush=True)

def parse_and_notify(raw_output):
    clean = raw_output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', clean)
    clean = re.sub(r'(?<!\*)\*(?!\*)([^\n]+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', clean)

    # 1. Extraer bloque VOZ si existe
    voice_phrase = ""
    voz_match = re.search(
        r'(?:^|\n)\s*(?:<b>)?VOZ:?(?:</b>)?\s*(.+?)(?=(?:\n\s*(?:<b>)?(?:RESPUESTA|DETALLE|EXPLICACION|JUSTIFICACION):?)|\Z)',
        clean,
        re.IGNORECASE | re.DOTALL
    )
    if not voz_match:
        voz_match = re.search(r'(?:^|\n)\s*(?:<b>)?VOZ:?(?:</b>)?\s*(.+)', clean, re.IGNORECASE | re.DOTALL)
    if voz_match:
        voice_phrase = re.sub(r'<[^>]+>', '', voz_match.group(1)).strip()

    # 2. Extraer bloques RESPUESTA y DETALLE
    resp_match = re.search(
        r'(?:^|\n)\s*(?:<b>)?(?:RESPUESTA|OPCIÓN|OPCION):?(?:</b>)?\s*(.+?)(?=(?:\n\s*(?:<b>)?(?:DETALLE|EXPLICACION|JUSTIFICACION|VOZ):?)|\Z)',
        clean,
        re.IGNORECASE | re.DOTALL
    )
    det_match = re.search(
        r'(?:^|\n)\s*(?:<b>)?(?:DETALLE|EXPLICACION|JUSTIFICACION):?(?:</b>)?\s*(.+?)(?=(?:\n\s*(?:<b>)?(?:VOZ):?)|\Z)',
        clean,
        re.IGNORECASE | re.DOTALL
    )

    title = "💡 Respuesta"
    body = ""

    if resp_match:
        resp_clean = resp_match.group(1).strip()
        det_clean = det_match.group(1).strip() if det_match else ""

        # Comprobar si son múltiples preguntas (P1, P2, • P1, Pregunta 1, etc.)
        is_multi = bool(re.search(r'(?:•\s*P\d+|P\d+[:.]|Pregunta\s*\d+)', resp_clean, re.IGNORECASE))

        if is_multi:
            title = "💡 Respuestas del examen"
            if det_clean:
                body = f"{resp_clean}\n\n{det_clean}"
            else:
                body = resp_clean

            if not voice_phrase:
                v = re.sub(r'•\s*P(\d+)[:.]?', r'Pregunta \1:', resp_clean)
                v = re.sub(r'<[^>]+>', '', v)
                voice_phrase = v
        else:
            title = f"💡 {resp_clean}"
            body = det_clean
            if not voice_phrase:
                voice_phrase = resp_clean
    else:
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        if lines:
            title = f"💡 {lines[0]}"
            body = "\n".join(lines[1:]) if len(lines) > 1 else ""
            if not voice_phrase:
                voice_phrase = lines[0]

    body = body.strip()
    notify(title, body, icon="accessories-dictionary", timeout_ms=25000, urgency="normal")

    if voice_phrase:
        speak_answer(voice_phrase)

def process_question(text):
    global is_processing
    with proc_lock:
        if is_processing:
            return
        is_processing = True

    try:
        notify("Asistente de Estudio", "🔍 Analizando pregunta...", icon="edit-find", timeout_ms=3000, urgency="low")

        prompt = (
            "Eres un asistente de estudio técnico y preciso. Analiza el siguiente texto de un examen universitario.\n\n"
            "REGLAS OBLIGATORIAS:\n"
            "1. Detecta si contiene UNA SOLA pregunta o MÚLTIPLES preguntas visibles.\n\n"
            "2. Si es UNA SOLA pregunta:\n"
            "- Opción múltiple o Verdadero/Falso:\n"
            "RESPUESTA: [Letra y texto de la opción correcta, o Verdadero/Falso]\n"
            "DETALLE: [Justificación técnica directa en 1-2 líneas]\n"
            "VOZ: [Frase concisa para leer al auricular, ej: Opción B: Tiempo de respuesta menor a 2 segundos]\n"
            "- Pregunta de desarrollo o abierta:\n"
            "RESPUESTA: [Resumen conciso en 1-2 oraciones técnicas]\n"
            "DETALLE: [Respuesta en tono natural de estudiante universitario avanzado, concisa y fluida. Sin clichés como 'En conclusión'. Máximo 3-4 líneas.]\n"
            "VOZ: [Respuesta directa en 1 oración clara]\n\n"
            "3. Si son MÚLTIPLES preguntas (resuelve TODAS las visibles ordenadas):\n"
            "RESPUESTA:\n"
            "• P1: [Letra y texto clave o Verdadero/Falso]\n"
            "• P2: [Letra y texto clave o Verdadero/Falso]\n"
            "DETALLE:\n"
            "• P1: [Justificación técnica concisa en 1 línea]\n"
            "• P2: [Justificación técnica concisa en 1 línea]\n"
            "VOZ: Pregunta 1: [Respuesta corta]. Pregunta 2: [Respuesta corta]. Pregunta 3: [Respuesta corta].\n\n"
            "Estructura obligatoria sin texto previo ni posterior:\n"
            "RESPUESTA: ...\n"
            "DETALLE: ...\n"
            "VOZ: ...\n\n"
            "Pregunta(s):\n" + text
        )

        cmd = [
            "agy",
            "-p",
            prompt,
            "--model",
            "gemini-3.8-flash-low",
            "--effort",
            "low",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "35s"
        ]

        env = os.environ.copy()
        user_home = os.path.expanduser("~")
        env["PATH"] = f"{user_home}/.gemini/antigravity-cli/bin:{user_home}/.local/bin:/usr/local/bin:/usr/bin:" + env.get("PATH", "")

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=40, env=env)
        output = res.stdout.strip()
        if not output and res.stderr:
            output = f"Error: {res.stderr.strip()}"

        if output:
            parse_and_notify(output)

            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(f"PREGUNTA:\n{text}\n\n")
                f.write(f"RESPUESTA:\n{output}\n\n" + "-"*50 + "\n\n")
                f.flush()
    except subprocess.TimeoutExpired:
        notify("⚠️ Asistente de Estudio", "Tiempo de espera agotado al consultar Gemini.", icon="dialog-warning", timeout_ms=5000)
    except Exception as e:
        notify("⚠️ Asistente de Estudio", f"Error: {e}", icon="dialog-error", timeout_ms=5000)
    finally:
        with proc_lock:
            is_processing = False

def process_screen_image(image_path):
    global is_processing
    with proc_lock:
        if is_processing:
            # Si hay un proceso anterior atascado, interrumpirlo para priorizar la captura actual
            try:
                subprocess.run(["pkill", "-9", "-f", "gemini-3.8-flash-low"], check=False)
                subprocess.run(["pkill", "-9", "-f", "agy -p"], check=False)
            except Exception:
                pass
            is_processing = False
        is_processing = True

    try:
        notify("Asistente de Estudio", "📸 Analizando pantalla...", icon="camera-photo", timeout_ms=3500, urgency="low")

        prompt = (
            f"Examina detalladamente la imagen adjunta en el archivo {image_path} utilizando la herramienta view_file para inspeccionar visualmente la captura de pantalla del examen.\n\n"
            "Detecta con máxima precisión técnica TODAS las preguntas y alternativas visibles en la pantalla.\n\n"
            "REGLAS OBLIGATORIAS:\n"
            "1. Detecta si la pantalla contiene UNA SOLA pregunta o MÚLTIPLES preguntas visibles.\n\n"
            "2. Si hay UNA SOLA pregunta:\n"
            "- Opción múltiple o Verdadero/Falso:\n"
            "RESPUESTA: [Letra y texto de la opción correcta, o Verdadero/Falso]\n"
            "DETALLE: [Justificación técnica directa en 1-2 líneas]\n"
            "VOZ: [Frase concisa para leer al auricular, ej: Opción B: Tiempo de respuesta menor a 2 segundos]\n"
            "- Pregunta de desarrollo o abierta:\n"
            "RESPUESTA: [Resumen conciso en 1-2 oraciones técnicas]\n"
            "DETALLE: [Respuesta en tono natural de estudiante universitario avanzado, concisa y fluida. Sin clichés como 'En conclusión'. Máximo 3-4 líneas.]\n"
            "VOZ: [Respuesta directa en 1 oración clara]\n\n"
            "3. Si hay MÚLTIPLES preguntas (resuelve TODAS las visibles ordenadas):\n"
            "RESPUESTA:\n"
            "• P1: [Letra y texto clave o Verdadero/Falso]\n"
            "• P2: [Letra y texto clave o Verdadero/Falso]\n"
            "DETALLE:\n"
            "• P1: [Justificación técnica concisa en 1 línea]\n"
            "• P2: [Justificación técnica concisa en 1 línea]\n"
            "VOZ: Pregunta 1: [Respuesta corta]. Pregunta 2: [Respuesta corta]. Pregunta 3: [Respuesta corta].\n\n"
            "Estructura obligatoria sin texto previo ni posterior:\n"
            "RESPUESTA: ...\n"
            "DETALLE: ...\n"
            "VOZ: ...\n"
        )

        cmd = [
            "agy",
            "-p",
            prompt,
            "--model",
            "gemini-3.8-flash-low",
            "--effort",
            "low",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "45s"
        ]

        env = os.environ.copy()
        user_home = os.path.expanduser("~")
        env["PATH"] = f"{user_home}/.gemini/antigravity-cli/bin:{user_home}/.local/bin:/usr/local/bin:/usr/bin:" + env.get("PATH", "")

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=50, env=env)
        output = res.stdout.strip()
        if not output and res.stderr:
            output = f"Error: {res.stderr.strip()}"

        if output:
            parse_and_notify(output)

            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} [CAPTURA DE PANTALLA] ===\n")
                f.write(f"IMAGEN: {image_path}\n\n")
                f.write(f"RESPUESTA:\n{output}\n\n" + "-"*50 + "\n\n")
                f.flush()
        else:
            notify("⚠️ Asistente de Estudio", "No se detectaron preguntas en la captura.", icon="dialog-warning", timeout_ms=4000)
    except subprocess.TimeoutExpired:
        notify("⚠️ Asistente de Estudio", "Tiempo de espera agotado al consultar Gemini.", icon="dialog-warning", timeout_ms=5000)
    except Exception as e:
        notify("⚠️ Asistente de Estudio", f"Error en captura: {e}", icon="dialog-error", timeout_ms=5000)
    finally:
        with proc_lock:
            is_processing = False

def panic_cleanup():
    global running, is_processing, stealth_server
    print("[DAEMON] ¡ACTIVANDO PROTOCOLO DE PÁNICO!", flush=True)
    running = False
    with proc_lock:
        is_processing = False

    # 1. Silenciar y matar procesos de audio
    try:
        subprocess.run(["pkill", "-9", "pw-play"], check=False)
        subprocess.run(["pkill", "-9", "espeak-ng"], check=False)
    except Exception:
        pass

    # 2. Matar consultas en progreso a Gemini y agy
    try:
        subprocess.run(["pkill", "-9", "-f", "gemini-3.8-flash-low"], check=False)
        subprocess.run(["pkill", "-9", "-f", "agy -p"], check=False)
    except Exception:
        pass

    # 3. Cerrar notificaciones en pantalla de forma instantánea
    close_all_notifications()

    # 4. Vaciar portapapeles
    try:
        subprocess.run(["wl-copy", "--clear"], check=False)
    except Exception:
        pass

    # 5. Restaurar selección visual normal
    set_gtk_stealth(False)

    # 6. Limpiar archivos temporales
    for tmp in ["/tmp/asistente_screen.jpg", "/tmp/asistente_voice.wav", "/tmp/asistente_voice.mp3"]:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    stop_gtk_loop()

    # Cerrar el servidor HTTP sin deadlocks
    if stealth_server:
        try:
            stealth_server.server_close()
        except Exception:
            pass

    # Salir de inmediato y limpiamente para que systemd marque el servicio como inactivo sin timeouts
    os._exit(0)

# --- SERVIDOR HTTP LOCAL PARA MODO SIGILOSO, CAPTURA Y PÁNICO ---
stealth_server = None

class StealthHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cache-Control', 'no-cache')

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ['/test', '/test-bloqueo.html', '/test/']:
            test_path = os.path.join(DATA_DIR, "test-bloqueo.html")
            if os.path.exists(test_path):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self._send_cors_headers()
                self.end_headers()
                with open(test_path, 'rb') as f:
                    self.wfile.write(f.read())
                return

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self._send_cors_headers()
        self.end_headers()
        res = {
            "stealth": True,
            "voice": is_voice_enabled(),
            "voice_mode": get_voice_mode()
        }
        self.wfile.write(json.dumps(res).encode('utf-8'))

    def do_POST(self):
        if self.path == "/solve-screen":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                img_data = data.get('image', '')
                if ',' in img_data:
                    img_data = img_data.split(',', 1)[1]
                img_bytes = base64.b64decode(img_data)
                img_path = "/tmp/asistente_screen.jpg"
                with open(img_path, "wb") as f:
                    f.write(img_bytes)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

                threading.Thread(target=process_screen_image, args=(img_path,), daemon=True).start()
                return
            except Exception as e:
                print("[DAEMON] Error procesando imagen recibida:", e, flush=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(b'{"error": "invalid image"}')
                return
        elif self.path == "/panic":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"status": "panic"}')
            threading.Thread(target=panic_cleanup, daemon=True).start()
            return
        elif self.path == "/repeat-voice":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            threading.Thread(target=repeat_last_voice, daemon=True).start()
            return

        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format, *args):
        pass

def set_gtk_stealth(enable=True):
    gtk_paths = [
        os.path.expanduser("~/.config/gtk-3.0/gtk.css"),
        os.path.expanduser("~/.config/gtk-4.0/gtk.css")
    ]
    rule = "\n/* ASISTENTE_ESTUDIO_STEALTH */\nentry selection, textview text selection, label selection, text selection {\n  background-color: transparent;\n  color: inherit;\n}\n"
    for p in gtk_paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if enable:
                content = ""
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read()
                if "ASISTENTE_ESTUDIO_STEALTH" not in content:
                    with open(p, "a", encoding="utf-8") as f:
                        f.write(rule)
            else:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "ASISTENTE_ESTUDIO_STEALTH" in content:
                        content = re.sub(r'/\* ASISTENTE_ESTUDIO_STEALTH \*/[\s\S]*?\}', '', content).strip()
                        if content:
                            with open(p, "w", encoding="utf-8") as f:
                                f.write(content + "\n")
                        else:
                            os.remove(p)
        except Exception:
            pass

def start_stealth_server():
    global stealth_server
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        stealth_server = ThreadingHTTPServer(('127.0.0.1', 49211), StealthHandler)
        t = threading.Thread(target=stealth_server.serve_forever, daemon=True)
        t.start()
        print("[DAEMON] Servidor stealth de selección invisible activo en puerto 49211", flush=True)
    except Exception as e:
        print("[DAEMON] Error al iniciar servidor stealth:", e, flush=True)

# --- BANDEJA DEL SISTEMA / APPINDICATOR (BARRA SUPERIOR DE GNOME) ---
indicator = None
gtk_thread_obj = None

def setup_indicator():
    global indicator
    try:
        indicator = AppIndicator3.Indicator.new(
            "asistente-estudio",
            "accessories-dictionary-symbolic",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        item_title = Gtk.MenuItem(label="🟢 Asistente de Estudio (Activo)")
        item_title.set_sensitive(False)
        menu.append(item_title)

        menu.append(Gtk.SeparatorMenuItem())

        item_voice = Gtk.CheckMenuItem(label="🔊 Lectura de voz (Respuestas)")
        item_voice.set_active(is_voice_enabled())
        def _on_toggle_voice(widget):
            if widget.get_active():
                open(VOICE_FILE, "w").close()
            else:
                if os.path.exists(VOICE_FILE):
                    try:
                        os.remove(VOICE_FILE)
                    except Exception:
                        pass
        item_voice.connect("toggled", _on_toggle_voice)
        menu.append(item_voice)

        item_notif = Gtk.CheckMenuItem(label="🔔 Notificaciones en pantalla")
        item_notif.set_active(is_notif_enabled())
        def _on_toggle_notif(widget):
            if widget.get_active():
                if os.path.exists(NOTIF_MUTED_FILE):
                    try:
                        os.remove(NOTIF_MUTED_FILE)
                    except Exception:
                        pass
            else:
                open(NOTIF_MUTED_FILE, "w").close()
        item_notif.connect("toggled", _on_toggle_notif)
        menu.append(item_notif)

        item_last = Gtk.MenuItem(label="📋 Ver última respuesta")
        def _on_show_last(widget):
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "r", encoding="utf-8") as f:
                        blocks = f.read().split("--------------------------------------------------\n\n")
                        valid_blocks = [b.strip() for b in blocks if b.strip()]
                        if valid_blocks:
                            last_b = valid_blocks[-1]
                            notify("Última Consulta", last_b[:300], icon="accessories-dictionary", timeout_ms=12000, force=True)
                        else:
                            notify("Asistente de Estudio", "No hay consultas registradas aún.", icon="dialog-information", timeout_ms=3000, force=True)
                except Exception:
                    pass
            else:
                notify("Asistente de Estudio", "No hay consultas registradas aún.", icon="dialog-information", timeout_ms=3000, force=True)
        item_last.connect("activate", _on_show_last)
        menu.append(item_last)

        item_repeat = Gtk.MenuItem(label="🔁 Repetir respuesta al oído (Alt+R)")
        def _on_repeat(widget):
            threading.Thread(target=repeat_last_voice, daemon=True).start()
        item_repeat.connect("activate", _on_repeat)
        menu.append(item_repeat)

        menu.append(Gtk.SeparatorMenuItem())

        item_panic = Gtk.MenuItem(label="🚨 Botón de Pánico (Apagar)")
        def _on_panic(widget):
            threading.Thread(target=panic_cleanup, daemon=True).start()
        item_panic.connect("activate", _on_panic)
        menu.append(item_panic)

        menu.show_all()
        indicator.set_menu(menu)
        print("[DAEMON] Icono de barra superior (AppIndicator) activo en GNOME.", flush=True)
    except Exception as e:
        print("[DAEMON] No se pudo inicializar AppIndicator:", e, flush=True)

def start_gtk_loop():
    global gtk_thread_obj
    try:
        setup_indicator()
        gtk_thread_obj = threading.Thread(target=Gtk.main, daemon=True)
        gtk_thread_obj.start()
    except Exception as e:
        print("[DAEMON] Error al iniciar loop GTK:", e, flush=True)

def stop_gtk_loop():
    def _do_quit():
        global indicator
        try:
            if indicator:
                indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        except Exception:
            pass
        try:
            Gtk.main_quit()
        except Exception:
            pass
        return False

    try:
        GLib.idle_add(_do_quit)
    except Exception:
        pass

def cleanup():
    global running, stealth_server
    running = False
    stop_gtk_loop()
    set_gtk_stealth(False)
    if stealth_server:
        try:
            stealth_server.server_close()
        except Exception:
            pass
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

atexit.register(cleanup)

def signal_handler(signum, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


def main():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    set_gtk_stealth(True)
    start_stealth_server()
    start_gtk_loop()

    display_name = os.environ.get("DISPLAY", ":0").encode('utf-8')
    display = x11.XOpenDisplay(display_name)
    if not display:
        notify("⚠️ Asistente de Estudio", "No se pudo conectar al display gráfico.", icon="dialog-error")
        sys.exit(1)

    root = x11.XDefaultRootWindow(display)
    win = x11.XCreateSimpleWindow(display, root, 0, 0, 1, 1, 0, 0, 0)
    clip_atom = x11.XInternAtom(display, b'CLIPBOARD', 0)
    utf8_atom = x11.XInternAtom(display, b'UTF8_STRING', 0)
    prop_atom = x11.XInternAtom(display, b'STUDY_CLIP_PROP', 0)

    xfixes.XFixesQueryExtension(display, byref(xfixes_event_base), byref(xfixes_error_base))
    xfixes.XFixesSelectSelectionInput(display, root, clip_atom, 1)
    x11.XFlush(display)

    def read_clipboard():
        x11.XConvertSelection(display, clip_atom, utf8_atom, prop_atom, win, 0)
        x11.XFlush(display)

        ev = XEvent()
        t0 = time.time()
        while time.time() - t0 < 0.8:
            while x11.XPending(display):
                x11.XNextEvent(display, byref(ev))
                if ev.type == 31: # SelectionNotify
                    actual_type = c_ulong()
                    actual_format = c_int()
                    nitems = c_ulong()
                    bytes_after = c_ulong()
                    prop_return = POINTER(c_ubyte)()

                    x11.XGetWindowProperty(
                        display, win, prop_atom, 0, 1024*1024, 0, 0,
                        byref(actual_type), byref(actual_format),
                        byref(nitems), byref(bytes_after), byref(prop_return)
                    )
                    text = None
                    if prop_return and nitems.value > 0:
                        text = ctypes.string_at(prop_return, nitems.value).decode('utf-8', errors='replace')
                        x11.XFree(prop_return)
                        x11.XDeleteProperty(display, win, prop_atom)
                    return text
            time.sleep(0.02)
        return None

    last_text = ""
    last_event_time = 0.0
    fd = x11.XConnectionNumber(display)
    print(f"[DAEMON] Iniciado. DISPLAY={display_name.decode()}, xfixes_event_base={xfixes_event_base.value}, fd={fd}", flush=True)

    while running:
        r, _, _ = select.select([fd], [], [], 1.0)
        if not running:
            break
        if r:
            while x11.XPending(display):
                ev = XEvent()
                x11.XNextEvent(display, byref(ev))
                if ev.type == xfixes_event_base.value:
                    text = read_clipboard()
                    now = time.time()
                    print(f"[DAEMON] XFixes evento recibido. Texto={repr(text[:40]) if text else 'None'}", flush=True)
                    if text and (text != last_text or (now - last_event_time) > 2.0):
                        last_text = text
                        last_event_time = now
                        clean = text.strip()
                        if len(clean) >= 10:
                            print(f"[DAEMON] Lanzando hilo para pregunta: {repr(clean[:40])}", flush=True)
                            if not is_processing:
                                t = threading.Thread(target=process_question, args=(clean,), daemon=True)
                                t.start()
                            else:
                                notify("Asistente de Estudio", "⏳ Procesando pregunta anterior, un momento...", icon="edit-find", timeout_ms=2000, urgency="low")

    try:
        x11.XDestroyWindow(display, win)
        x11.XCloseDisplay(display)
    except Exception:
        pass
    cleanup()

if __name__ == "__main__":
    main()
