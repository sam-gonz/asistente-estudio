#!/usr/bin/env bash
# Script de desinstalación para Asistente de Estudio
set -euo pipefail

echo "Deteniendo servicio..."
systemctl --user stop asistente-estudio.service 2>/dev/null || true
systemctl --user disable asistente-estudio.service 2>/dev/null || true

echo "Eliminando archivos instalados..."
rm -f "$HOME/.local/bin/asistente-estudio"
rm -f "$HOME/.local/bin/asistente-estudio-daemon.py"
rm -f "$HOME/.local/bin/probar-ventana-protegida"
rm -f "$HOME/.config/systemd/user/asistente-estudio.service"

systemctl --user daemon-reload 2>/dev/null || true

read -p "¿Deseas eliminar también los datos y configuraciones en ~/.local/share/asistente-estudio? (s/N): " -r CONFIRM
if [[ "$CONFIRM" =~ ^[sS]$ ]]; then
    rm -rf "$HOME/.local/share/asistente-estudio"
    echo "Directorio de datos eliminado."
fi

echo "Desinstalación completada."
