// Eventos y DOM simulados: no verifican capturas reales ni el cliente de SEB.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const htmlPath = fs.existsSync(path.join(__dirname, '../test-pages/test-ventana-protegida.html'))
  ? path.join(__dirname, '../test-pages/test-ventana-protegida.html')
  : path.join(__dirname, '../test-ventana-protegida.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];

class Node {
  constructor() { this.listeners = {}; this.children = []; this.dataset = {}; this.hidden = false; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async emit(type, props = {}) {
    const event = { type, target: this, key: '', preventDefault() { this.defaultPrevented = true; }, ...props };
    for (const fn of this.listeners[type] || []) await fn(event);
    return event;
  }
  setAttribute() {}
  removeAttribute() {}
  prepend(child) { this.children.unshift(child); }
  appendChild(child) { this.children.push(child); }
  querySelector() { return this.children[0] || null; }
  closest() { return null; }
  replaceChildren() { this.children = []; }
}

function page() {
  const nodes = new Map();
  const byId = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const document = new Node();
  document.body = new Node();
  document.body.dataset.contentHidden = 'off';
  let focused = true;
  document.hasFocus = () => focused;
  document.hidden = false;
  document.getElementById = byId;
  document.querySelector = () => ({ value: 'b' });
  document.createElement = tag => {
    const node = new Node();
    if (tag === 'iframe') node.contentDocument = { body: new Node() };
    return node;
  };
  document.documentElement = { requestFullscreen: async () => {
    document.fullscreenElement = document.documentElement;
    await document.emit('fullscreenchange');
  } };
  document.exitFullscreen = async () => {
    document.fullscreenElement = null;
    await document.emit('fullscreenchange');
  };
  const window = new Node();
  window.getSelection = () => null;
  const context = vm.createContext({ document, window, Element: Node, Date, URL, location: { hash: '', href: 'http://127.0.0.1:8765/' }, setTimeout: fn => fn() });
  vm.runInContext(source, context);
  return { document, window, byId, context, focus(value) { focused = value; } };
}

test('Alt+S intenta cancelarse si llega al documento, sin cancelar otra combinación', async () => {
  const p = page();
  assert.equal((await p.document.emit('keydown', { key: 's', altKey: true })).defaultPrevented, true);
  assert.match(p.byId('shortcut-state').textContent, /1 eventos recibidos/);
  assert.equal((await p.document.emit('keydown', { key: 's', altKey: true, ctrlKey: true })).defaultPrevented, undefined);
  await p.byId('seb-simulation').emit('change', { target: { checked: false } });
  assert.equal((await p.document.emit('keyup', { key: 's', altKey: true })).defaultPrevented, undefined);
});

test('portapapeles y menú se bloquean en simulación y se liberan al apagar los bloqueos', async () => {
  const p = page();
  for (const type of ['copy', 'cut', 'paste', 'contextmenu']) {
    assert.equal((await p.document.emit(type)).defaultPrevented, true);
  }
  await p.byId('blocks').emit('change', { target: { checked: false } });
  for (const type of ['copy', 'cut', 'paste', 'contextmenu']) {
    assert.equal((await p.document.emit(type)).defaultPrevented, undefined);
  }
});

test('foco y visibilidad ocultan contenido, y volver o apagar la simulación lo restaura', async () => {
  const p = page();
  p.focus(false); await p.window.emit('blur');
  assert.equal(p.document.body.dataset.contentHidden, 'on');
  assert.equal(p.byId('focus-notice').hidden, false);
  p.focus(true); await p.window.emit('focus');
  assert.equal(p.document.body.dataset.contentHidden, 'off');
  p.document.hidden = true; await p.document.emit('visibilitychange');
  assert.equal(p.document.body.dataset.contentHidden, 'on');
  await p.byId('seb-simulation').emit('change', { target: { checked: false } });
  assert.equal(p.document.body.dataset.contentHidden, 'off');
});

test('enfocar el marco manteniendo foco del documento no oculta el contenido', async () => {
  const p = page();
  await p.window.emit('blur');
  assert.equal(p.document.body.dataset.contentHidden, 'off');
});

test('pantalla completa permite entrar, salir y comunica un rechazo del navegador', async () => {
  const p = page();
  await p.byId('fullscreen').emit('click');
  assert.equal(p.byId('fullscreen-state').textContent, 'Pantalla completa activada');
  await p.byId('fullscreen').emit('click');
  assert.equal(p.byId('fullscreen-state').textContent, 'Pantalla completa desactivada');
  p.document.documentElement.requestFullscreen = async () => { throw new Error('Denegado'); };
  await p.byId('fullscreen').emit('click');
  assert.equal(p.byId('notice').hidden, false);
  assert.match(p.byId('notice').textContent, /No se pudo/);
});

test('el iframe comparte los bloqueos, permite desactivarlos y registra Alt+S', async () => {
  const p = page();
  await p.byId('open-frame').emit('click');
  const frame = p.byId('frame-area').children[0];
  await frame.emit('load');
  const frameDocument = new Node();
  // Las funciones llamadas desde srcdoc deben existir en el objeto window padre.
  const frameContext = vm.createContext({ document: frameDocument, parent: p.context });
  vm.runInContext(frame.srcdoc.match(/<script>([\s\S]*?)<\/script>/)[1], frameContext);
  assert.equal((await frameDocument.emit('keydown', { altKey: true, key: 's' })).defaultPrevented, true);
  assert.match(p.byId('shortcut-state').textContent, /marco/);
  await p.byId('blocks').emit('change', { target: { checked: false } });
  assert.equal(frame.contentDocument.body.dataset.blocks, 'off');
  assert.equal((await frameDocument.emit('copy')).defaultPrevented, undefined);
  assert.equal((await frameDocument.emit('keyup', { altKey: true, key: 's' })).defaultPrevented, undefined);
});

test('los controles de respuesta conservan su funcionamiento con la simulación activa', async () => {
  const p = page();
  await p.byId('save-answer').emit('click');
  assert.match(p.byId('answer-result').textContent, /respuesta B/);
});
