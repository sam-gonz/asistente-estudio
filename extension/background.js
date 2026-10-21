// Capturar el área visible de la ventana que originó el atajo.
let lastCaptureAt = -Infinity;
let lastCaptureTask = null;

function triggerCapture(preferredWindowId = null) {
  // Evitar capturas repetidas por pulsaciones rápidas y respetar
  // el límite de capturas del navegador.
  const now = Date.now();
  if (now - lastCaptureAt < 600 && lastCaptureTask) return lastCaptureTask;
  lastCaptureAt = now;
  lastCaptureTask = performCapture(preferredWindowId);
  return lastCaptureTask;
}

async function performCapture(preferredWindowId) {
  let timeoutId;
  try {
    let windowId = preferredWindowId;
    if (!Number.isInteger(windowId) || windowId < 0) {
      windowId = await new Promise((resolve, reject) => {
        chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
          const error = chrome.runtime.lastError;
          if (error) return reject(new Error(error.message));
          if (!tabs || !tabs.length || !Number.isInteger(tabs[0].windowId)) {
            return reject(new Error('No hay una ventana activa para capturar.'));
          }
          resolve(tabs[0].windowId);
        });
      });
    }
    const dataUrl = await new Promise((resolve, reject) => {
      chrome.tabs.captureVisibleTab(windowId, { format: 'jpeg', quality: 80 }, (image) => {
        const error = chrome.runtime.lastError;
        if (error || !image) return reject(new Error(error ? error.message : 'Captura vacía.'));
        resolve(image);
      });
    });
    const controller = new AbortController();
    timeoutId = setTimeout(() => controller.abort(), 12000);
    const res = await fetch('http://127.0.0.1:49211/solve-screen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataUrl }),
      signal: controller.signal
    });
    if (!res.ok) throw new Error(`El asistente respondió con estado ${res.status}.`);
    return { ok: true };
  } catch (err) {
    console.warn('[Asistente] Error en captura:', err);
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

// El navegador gestiona Alt+S. No se inyectan manejadores en las páginas.
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === 'capture-screen') {
    triggerCapture(tab && tab.windowId);
  }
});
