#!/usr/bin/env bash
# Script de instalación para Asistente de Estudio
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/asistente-estudio"

echo "=========================================="
echo "  Instalador: Asistente de Estudio"
echo "=========================================="

# 1. Crear directorios necesarios
mkdir -p "$BIN_DIR" "$SYSTEMD_USER_DIR" "$DATA_DIR/moodle-extension" "$DATA_DIR/prueba-navegador"

# 2. Instalar binarios ejecutables
echo "[1/5] Instalando ejecutables en $BIN_DIR..."
install -m 755 "$REPO_DIR/bin/asistente-estudio" "$BIN_DIR/asistente-estudio"
install -m 755 "$REPO_DIR/bin/asistente-estudio-daemon.py" "$BIN_DIR/asistente-estudio-daemon.py"
install -m 755 "$REPO_DIR/bin/probar-ventana-protegida" "$BIN_DIR/probar-ventana-protegida"

# 3. Instalar servicio systemd de usuario
echo "[2/5] Instalando servicio systemd..."
cp "$REPO_DIR/systemd/asistente-estudio.service" "$SYSTEMD_USER_DIR/asistente-estudio.service"
systemctl --user daemon-reload

# 4. Instalar extensión y páginas de prueba
echo "[3/5] Configurando extensión y datos en $DATA_DIR..."
cp -r "$REPO_DIR/extension/"* "$DATA_DIR/moodle-extension/"
cp "$REPO_DIR/test-pages/test-bloqueo.html" "$DATA_DIR/"
cp "$REPO_DIR/test-pages/test-ventana-protegida.html" "$DATA_DIR/"
ln -sf "$DATA_DIR/test-ventana-protegida.html" "$DATA_DIR/prueba-navegador/index.html"

# 5. Comprobar dependencias del sistema
echo "[4/5] Comprobando dependencias recomendadas..."
check_dep() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ✔ $1 encontrado."
  else
    echo "  ⚠ $1 NO encontrado: $2"
  fi
}

check_dep "python3" "Requerido para el demonio."
check_dep "notify-send" "Requerido para notificaciones de escritorio (libnotify)."
check_dep "pw-play" "Requerido para salida de audio (PipeWire)."
check_dep "agy" "Requerido para la IA de Antigravity (CLI)."
check_dep "edge-tts" "Opcional: proporciona voz neuronal ultra realista."
check_dep "espeak-ng" "Opcional: motor TTS de respaldo offline."

echo ""
echo "[5/5] ¡Instalación completada con éxito!"
echo ""
echo "Pasos siguientes recomendados:"
echo "  1. Iniciar asistente:"
echo "     asistente-estudio start"
echo ""
echo "  2. Cargar la extensión en Brave/Chrome:"
echo "     - Abre brave://extensions o chrome://extensions"
echo "     - Activa 'Modo de desarrollador'"
echo "     - Pulsa 'Cargar descomprimida' y selecciona:"
echo "       $DATA_DIR/moodle-extension"
echo "     - En chrome://extensions/shortcuts asigna Alt+S a 'Capturar pestaña'"
echo ""
echo "  3. Configurar atajos de pánico en GNOME (opcional pero recomendado):"
echo "     - Alt+X o Ctrl+Alt+P -> 'asistente-estudio panic'"
echo "=========================================="
