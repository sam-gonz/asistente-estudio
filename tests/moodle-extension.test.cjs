// Pruebas de captura con APIs simuladas; no realizan capturas ni consultas reales.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const directory = process.env.EXTENSION_DIR || (fs.existsSync(path.join(__dirname, '../extension')) ? path.join(__dirname, '../extension') : path.join(__dirname, '../moodle-extension'));
const source = name => fs.readFileSync(path.join(directory, name), 'utf8');

function background(options = {}) {
  let command;
  let now = 1000;
  const captures = [], requests = [], cleared = [];
  const runtime = {};
  const context = vm.createContext({
    AbortController, console: { warn() {} },
    Date: { now: () => now },
    setTimeout: () => 1, clearTimeout: id => cleared.push(id),
    fetch: async (url, init) => {
      requests.push({ url, init });
      if (options.fetchError) throw new Error('Servicio local inaccesible');
      return { ok: !options.httpError, status: 503 };
    },
    chrome: {
      runtime, commands: { onCommand: { addListener(fn) { command = fn; } } },
      tabs: {
        query(query, callback) { callback(options.tabs || [{ id: 1, windowId: 10 }]); },
        captureVisibleTab(id, settings, callback) {
          captures.push({ id, settings });
          runtime.lastError = options.captureError ? { message: 'Captura deshabilitada por el navegador' } : undefined;
          callback(options.captureError ? undefined : 'data:image/jpeg;base64,dGVzdA==');
          runtime.lastError = undefined;
        }
      }
    }
  });
  vm.runInContext(source('background.js'), context);
  return {
    captures, requests, cleared, command,
    advance(ms) { now += ms; },
    capture(id) { return vm.runInContext(`triggerCapture(${JSON.stringify(id)})`, context); }
  };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('Alt+S conserva el comando original y captura la ventana emergente de origen', async () => {
  const fixture = background();
  fixture.command('capture-screen', { windowId: 72 });
  await settle();
  assert.equal(fixture.captures[0].id, 72);
  assert.equal(fixture.captures[0].settings.format, 'jpeg');
  const request = fixture.requests[0];
  assert.equal(request.url, 'http://127.0.0.1:49211/solve-screen');
  assert.equal(request.init.method, 'POST');
  assert.equal(JSON.parse(request.init.body).image, 'data:image/jpeg;base64,dGVzdA==');
  assert.equal(fixture.cleared.length, 1);
});

test('sin ventana explícita captura la ventana activa', async () => {
  const fixture = background();
  fixture.command('capture-screen');
  await settle();
  assert.equal(fixture.captures[0].id, 10);
  assert.equal(fixture.requests.length, 1);
});

test('las pulsaciones rápidas no duplican capturas y se permite una nueva captura después', async () => {
  const fixture = background();
  fixture.command('capture-screen', { windowId: 72 });
  fixture.command('capture-screen', { windowId: 72 });
  await settle();
  assert.equal(fixture.captures.length, 1);
  assert.equal(fixture.requests.length, 1);
  fixture.advance(601);
  fixture.command('capture-screen', { windowId: 73 });
  await settle();
  assert.equal(fixture.captures.length, 2);
  assert.equal(fixture.captures[1].id, 73);
});

test('un error del navegador no envía una imagen ni se comunica como éxito', async () => {
  const fixture = background({ captureError: true });
  const response = await fixture.capture(72);
  assert.equal(response.ok, false);
  assert.match(response.error, /deshabilitada/);
  assert.equal(fixture.requests.length, 0);
});

test('un error HTTP del asistente se devuelve y libera el temporizador', async () => {
  const fixture = background({ httpError: true });
  const result = await fixture.capture(72);
  assert.equal(result.ok, false);
  assert.match(result.error, /503/);
  assert.equal(fixture.cleared.length, 1);
});

test('si el servicio está apagado la captura falla de forma controlada', async () => {
  const fixture = background({ fetchError: true });
  const result = await fixture.capture(72);
  assert.equal(result.ok, false);
  assert.match(result.error, /inaccesible/);
  assert.equal(fixture.cleared.length, 1);
});

test('sin pestañas disponibles no captura una ventana arbitraria', async () => {
  const fixture = background({ tabs: [] });
  assert.equal((await fixture.capture(null)).ok, false);
  assert.equal(fixture.captures.length, 0);
  assert.equal(fixture.requests.length, 0);
});

test('el worker no consulta ni captura nada al arrancar y solo atiende el comando de captura', async () => {
  const fixture = background();
  for (const command of ['panic-button', 'repeat-voice', 'unknown']) fixture.command(command);
  await settle();
  assert.equal(fixture.captures.length, 0);
  assert.equal(fixture.requests.length, 0);
});

test('el manifiesto conserva Alt+S y los permisos necesarios sin inyectar nada en las páginas', () => {
  const manifest = JSON.parse(source('manifest.json'));
  assert.deepEqual(Object.keys(manifest.commands), ['capture-screen']);
  assert.equal(manifest.commands['capture-screen'].suggested_key.default, 'Alt+S');
  assert.deepEqual(manifest.permissions, ['activeTab']);
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.web_accessible_resources, undefined);
  assert.deepEqual(fs.readdirSync(directory).sort(), ['background.js', 'manifest.json']);
});
