#!/usr/bin/env node
/**
 * TechCargo WhatsApp Bot — Cloud-ready version.
 * Variables de entorno:
 *   CHROMIUM_PATH      → path al ejecutable de Chrome/Chromium (default: /usr/bin/chromium)
 *   WPP_SESSION_PATH   → directorio donde guardar la sesión (default: /data/.wpp_session)
 *   APP_DATA_DIR       → directorio del token.json (default: /data)
 *   GOOGLE_CREDENTIALS_JSON → contenido del token.json (si no existe el archivo)
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const https  = require('https');
const fs     = require('fs');
const path   = require('path');

// ─── Configuración cloud ──────────────────────────────────────────────────────

const DATA_DIR      = process.env.APP_DATA_DIR    || '/data';
const SESSION_PATH  = process.env.WPP_SESSION_PATH || path.join(DATA_DIR, '.wpp_session');
const CHROME_PATH   = process.env.CHROMIUM_PATH    || '/usr/bin/chromium';
const TOKEN_FILE    = path.join(DATA_DIR, 'token.json');
const SPEC_ID       = '1MjTLP-7ZRqmaUa4uMkY3O7gXsMT8U9fnCk8fOhHUaVs';

// Crear directorios si no existen
fs.mkdirSync(DATA_DIR, { recursive: true });
fs.mkdirSync(SESSION_PATH, { recursive: true });

// Inicializar token desde env var si no existe
if (!fs.existsSync(TOKEN_FILE)) {
  const credsJson = process.env.GOOGLE_CREDENTIALS_JSON;
  if (credsJson) {
    fs.writeFileSync(TOKEN_FILE, credsJson);
    console.log('[Token] Inicializado desde GOOGLE_CREDENTIALS_JSON');
  } else {
    console.error('[Token] ERROR: No hay token.json ni GOOGLE_CREDENTIALS_JSON');
    process.exit(1);
  }
}

// ─── Google Sheets helpers ────────────────────────────────────────────────────

function loadToken() {
  return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
}

function saveToken(data) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(data));
}

function refreshToken(data) {
  return new Promise((resolve, reject) => {
    const body = new URLSearchParams({
      client_id:     data.client_id,
      client_secret: data.client_secret,
      refresh_token: data.refresh_token,
      grant_type:    'refresh_token',
    }).toString();

    const req = https.request({
      hostname: 'oauth2.googleapis.com',
      path: '/token',
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }, res => {
      let raw = '';
      res.on('data', d => raw += d);
      res.on('end', () => {
        const resp = JSON.parse(raw);
        data.token = resp.access_token;
        const exp = new Date(Date.now() + resp.expires_in * 1000);
        data.expiry = exp.toISOString().replace(/\.\d+Z$/, 'Z');
        saveToken(data);
        resolve(data);
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function getToken() {
  let data = loadToken();
  const expiry = new Date(data.expiry);
  if (Date.now() >= expiry - 5 * 60 * 1000) {
    data = await refreshToken(data);
  }
  return data.token;
}

function sheetsGet(range) {
  return new Promise(async (resolve, reject) => {
    const token = await getToken();
    const url = `/v4/spreadsheets/${SPEC_ID}/values/${encodeURIComponent(range)}`;
    const req = https.request({
      hostname: 'sheets.googleapis.com',
      path: url,
      headers: { Authorization: `Bearer ${token}` },
    }, res => {
      let raw = '';
      res.on('data', d => raw += d);
      res.on('end', () => {
        try { resolve(JSON.parse(raw).values || []); }
        catch(e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

function getVal(row, i) {
  return row && row[i] ? String(row[i]).trim() : '';
}

function parseMonto(v) {
  return parseFloat(String(v).replace(',', '.').replace('$', '').replace(/ /g, '') || 0) || 0;
}

// ─── Lógica de cuentas ───────────────────────────────────────────────────────

async function getCuentaCliente(nombre) {
  const [rowsD, rowsM] = await Promise.all([
    sheetsGet('DEUDORES!A2:G'),
    sheetsGet('MOVIMIENTOS!A2:G'),
  ]);

  const query = nombre.toLowerCase().trim();
  const matches = rowsD.filter(r => getVal(r, 1).toLowerCase().includes(query));

  if (matches.length === 0) {
    return `❌ No encontré ningún cliente con el nombre *${nombre}*.\n\nUsá *!cc lista* para ver todos los clientes.`;
  }

  if (matches.length > 1) {
    const lista = matches.map(r => `• ${getVal(r, 1)}`).join('\n');
    return `⚠️ Encontré varios clientes con ese nombre:\n${lista}\n\nSé más específico.`;
  }

  const cliente = matches[0];
  const cid     = getVal(cliente, 0);
  const nombre_ = getVal(cliente, 1);
  const resp    = getVal(cliente, 2);
  const monto0  = parseMonto(getVal(cliente, 3));
  const tipo    = getVal(cliente, 4);
  const fechaAlta = getVal(cliente, 5);

  let saldo = monto0;
  const movs = rowsM.filter(r => getVal(r, 1) === cid);
  for (const m of movs) {
    const t = getVal(m, 3);
    const v = parseMonto(getVal(m, 4));
    if (t.includes('Pago'))  saldo -= v;
    if (t.includes('Cargo')) saldo += v;
  }

  const saldoStr = saldo > 0
    ? `🔴 *Saldo deudor: $${saldo.toFixed(2)}*`
    : saldo < 0
      ? `🟢 *Dinero a favor: $${Math.abs(saldo).toFixed(2)}*`
      : `🟢 *Saldo: $0 (cancelado)*`;

  let msg = `📋 *Cuenta Corriente — ${nombre_}*\n`;
  if (resp) msg += `👤 TECHCARGO: ${resp}\n`;
  if (fechaAlta) msg += `📅 Alta: ${fechaAlta}\n`;
  msg += `\n${saldoStr}\n`;

  if (movs.length > 0) {
    msg += `\n📝 *Movimientos:*\n`;
    for (const m of movs) {
      const fecha   = getVal(m, 2);
      const tipo_   = getVal(m, 3);
      const monto   = getVal(m, 4);
      const concep  = getVal(m, 5);
      const emoji   = tipo_.includes('Pago') ? '🟢' : '🔴';
      msg += `${emoji} ${fecha} — ${tipo_} $${monto}`;
      if (concep) msg += ` (${concep})`;
      msg += '\n';
    }
  } else {
    msg += '\n_(sin movimientos registrados)_\n';
  }

  return msg;
}

async function getListaClientes() {
  const [rowsD, rowsM] = await Promise.all([
    sheetsGet('DEUDORES!A2:G'),
    sheetsGet('MOVIMIENTOS!A2:G'),
  ]);

  if (rowsD.length === 0) return '📋 No hay clientes registrados aún.';

  let msg = '📋 *Lista de clientes:*\n\n';
  for (const row of rowsD) {
    const cid    = getVal(row, 0);
    const nombre = getVal(row, 1);
    if (!nombre) continue;
    let saldo = parseMonto(getVal(row, 3));
    for (const m of rowsM.filter(r => getVal(r, 1) === cid)) {
      const t = getVal(m, 3);
      const v = parseMonto(getVal(m, 4));
      if (t.includes('Pago'))  saldo -= v;
      if (t.includes('Cargo')) saldo += v;
    }
    const emoji = saldo > 0 ? '🔴' : saldo === 0 ? '✅' : '🟢';
    msg += `${emoji} *${nombre}* — $${saldo.toFixed(2)}\n`;
  }
  msg += '\nUsá *!cc NOMBRE* para ver el detalle.';
  return msg;
}

// ─── Bot WhatsApp ─────────────────────────────────────────────────────────────

// Detectar path de Chrome automáticamente si no está configurado
function detectChromePath() {
  const candidates = [
    process.env.CHROMIUM_PATH,
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/snap/bin/chromium',
  ].filter(Boolean);

  for (const p of candidates) {
    if (fs.existsSync(p)) {
      console.log(`[Chrome] Usando: ${p}`);
      return p;
    }
  }
  console.warn('[Chrome] No se encontró ejecutable. Usando puppeteer bundled.');
  return undefined;
}

const chromePath = detectChromePath();

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: SESSION_PATH }),
  puppeteer: {
    executablePath: chromePath,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-gpu',
    ],
  }
});

client.on('qr', qr => {
  console.log('\n📱 Escaneá este QR con WhatsApp en tu celu:\n');
  qrcode.generate(qr, { small: true });
  console.log('\n(Abrí WhatsApp → ⋮ → Aparatos vinculados → Vincular un aparato)\n');
});

client.on('ready', () => {
  console.log('\n✅ Bot conectado y listo');
  console.log('   Comandos: !cc NOMBRE | !cc lista\n');
});

async function handleMessage(msg) {
  const text = msg.body.trim();

  // Solo responder a mensajes propios
  if (!msg.fromMe) return;

  let arg;
  if (text.startsWith('!cc')) {
    arg = text.slice(3).trim();
    if (!arg) {
      msg.reply('Usá *!cc NOMBRE* para ver la cuenta corriente o *!cc lista* para ver todos.');
      return;
    }
  } else {
    arg = text;
    try {
      const respuesta = await getCuentaCliente(arg);
      if (!respuesta.startsWith('❌')) msg.reply(respuesta);
    } catch (err) {
      console.error('Error:', err);
    }
    return;
  }

  try {
    let respuesta;
    if (arg.toLowerCase() === 'lista') {
      respuesta = await getListaClientes();
    } else {
      respuesta = await getCuentaCliente(arg);
    }
    msg.reply(respuesta);
  } catch (err) {
    console.error('Error:', err);
    msg.reply('❌ Error consultando la planilla. Intentá de nuevo.');
  }
}

client.on('message', handleMessage);
client.on('message_create', handleMessage);

client.on('auth_failure', () => {
  console.error('❌ Error de autenticación. Borrá la sesión y reiniciá.');
});

client.on('disconnected', reason => {
  console.log('Bot desconectado:', reason);
  setTimeout(() => client.initialize(), 5000); // reconectar
});

client.initialize();
