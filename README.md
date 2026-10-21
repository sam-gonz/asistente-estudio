# Asistente de Estudio 🎓⚡

> Herramienta discreta de asistencia y estudio para Linux que captura preguntas en pantalla o portapapeles y las analiza con IA multimodal vía Antigravity CLI (`agy`), entregando las respuestas al instante mediante notificaciones de escritorio y voz neuronal al oído.

---

## 🌟 Características Principales

- 📸 **Captura Silenciosa de Pantalla (`Alt+S`)**:
  - Extensión para navegadores Chromium (Brave, Chrome) bajo Manifest V3.
  - Captura el área visible con `chrome.tabs.captureVisibleTab` y la envía de forma segura al demonio local (puerto `49211`).
  - Cero inyección de scripts extraños en el DOM de las páginas.
- 📋 **Monitor Nativo X11 de Portapapeles (`Ctrl+C`)**:
  - Basado en eventos de la extensión XFixes de X11 (sin polling de CPU ni demoras).
  - Activa la resolución automática al copiar una pregunta.
  - Incluye modo de selección sigilosa (GTK CSS stealth) para no dejar rastros visuales.
- 🧠 **IA Multimodal con Antigravity (`agy`)**:
  - Consulta de baja latencia con Gemini (`gemini-3.8-flash-low`).
  - Capaz de resolver preguntas individuales o múltiples preguntas simultáneas detectadas en una captura de examen.
  - Respuestas estructuradas: `RESPUESTA` (opción y texto), `DETALLE` (justificación técnica concisa) y `VOZ` (resumen para audio).
- 🎧 **Lectura Neuronal al Oído (TTS)**:
  - Síntesis de voz ultra natural con Microsoft Edge TTS (`es-MX-JorgeNeural`, `es-PA-RobertoNeural`, etc.) y reproducción PipeWire (`pw-play`).
  - Atajo `Alt+R` para repetir la última respuesta al auricular.
  - Modo discreto (ocultar notificaciones visuales y recibir solo voz).
  - Respaldo offline automático con `espeak-ng` si se pierde la conexión.
- 🚨 **Botón de Pánico Inmediato (`Alt+X` / `Ctrl+Alt+P`)**:
  - Detiene y mata al instante cualquier reproducción de audio (`pw-play`, `espeak-ng`).
  - Cancela las consultas en progreso a Gemini y subprocesos `agy`.
  - Cierra de inmediato todas las notificaciones activas en el escritorio.
  - Limpia el portapapeles (`wl-copy --clear`) y borra archivos temporales.
  - Detiene el servicio y restaura los estilos del sistema.
- 🖥️ **Bandeja del Sistema (AppIndicator)**:
  - Icono en el panel superior de GNOME para activar/desactivar la voz, silenciar notificaciones visuales, repetir respuesta o disparar pánico.
- ⚙️ **Gestión como Servicio de Usuario (Systemd)**:
  - Se ejecuta en segundo plano como servicio gestionado con reinicio automático.

---

## 📁 Estructura del Proyecto

```text
asistente-estudio/
├── bin/
│   ├── asistente-estudio            # CLI para gestionar y consultar el estado
│   ├── asistente-estudio-daemon.py   # Demonio principal (X11, HTTP 49211, AppIndicator, agy)
│   └── probar-ventana-protegida     # Servidor local para pruebas de captura
├── systemd/
│   └── asistente-estudio.service    # Unidad systemd para el usuario
├── extension/                       # Extensión para Chromium/Brave (MV3)
│   ├── manifest.json
│   └── background.js
├── tests/                           # Pruebas automatizadas unitarias y de simulación
│   ├── moodle-extension.test.cjs
│   └── ventana-protegida.test.cjs
├── test-pages/                      # Entornos de prueba locales
│   ├── test-bloqueo.html
│   ├── test-ventana-protegida.html
│   └── index.html -> test-ventana-protegida.html
├── install.sh                       # Instalador automático en el sistema
├── uninstall.sh                     # Desinstalador
└── README.md
```

---

## 🛠️ Requisitos Previos

- **Sistema Operativo**: Linux (GNOME / X11 o Wayland con soporte XWayland).
- **Herramientas del sistema**:
  - `python3` (con soporte GTK3 y libappindicator si se desea icono en bandeja).
  - `notify-send` (`libnotify`).
  - `pw-play` (`pipewire-audio` o `pipewire-utils`).
  - `wl-copy` o utilidades de portapapeles.
- **CLI de Antigravity (`agy`)**:
  - Asegúrate de tener `agy` disponible en tu `PATH` o en `~/.gemini/antigravity-cli/bin/agy`.
- **Opcional (para voz neuronal)**:
  - `edge-tts` (vía `pip` o `uv`).
  - `espeak-ng` (respaldo offline).

---

## 🚀 Instalación Rápida

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/sam-gonz/asistente-estudio.git
   cd asistente-estudio
   ```

2. **Ejecutar el instalador:**
   ```bash
   ./install.sh
   ```
   El instalador copiará los ejecutables a `~/.local/bin/`, el servicio a `~/.config/systemd/user/` y la extensión a `~/.local/share/asistente-estudio/moodle-extension/`.

3. **Cargar la extensión en el navegador (Brave / Chrome):**
   - Ve a `brave://extensions` o `chrome://extensions`.
   - Activa el **Modo de desarrollador**.
   - Haz clic en **Cargar descomprimida** y selecciona la carpeta `extension/` (o `~/.local/share/asistente-estudio/moodle-extension`).
   - Ve a `brave://extensions/shortcuts` y verifica que el atajo para **Capturar la pestaña y enviarla al asistente** esté configurado en `Alt+S` con alcance *En el navegador*.

4. **Configurar los atajos de teclado en el sistema (GNOME):**
   - Abre *Configuración* -> *Teclado* -> *Ver y personalizar atajos* -> *Atajos personalizados*.
   - Añadir:
     - **Nombre**: Asistente Pánico
     - **Comando**: `asistente-estudio panic`
     - **Atajo**: `Alt+X` o `Ctrl+Alt+P`

---

## 📖 Guía de Uso

### Iniciar y Detener el Asistente

```bash
# Iniciar en segundo plano
asistente-estudio start

# Ver estado, atajos, modelo y consultas registradas
asistente-estudio status

# Detener el asistente
asistente-estudio stop

# Alternar encendido / apagado
asistente-estudio toggle
```

### Modos de Consulta

1. **Captura de Pantalla (`Alt+S`)**:
   - Pulsa `Alt+S` en cualquier pestaña del navegador.
   - La extensión capturará el área visible y la enviará al asistente.
   - Recibirás una notificación y/o lectura con la solución a todas las preguntas visibles.

2. **Copiar Pregunta (`Ctrl+C`)**:
   - Selecciona el texto de una pregunta y cópialo (`Ctrl+C`).
   - El monitor XFixes detectará el cambio y enviará la consulta a Antigravity automáticamente.

3. **Repetir Última Respuesta (`Alt+R` o comando CLI)**:
   ```bash
   asistente-estudio repeat
   ```

4. **Botón de Pánico (`Alt+X` / `Ctrl+Alt+P`)**:
   ```bash
   asistente-estudio panic
   ```

---

## 🎙️ Configuración de Voz y Notificaciones

El asistente soporta voces humanas neuronales vía Azure / Edge TTS:

```bash
# Activar / desactivar voz
asistente-estudio voice on
asistente-estudio voice off
asistente-estudio voice toggle

# Listar voces disponibles
asistente-estudio voice list

# Cambiar la voz activa
asistente-estudio voice set jorge     # México (recomendada, neutra y ultra natural)
asistente-estudio voice set roberto   # Panamá
asistente-estudio voice set margarita # Panamá
asistente-estudio voice set dalia     # México mujer

# Probar voz
asistente-estudio voice test

# Modo discreto: silenciar notificaciones visuales y dejar solo la voz
asistente-estudio notif off
```

---

## 🧪 Pruebas Automatizadas

El repositorio incluye suites de pruebas con APIs y DOM simulados que validan la extensión y las restricciones de eventos:

```bash
# Pruebas de la extensión del navegador (manifiesto, atajos, errores HTTP, buffers)
node tests/moodle-extension.test.cjs

# Pruebas de eventos y comportamiento en ventana protegida / SEB
node tests/ventana-protegida.test.cjs
```

Para probar manualmente el comportamiento de captura en una ventana local con restricciones:
```bash
probar-ventana-protegida
```
Abre `http://127.0.0.1:8765/` en tu navegador.

---

## 🔒 Privacidad y Responsabilidad

- Este software fue creado con fines didácticos, de investigación técnica y accesibilidad para el estudio personal asistido por inteligencia artificial.
- El usuario es el único responsable del uso que se le dé conforme a las políticas y reglamentos de sus respectivas plataformas o instituciones.
