#!/usr/bin/env python3
"""
TechCargo — Servidor cloud-ready: Stock + Cuentas.
Deploy en Railway, Render, o VPS con Docker.

Variables de entorno requeridas:
  GOOGLE_CREDENTIALS_JSON  → contenido completo de token.json (como string JSON)
  PORT                     → puerto (Railway lo setea automático)
  APP_DATA_DIR             → directorio de datos persistentes (default: /app/data)
"""

import json, datetime, subprocess, sys, os, threading, time, ssl
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import urllib.request, urllib.parse

# ─── Configuración cloud ──────────────────────────────────────────────────────

BASE_DIR   = Path(os.environ.get("APP_DATA_DIR", Path(__file__).parent / "data"))
TOKEN_FILE = BASE_DIR / "token.json"
BASE_DIR.mkdir(parents=True, exist_ok=True)

def _init_token():
    """Inicializa token.json desde env var GOOGLE_CREDENTIALS_JSON si no existe."""
    if not TOKEN_FILE.exists():
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
        if creds_json:
            with open(TOKEN_FILE, "w") as f:
                f.write(creds_json)
            print("[Token] Inicializado desde GOOGLE_CREDENTIALS_JSON")
        else:
            print("[Token] ADVERTENCIA: No hay token.json ni GOOGLE_CREDENTIALS_JSON")

_init_token()

SPEC_ID        = "1MjTLP-7ZRqmaUa4uMkY3O7gXsMT8U9fnCk8fOhHUaVs"
CAJA_ID        = "1KP58_1-qOWYYn4JM7F2kSYthRB9b7qHOraiyINy2P5g"
SPEC_SHEET     = "ESPECIFICACIÓN STOCK - TECHCARGO"
SHEET_DEUDORES = "DEUDORES"
SHEET_MOV      = "MOVIMIENTOS"

# ─── Google Sheets helpers ───────────────────────────────────────────────────

def get_token():
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    expiry_str = data.get("expiry", "")
    if expiry_str:
        expiry = datetime.datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
        if datetime.datetime.utcnow() >= expiry - datetime.timedelta(minutes=5):
            data = refresh_token(data)
    return data["token"]

def refresh_token(data):
    payload = urllib.parse.urlencode({
        "client_id":     data["client_id"],
        "client_secret": data["client_secret"],
        "refresh_token": data["refresh_token"],
        "grant_type":    "refresh_token"
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
    data["token"] = resp["access_token"]
    expires = datetime.datetime.utcnow() + datetime.timedelta(seconds=resp["expires_in"])
    data["expiry"] = expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)
    print(f"[Token renovado] Expira: {data['expiry']}")
    return data

def sheets_get(spreadsheet_id, range_):
    token = get_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{urllib.parse.quote(range_)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("values", [])

def sheets_update(spreadsheet_id, range_, values):
    token = get_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{urllib.parse.quote(range_)}?valueInputOption=RAW"
    body = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def sheets_clear(spreadsheet_id, range_):
    token = get_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{urllib.parse.quote(range_)}:clear"
    req = urllib.request.Request(url, data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def sheets_append(spreadsheet_id, range_, values):
    token = get_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{urllib.parse.quote(range_)}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    body = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ─── Helpers ─────────────────────────────────────────────────────────────────

def hoy():
    return datetime.datetime.now().strftime("%d/%m/%Y")

def pad(row, n):
    row = list(row)
    while len(row) < n:
        row.append("")
    return row

def get_val(row, idx):
    return str(row[idx]).strip() if idx < len(row) else ""

def parse_monto(val):
    try:
        return float(str(val).replace(",", ".").replace("$", "").replace(" ", "") or 0)
    except:
        return 0.0

# ─── Lógica STOCK ────────────────────────────────────────────────────────────

def lookup_imei(imei):
    imei = imei.strip()
    result = {"imei": imei, "found": False, "fuente": None}

    rows = sheets_get(SPEC_ID, f"{SPEC_SHEET}!A1:L")
    for i, row in enumerate(rows[1:], 2):
        if get_val(row, 3) == imei:
            result.update({
                "found": True, "fuente": "STOCK", "row_num": i,
                "numero": get_val(row, 0), "modelo": get_val(row, 1),
                "color":  get_val(row, 2), "bateria": get_val(row, 4),
                "precio": get_val(row, 5), "estado": get_val(row, 6),
                "fecha_ingreso": get_val(row, 7), "fecha_egreso": get_val(row, 8),
            })
            return result

    rows_f = sheets_get(SPEC_ID, "FALLADOS!A1:K")
    for i, row in enumerate(rows_f[1:], 2):
        if get_val(row, 1) == imei:
            result.update({
                "found": True, "fuente": "FALLADOS", "row_num": i,
                "numero": get_val(row, 0), "modelo": get_val(row, 2),
                "color":  get_val(row, 3), "cliente": get_val(row, 4),
                "fecha_venta": get_val(row, 5), "fecha_reingreso": get_val(row, 6),
                "estado": get_val(row, 7), "falla": get_val(row, 8),
                "fecha_salida_taller": get_val(row, 9),
            })
            return result

    rows_s = sheets_get(SPEC_ID, "SCANNER!A1:M")
    for i, row in enumerate(rows_s[13:], 14):
        if get_val(row, 3) == imei:
            result.update({
                "found": True, "fuente": "SCANNER", "row_num": i,
                "numero": get_val(row, 0), "modelo": get_val(row, 1),
                "color":  get_val(row, 2), "precio": get_val(row, 5),
                "fecha_egreso": get_val(row, 8), "fecha_reingreso": get_val(row, 12),
            })
            return result

    return result

def marcar_vendido(imei, cliente, fecha):
    info = lookup_imei(imei)
    if not info["found"] or info["fuente"] != "STOCK":
        return {"ok": False, "error": "Equipo no encontrado en stock activo"}
    if info.get("fecha_egreso"):
        return {"ok": False, "error": "El equipo ya tiene fecha de egreso"}
    row_num = info["row_num"]
    fecha_uso = fecha or hoy()
    sheets_update(SPEC_ID, f"{SPEC_SHEET}!I{row_num}", [[fecha_uso]])
    if cliente:
        sheets_update(SPEC_ID, f"{SPEC_SHEET}!J{row_num}", [[f"Vendido a: {cliente}"]])
    threading.Thread(target=run_sync, daemon=True).start()
    return {"ok": True, "mensaje": f"✅ {info['modelo']} marcado como vendido ({fecha_uso})"}

def marcar_fallado(imei, falla, fecha_reingreso):
    info = lookup_imei(imei)
    if not info["found"]:
        return {"ok": False, "error": "IMEI no encontrado en ninguna planilla"}
    if info["fuente"] == "FALLADOS":
        return {"ok": False, "error": "El equipo ya está en FALLADOS"}
    fecha_uso = fecha_reingreso or hoy()
    rows_f = sheets_get(SPEC_ID, "FALLADOS!A:A")
    numeros = []
    for r in rows_f[1:]:
        if r and r[0].isdigit():
            numeros.append(int(r[0]))
    ultimo = max(numeros) if numeros else 0
    nueva_fila = [
        str(ultimo + 1), imei,
        info.get("modelo", ""), info.get("color", ""),
        "", info.get("fecha_egreso", ""),
        fecha_uso, "EN REPARACIÓN", falla, "", ""
    ]
    sheets_append(SPEC_ID, "FALLADOS!A2", [nueva_fila])
    threading.Thread(target=run_sync, daemon=True).start()
    return {"ok": True, "mensaje": f"✅ {info.get('modelo',imei)} registrado como fallado"}

def marcar_reparado(imei, fecha_salida):
    info = lookup_imei(imei)
    if not info["found"] or info["fuente"] != "FALLADOS":
        return {"ok": False, "error": "Equipo no encontrado en FALLADOS"}
    fecha_uso = fecha_salida or hoy()
    row_num = info["row_num"]
    sheets_update(SPEC_ID, f"FALLADOS!H{row_num}", [["REPARADO"]])
    sheets_update(SPEC_ID, f"FALLADOS!J{row_num}", [[fecha_uso]])
    threading.Thread(target=run_sync, daemon=True).start()
    return {"ok": True, "mensaje": f"✅ {info.get('modelo',imei)} marcado como reparado — vuelve al stock"}

def marcar_vendido_roto(imei):
    info = lookup_imei(imei)
    if not info["found"] or info["fuente"] != "FALLADOS":
        return {"ok": False, "error": "Equipo no encontrado en FALLADOS"}
    sheets_update(SPEC_ID, f"FALLADOS!H{info['row_num']}", [["VENDIDO ROTO"]])
    threading.Thread(target=run_sync, daemon=True).start()
    return {"ok": True, "mensaje": f"✅ {info.get('modelo',imei)} marcado como vendido roto"}

def get_stock():
    rows = sheets_get(SPEC_ID, f"{SPEC_SHEET}!A1:L")
    stock = []
    for row in rows[1:]:
        imei = get_val(row, 3)
        fecha_egreso = get_val(row, 8)
        modelo = get_val(row, 1)
        if not fecha_egreso and modelo:
            stock.append({
                "numero": get_val(row, 0), "modelo": modelo,
                "color":  get_val(row, 2), "imei":   imei,
                "bateria": get_val(row, 4), "precio": get_val(row, 5),
                "estado": get_val(row, 6), "fecha_ingreso": get_val(row, 7),
            })
    return stock

def get_fallados():
    rows = sheets_get(SPEC_ID, "FALLADOS!A1:K")
    result = []
    for row in rows[1:]:
        imei = get_val(row, 1)
        if imei:
            result.append({
                "numero": get_val(row, 0), "imei": imei,
                "modelo": get_val(row, 2), "color":  get_val(row, 3),
                "cliente": get_val(row, 4), "fecha_venta": get_val(row, 5),
                "fecha_reingreso": get_val(row, 6), "estado": get_val(row, 7),
                "falla":  get_val(row, 8), "fecha_salida_taller": get_val(row, 9),
            })
    return result

def run_sync():
    script = Path(__file__).parent / "techcargo_sync.py"
    if script.exists():
        print("[Sync] Iniciando...")
        env = os.environ.copy()
        env["APP_DATA_DIR"] = str(BASE_DIR)
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env)
        print("[Sync] Completado")
        if result.returncode != 0:
            print("[Sync] Error:", result.stderr[:500])
    else:
        print("[Sync] Script no encontrado, saltando.")

# ─── Sync diario programado ───────────────────────────────────────────────────

def _schedule_daily_sync():
    now = datetime.datetime.now()
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    delay = (target - now).total_seconds()
    def _run():
        print("[Sync diario] Ejecutando...")
        run_sync()
        _schedule_daily_sync()
    t = threading.Timer(delay, _run)
    t.daemon = True
    t.start()
    print(f"[Sync] Próximo sync programado: {target.strftime('%d/%m/%Y %H:%M')}")

_schedule_daily_sync()

# ─── Lógica CUENTAS ──────────────────────────────────────────────────────────

def get_nombres_clientes():
    rows = sheets_get(SPEC_ID, f"{SHEET_DEUDORES}!A2:B")
    return [{"id": get_val(r, 0), "nombre": get_val(r, 1)} for r in rows if get_val(r, 1)]

def get_cobranzas():
    rows_d = sheets_get(SPEC_ID, f"{SHEET_DEUDORES}!A2:G")
    rows_m = sheets_get(SPEC_ID, f"{SHEET_MOV}!A2:G")
    hoy_dt = datetime.datetime.now()
    result = []

    for row in rows_d:
        nombre = get_val(row, 1)
        if not nombre:
            continue
        cid = get_val(row, 0)
        saldo = parse_monto(get_val(row, 3))
        ultima_fecha = get_val(row, 5)

        for mov in rows_m:
            if get_val(mov, 1) != cid:
                continue
            monto = parse_monto(get_val(mov, 4))
            tipo = get_val(mov, 3)
            if "Pago" in tipo:
                saldo -= monto
            elif "Cargo" in tipo:
                saldo += monto
            f = get_val(mov, 2)
            if f:
                ultima_fecha = f

        dias = 0
        if ultima_fecha:
            try:
                parts = ultima_fecha.split("/")
                if len(parts) == 3:
                    dt = datetime.datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                    dias = (hoy_dt - dt).days
            except:
                pass

        result.append({
            "id": cid,
            "nombre": nombre,
            "responsable": get_val(row, 2),
            "saldo": round(saldo, 2),
            "dias": dias,
        })

    result.sort(key=lambda x: x["saldo"], reverse=True)
    return result

def get_cuenta(cliente_id):
    rows_d = sheets_get(SPEC_ID, f"{SHEET_DEUDORES}!A2:G")
    cliente = None
    for row in rows_d:
        if get_val(row, 0) == cliente_id:
            cliente = {
                "id": get_val(row, 0), "nombre": get_val(row, 1),
                "responsable": get_val(row, 2), "monto_original": get_val(row, 3),
                "tipo": get_val(row, 4), "fecha_alta": get_val(row, 5),
            }
            break
    if not cliente:
        return {"error": "Cliente no encontrado"}

    rows_m = sheets_get(SPEC_ID, f"{SHEET_MOV}!A2:G")
    movs = []
    saldo = parse_monto(cliente["monto_original"])
    for row in rows_m:
        if get_val(row, 1) != cliente_id:
            continue
        monto = parse_monto(get_val(row, 4))
        tipo = get_val(row, 3)
        if "Pago" in tipo:
            saldo -= monto
        elif "Cargo" in tipo:
            saldo += monto
        movs.append({
            "id_mov": get_val(row, 0), "fecha": get_val(row, 2),
            "tipo": tipo, "monto": get_val(row, 4),
            "concepto": get_val(row, 5), "notas": get_val(row, 6),
        })

    return {"cliente": cliente, "movimientos": movs, "saldo": round(saldo, 2)}

def registrar_movimiento(cliente_id, tipo, monto, concepto, notas):
    rows = sheets_get(SPEC_ID, f"{SHEET_MOV}!A2:A")
    ultimo = 0
    for r in rows:
        v = get_val(r, 0)
        if v.startswith("M"):
            try:
                n = int(v[1:])
                if n > ultimo: ultimo = n
            except: pass
    nuevo_id = f"M{ultimo + 1:03d}"
    sheets_append(SPEC_ID, f"{SHEET_MOV}!A2", [[nuevo_id, cliente_id, hoy(), tipo, str(monto), concepto, notas]])
    return {"ok": True, "mensaje": "✅ Movimiento registrado"}

def agregar_deudor(nombre, responsable, monto, tipo, concepto):
    rows = sheets_get(SPEC_ID, f"{SHEET_DEUDORES}!A2:A")
    ultimo = 0
    for r in rows:
        v = get_val(r, 0)
        if v.startswith("D"):
            try:
                n = int(v[1:])
                if n > ultimo: ultimo = n
            except: pass
    nuevo_id = f"D{ultimo + 1:03d}"
    sheets_append(SPEC_ID, f"{SHEET_DEUDORES}!A2", [[nuevo_id, nombre, responsable, str(monto), tipo, hoy(), concepto]])
    return {"ok": True, "id": nuevo_id, "mensaje": f"✅ {nombre} agregado"}

# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>TechCargo</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html5-qrcode/2.3.8/html5-qrcode.min.js" onerror="window._qrFailed=true"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0f0f0f; color: #fff; min-height: 100vh; padding-bottom: 72px; }

  .screen { display: none; padding: 16px; max-width: 480px; margin: 0 auto; }
  .screen.active { display: block; }

  /* Bottom nav */
  .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; height: 60px; background: #1a1a1a; border-top: 1px solid #2c2c2e; display: flex; z-index: 100; }
  .tab-btn { flex: 1; background: none; border: none; color: #555; font-size: 12px; font-weight: 600; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; transition: color 0.15s; }
  .tab-btn .tab-icon { font-size: 22px; }
  .tab-btn.active { color: #007aff; }

  /* Header */
  .header { display: flex; align-items: center; gap: 12px; padding: 16px 0 12px; }
  .header h1 { font-size: 20px; font-weight: 700; flex: 1; }
  .back-btn { background: #1e1e1e; border: none; color: #fff; padding: 8px 12px; border-radius: 10px; cursor: pointer; font-size: 16px; }

  /* Stats chips */
  .stats-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
  .chip { background: #1e1e1e; border-radius: 20px; padding: 6px 14px; font-size: 13px; font-weight: 600; }
  .chip.green { color: #34c759; }
  .chip.orange { color: #ff9500; }
  .chip.gray { color: #888; }

  /* Segmented control */
  .seg-control { display: flex; background: #1a1a1a; border-radius: 12px; padding: 3px; margin-bottom: 14px; }
  .seg-btn { flex: 1; background: none; border: none; color: #666; font-size: 13px; font-weight: 700; padding: 9px 4px; border-radius: 9px; cursor: pointer; transition: all 0.15s; }
  .seg-btn.active { background: #2c2c2e; color: #fff; }

  /* Buttons */
  .btn { width: 100%; padding: 16px; border-radius: 14px; border: none; font-size: 17px; font-weight: 600; cursor: pointer; margin-bottom: 10px; transition: opacity 0.15s; }
  .btn:active { opacity: 0.7; }
  .btn-primary { background: #007aff; color: #fff; }
  .btn-danger  { background: #ff3b30; color: #fff; }
  .btn-warning { background: #ff9500; color: #000; }
  .btn-success { background: #34c759; color: #000; }
  .btn-gray    { background: #2c2c2e; color: #fff; }
  .btn-outline { background: transparent; color: #007aff; border: 2px solid #007aff; }

  /* Cards */
  .card { background: #1e1e1e; border-radius: 16px; padding: 16px; margin-bottom: 12px; }
  .card-label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .card-value { font-size: 16px; font-weight: 600; }

  /* Badge */
  .badge { display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-bottom: 12px; }
  .badge-stock   { background: #1a3a1a; color: #34c759; }
  .badge-fallado { background: #3a1a00; color: #ff9500; }
  .badge-scanner { background: #1a1a3a; color: #007aff; }

  /* Info grid */
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
  .info-item { background: #1e1e1e; border-radius: 12px; padding: 12px; }
  .info-item.wide { grid-column: 1 / -1; }

  /* List items */
  .list-item { background: #1e1e1e; border-radius: 12px; padding: 14px; margin-bottom: 8px; cursor: pointer; }
  .list-item:active { opacity: 0.7; }
  .list-item-title { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
  .list-item-sub { font-size: 12px; color: #888; }

  /* Input */
  input, textarea, select { width: 100%; background: #1e1e1e; border: 1px solid #333; color: #fff; border-radius: 12px; padding: 14px; font-size: 16px; margin-bottom: 12px; outline: none; -webkit-appearance: none; }
  input:focus, select:focus { border-color: #007aff; }
  label { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; display: block; }

  /* Toast */
  .toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: #1e1e1e; border: 1px solid #333; color: #fff; padding: 14px 24px; border-radius: 16px; font-size: 15px; font-weight: 500; z-index: 999; opacity: 0; transition: opacity 0.3s; max-width: 90vw; text-align: center; pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast.success { border-color: #34c759; }
  .toast.error { border-color: #ff3b30; }

  /* Loading */
  .loading { text-align: center; padding: 40px; color: #888; }
  .spinner { display: inline-block; width: 30px; height: 30px; border: 3px solid #333; border-top-color: #007aff; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Error state */
  .err-box { background: #200; border: 1px solid #ff3b30; border-radius: 12px; padding: 16px; color: #ff3b30; text-align: center; margin-bottom: 12px; font-size: 14px; }

  .divider { height: 1px; background: #2c2c2e; margin: 16px 0; }
  .section-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .imei-text { font-family: monospace; font-size: 13px; color: #888; }

  /* Cuentas sub-nav */
  .cuentas-subnav { display: flex; background: #1a1a1a; border-radius: 12px; padding: 4px; margin-bottom: 16px; }
  .subnav-btn { flex: 1; background: none; border: none; color: #666; font-size: 12px; font-weight: 700; padding: 10px 4px; border-radius: 9px; cursor: pointer; text-align: center; transition: all 0.15s; }
  .subnav-btn.active { background: #2c2c2e; color: #fff; }
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<!-- ═══════════ SECCIÑN STOCK ═══════════ -->
<div id="section-stock">

  <!-- HOME + LISTA (pantalla principal) -->
  <div class="screen active" id="screen-stock-home">
    <div class="header"><h1>📱 TechCargo</h1></div>
    <div class="stats-chips">
      <span class="chip gray" id="chip-stock">⏳ cargando...</span>
      <span class="chip gray" id="chip-fallados"></span>
    </div>
    <button class="btn btn-primary" onclick="startScan()">📷 Escanear IMEI</button>
    <div class="seg-control">
      <button class="seg-btn active" id="seg-stock"    onclick="showList('stock')">📦 Stock</button>
      <button class="seg-btn"        id="seg-fallados" onclick="showList('fallados')">🔧 Fallados</button>
    </div>
    <div id="main-list"><div class="loading"><div class="spinner"></div></div></div>
  </div>

  <!-- SCAN -->
  <div class="screen" id="screen-scan">
    <div class="header">
      <button class="back-btn" onclick="volverHome()">←</button>
      <h1>Escanear IMEI</h1>
    </div>
    <div id="scan-status" style="color:#888;font-size:13px;text-align:center;margin-bottom:10px">Apuntá la cámara al IMEI</div>
    <div id="scanner-video" style="border-radius:16px;overflow:hidden;"></div>
    <div class="divider"></div>
    <label>O ingresá el IMEI manualmente</label>
    <input type="number" id="imei-manual" placeholder="IMEI de 15 dígitos" inputmode="numeric"
           onkeydown="if(event.key==='Enter') lookupManual()">
    <button class="btn btn-outline" onclick="lookupManual()">Buscar</button>
  </div>

  <!-- RESULTADO -->
  <div class="screen" id="screen-result">
    <div class="header">
      <button class="back-btn" onclick="volverHome()">←</button>
      <h1>Equipo encontrado</h1>
    </div>
    <div id="result-content"></div>
  </div>

  <!-- VENDER -->
  <div class="screen" id="screen-vender">
    <div class="header">
      <button class="back-btn" onclick="volverAResultado()">←</button>
      <h1>Marcar vendido</h1>
    </div>
    <div class="card" id="vender-modelo-card"></div>
    <div class="divider"></div>
    <label>Cliente</label>
    <input type="text" id="vender-cliente" placeholder="Nombre del cliente">
    <label>Fecha de venta</label>
    <input type="date" id="vender-fecha">
    <button class="btn btn-success" onclick="confirmarVenta()">✅ Confirmar venta</button>
  </div>

  <!-- FALLADO -->
  <div class="screen" id="screen-fallado">
    <div class="header">
      <button class="back-btn" onclick="volverAResultado()">←</button>
      <h1>Registrar fallado</h1>
    </div>
    <div class="card" id="fallado-modelo-card"></div>
    <div class="divider"></div>
    <label>Descripción de la falla</label>
    <input type="text" id="fallado-falla" placeholder="Ej: Pantalla rota, no enciende...">
    <label>Fecha de reingreso</label>
    <input type="date" id="fallado-fecha">
    <button class="btn btn-warning" onclick="confirmarFallado()">⚠️ Registrar fallado</button>
  </div>

  <!-- REPARADO -->
  <div class="screen" id="screen-reparado">
    <div class="header">
      <button class="back-btn" onclick="volverAResultado()">←</button>
      <h1>Marcar reparado</h1>
    </div>
    <div class="card" id="reparado-modelo-card"></div>
    <div class="divider"></div>
    <label>Fecha de salida del taller</label>
    <input type="date" id="reparado-fecha">
    <button class="btn btn-success" onclick="confirmarReparado()">✅ Listo — vuelve al stock</button>
  </div>

</div><!-- /section-stock -->


<!-- ═══════════ SECCIÓN CUENTAS ═══════════ -->
<div id="section-cuentas" style="display:none">

  <!-- CUENTAS MAIN -->
  <div class="screen active" id="screen-cuentas-main">
    <div class="header"><h1>💵 Cuentas</h1></div>
    <div class="cuentas-subnav">
      <button class="subnav-btn active" id="snav-cobranzas" onclick="switchCuentasTab('cobranzas')">📋 Deudores</button>
      <button class="subnav-btn"        id="snav-caja"      onclick="switchCuentasTab('caja')">💰 Registrar</button>
      <button class="subnav-btn"        id="snav-nuevo"     onclick="switchCuentasTab('nuevo')">➕ Nuevo</button>
    </div>

    <!-- TAB COBRANZAS -->
    <div id="cuentas-cobranzas" class="cuentas-tab">
      <div id="cobranzas-list"><div class="loading"><div class="spinner"></div></div></div>
    </div>

    <!-- TAB CAJA -->
    <div id="cuentas-caja" class="cuentas-tab" style="display:none">
      <label>Cliente</label>
      <select id="caja-cliente"><option value="">— Seleccionar cliente —</option></select>
      <label>Tipo</label>
      <select id="caja-tipo">
        <option value="✅ Pago recibido">✅ Pago recibido</option>
        <option value="📦 Cargo nuevo">📦 Cargo nuevo</option>
      </select>
      <label>Monto (USD)</label>
      <input type="number" id="caja-monto" placeholder="0.00" step="0.01" min="0" inputmode="decimal">
      <label>Concepto</label>
      <input type="text" id="caja-concepto" placeholder="Ej: iPhone 16 Pro, pago parcial...">
      <label>Notas (opcional)</label>
      <input type="text" id="caja-notas" placeholder="">
      <button class="btn btn-success" onclick="confirmarMovimiento()">Registrar</button>
    </div>

    <!-- TAB NUEVO -->
    <div id="cuentas-nuevo" class="cuentas-tab" style="display:none">
      <label>Nombre</label>
      <input type="text" id="nuevo-nombre" placeholder="Nombre completo">
      <label>Responsable</label>
      <select id="nuevo-responsable">
        <option value="ANDRES">ANDRES</option>
        <option value="PASCUI">PASCUI</option>
        <option value="SERE">SERE</option>
      </select>
      <label>Deuda inicial (USD)</label>
      <input type="number" id="nuevo-monto" placeholder="0.00" step="0.01" min="0" inputmode="decimal">
      <label>Tipo</label>
      <select id="nuevo-tipo">
        <option value="DEUDOR">DEUDOR</option>
        <option value="ACREEDOR">ACREEDOR</option>
      </select>
      <label>Concepto</label>
      <input type="text" id="nuevo-concepto" placeholder="Descripción de la deuda inicial">
      <button class="btn btn-primary" onclick="confirmarNuevoDeudor()">Agregar cliente</button>
    </div>
  </div>

  <!-- DETALLE CLIENTE -->
  <div class="screen" id="screen-cliente">
    <div class="header">
      <button class="back-btn" onclick="showCuentasScreen('screen-cuentas-main')">←</button>
      <h1 id="cliente-nombre-header">Cliente</h1>
    </div>
    <div id="cliente-detail"></div>
  </div>

</div><!-- /section-cuentas -->

<!-- ═══════════ SECCIÓN PEDIDOS ═══════════ -->
<div id="section-pedidos" style="display:none">
  <div style="height:calc(100vh - 132px);margin:16px;border-radius:16px;overflow:hidden;">
    <iframe id="pedidos-iframe"
      src="https://script.google.com/macros/s/AKfycbwqGU4rRWIYR1SCcn028jnTWI8SqFZxOGKwUsSZYVftDRCKiIlcf1OvEHn11kOFyoFT/exec"
      style="width:100%;height:100%;border:none;background:#0f0f0f;"
      allow="camera">
    </iframe>
  </div>
</div><!-- /section-pedidos -->


<!-- Bottom nav -->
<div class="bottom-nav">
  <button class="tab-btn active" id="tab-stock" onclick="switchTab('stock')">
    <span class="tab-icon">📱</span>Stock
  </button>
  <button class="tab-btn" id="tab-cuentas" onclick="switchTab('cuentas')">
    <span class="tab-icon">💵</span>Cuentas
  </button>
  <button class="tab-btn" id="tab-pedidos" onclick="switchTab('pedidos')">
    <span class="tab-icon">📋</span>Pedidos
  </button>
</div>


<script>
// ─── Estado global ─────────────────────────────────────────────────────────
var currentIMEI  = null;
var currentInfo  = null;
var currentList  = 'stock';
var stockData    = null;
var falladosData = null;
var qrScanner    = null;

// ─── Tab principal ──────────────────────────────────────────────────────────
function switchTab(tab) {
  ['stock','cuentas','pedidos'].forEach(function(t) {
    document.getElementById('section-' + t).style.display = (t === tab) ? 'block' : 'none';
    document.getElementById('tab-' + t).classList.toggle('active', t === tab);
  });
  if (tab === 'cuentas') {
    loadClientesDropdown();
    if (document.getElementById('cuentas-cobranzas').style.display !== 'none') loadCobranzas();
  }
}

// ─── Navegación STOCK ───────────────────────────────────────────────────────
function showStockScreen(id) {
  document.querySelectorAll('#section-stock .screen').forEach(function(s) { s.classList.remove('active'); });
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

function volverHome() {
  stopScan();
  var inp = document.getElementById('imei-manual');
  if (inp) inp.value = '';
  showStockScreen('screen-stock-home');
}

function volverAResultado() {
  if (currentInfo) { showStockScreen('screen-result'); renderResult(currentInfo); }
  else volverHome();
}

// ─── Toast ─────────────────────────────────────────────────────────────────
function toast(msg, type) {
  type = type || '';
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(function() { t.className = 'toast'; }, 3000);
}

// ─── Stats + lista ──────────────────────────────────────────────────────────
async function loadStats() {
  try {
    var rs = await fetch('/api/stock');
    var rf = await fetch('/api/fallados');
    if (!rs.ok) throw new Error('HTTP ' + rs.status);
    var s = await rs.json();
    var f = await rf.json();
    if (!Array.isArray(s)) throw new Error('respuesta inesperada');
    stockData    = s;
    falladosData = f;
    var nStock = s.filter(function(x) { return x.imei; }).length;
    var nFall  = f.filter(function(x) { return x.estado === 'EN REPARACIÓN'; }).length;
    var cs = document.getElementById('chip-stock');
    var cf = document.getElementById('chip-fallados');
    cs.textContent = '📦 ' + nStock + ' en stock';
    cs.className = 'chip green';
    cf.textContent = '🔧 ' + nFall + ' fallados';
    cf.className = 'chip orange';
    renderList(currentList);
  } catch(e) {
    document.getElementById('chip-stock').textContent = '⚠️ Error al cargar';
    document.getElementById('chip-stock').className = 'chip';
    document.getElementById('chip-fallados').textContent = '';
    document.getElementById('main-list').innerHTML =
      '<div class="err-box">No se pudo cargar el stock.<br><small>' + e.message + '</small><br><br>' +
      '<button class="btn btn-gray" onclick="loadStats()" style="margin-top:8px;width:auto;padding:10px 20px;font-size:14px">Reintentar</button></div>';
  }
}

function showList(which) {
  currentList = which;
  document.getElementById('seg-stock').classList.toggle('active', which === 'stock');
  document.getElementById('seg-fallados').classList.toggle('active', which === 'fallados');
  if (which === 'stock' && stockData) { renderList('stock'); return; }
  if (which === 'fallados' && falladosData) { renderList('fallados'); return; }
  loadStats();
}

function renderList(which) {
  var el = document.getElementById('main-list');
  if (which === 'stock') {
    var items = stockData || [];
    if (!items.length) { el.innerHTML = '<div class="loading" style="color:#888">Sin stock disponible</div>'; return; }
    var grupos = {};
    items.forEach(function(i) { if (!grupos[i.modelo]) grupos[i.modelo] = []; grupos[i.modelo].push(i); });
    var html = '';
    Object.keys(grupos).forEach(function(modelo) {
      var equipos = grupos[modelo];
      html += '<div class="section-title" style="margin-top:16px">' + modelo + ' <span style="color:#007aff">(' + equipos.length + ')</span></div>';
      equipos.forEach(function(eq) {
        html += '<div class="list-item" onclick="lookupIMEI(\\'' + esc(eq.imei) + '\\')">' +
          '<div class="list-item-title">' + (eq.color||'Sin color') + (eq.precio?' · $'+eq.precio:'') + '</div>' +
          '<div class="list-item-sub">' + (eq.imei?'IMEI: '+eq.imei:'Sin IMEI') + (eq.bateria?' · '+eq.bateria+'%':'') + '</div>' +
          '</div>';
      });
    });
    el.innerHTML = html;
  } else {
    var items = falladosData || [];
    if (!items.length) { el.innerHTML = '<div class="loading" style="color:#888">Sin equipos fallados</div>'; return; }
    var html = '';
    var estadoColor = {'EN REPARACIÓN':'#ff9500','REPARADO':'#34c759','VENDIDO ROTO':'#888'};
    items.forEach(function(eq) {
      var color = estadoColor[eq.estado] || '#fff';
      html += '<div class="list-item" onclick="lookupIMEI(\\'' + esc(eq.imei) + '\\')">' +
        '<div class="list-item-title">' + eq.modelo + ' <span style="color:' + color + ';float:right;font-size:12px">' + eq.estado + '</span></div>' +
        '<div class="list-item-sub">' + (eq.cliente||'Sin cliente') + ' · ' + (eq.falla||'Sin descripción') + '</div>' +
        '<div class="list-item-sub" style="margin-top:4px">Reingreso: ' + (eq.fecha_reingreso||'—') + '</div>' +
        '</div>';
    });
    el.innerHTML = html;
  }
}

// ─── Escáner ────────────────────────────────────────────────────────────────
function startScan() {
  showStockScreen('screen-scan');
  var statusEl = document.getElementById('scan-status');
  statusEl.textContent = 'Iniciando cámara...';
  if (window._qrFailed || typeof Html5Qrcode === 'undefined') {
    statusEl.textContent = '❌ Librería de escáner no disponible. Usá el campo manual.';
    return;
  }
  qrScanner = new Html5Qrcode("scanner-video", { verbose: false });
  qrScanner.start(
    { facingMode: "environment" },
    { fps: 15, qrbox: { width: 280, height: 80 } },
    function(raw) {
      var imei = raw.replace(/\\D/g, '');
      if (imei.length >= 14) { stopScan(); lookupIMEI(imei); }
      else { var m = raw.match(/\\d{14,15}/); if (m) { stopScan(); lookupIMEI(m[0]); } }
    },
    function() {}
  ).then(function() {
    statusEl.textContent = 'Apuntá al código de barras del IMEI';
  }).catch(function(e) {
    statusEl.textContent = '❌ Sin acceso a la cámara: ' + e;
  });
}

function stopScan() {
  if (qrScanner) { qrScanner.stop().catch(function(){}); qrScanner.clear(); qrScanner = null; }
}

function lookupManual() {
  var imei = document.getElementById('imei-manual').value.trim();
  if (imei.length < 14) { toast('IMEI debe tener al menos 14 dígitos', 'error'); return; }
  stopScan();
  document.getElementById('imei-manual').value = '';
  lookupIMEI(imei);
}

async function lookupIMEI(imei) {
  showStockScreen('screen-result');
  document.getElementById('result-content').innerHTML = '<div class="loading"><div class="spinner"></div><p style="margin-top:12px;color:#888">Buscando...</p></div>';
  currentIMEI = imei;
  try {
    var r = await fetch('/api/lookup?imei=' + encodeURIComponent(imei));
    currentInfo = await r.json();
    renderResult(currentInfo);
  } catch(e) {
    document.getElementById('result-content').innerHTML = '<div class="err-box">Error de conexión<br><small>' + e.message + '</small></div>';
  }
}

function renderResult(d) {
  if (!d.found) {
    document.getElementById('result-content').innerHTML =
      '<div style="text-align:center;padding:40px 0"><div style="font-size:48px">🔍</div>' +
      '<div style="font-size:18px;margin:12px 0">IMEI no encontrado</div>' +
      '<div class="imei-text">' + d.imei + '</div></div>';
    return;
  }
  var badges = { STOCK:'badge-stock', FALLADOS:'badge-fallado', SCANNER:'badge-scanner' };
  var labels  = { STOCK:'✅ En stock', FALLADOS:'⚠️ Fallado', SCANNER:'📋 Historial' };
  var html = '<span class="badge ' + badges[d.fuente] + '">' + labels[d.fuente] + '</span><div class="info-grid">';
  html += '<div class="info-item wide"><div class="card-label">Modelo</div><div class="card-value">' + (d.modelo||'—') + '</div></div>';
  if (d.color)               html += '<div class="info-item"><div class="card-label">Color</div><div class="card-value">' + d.color + '</div></div>';
  if (d.precio)              html += '<div class="info-item"><div class="card-label">Precio</div><div class="card-value">' + d.precio + '</div></div>';
  if (d.bateria)             html += '<div class="info-item"><div class="card-label">Batería</div><div class="card-value">' + d.bateria + '%</div></div>';
  if (d.fecha_ingreso)       html += '<div class="info-item"><div class="card-label">Ingreso</div><div class="card-value">' + d.fecha_ingreso + '</div></div>';
  if (d.fecha_egreso)        html += '<div class="info-item"><div class="card-label">Egreso</div><div class="card-value">' + d.fecha_egreso + '</div></div>';
  if (d.cliente)             html += '<div class="info-item"><div class="card-label">Cliente</div><div class="card-value">' + d.cliente + '</div></div>';
  if (d.estado)              html += '<div class="info-item"><div class="card-label">Estado</div><div class="card-value">' + d.estado + '</div></div>';
  if (d.falla)               html += '<div class="info-item wide"><div class="card-label">Falla</div><div class="card-value">' + d.falla + '</div></div>';
  if (d.fecha_venta)         html += '<div class="info-item"><div class="card-label">Venta</div><div class="card-value">' + d.fecha_venta + '</div></div>';
  if (d.fecha_reingreso)     html += '<div class="info-item"><div class="card-label">Reingreso</div><div class="card-value">' + d.fecha_reingreso + '</div></div>';
  if (d.fecha_salida_taller) html += '<div class="info-item"><div class="card-label">Salida taller</div><div class="card-value">' + d.fecha_salida_taller + '</div></div>';
  html += '<div class="info-item wide"><div class="card-label">IMEI</div><div class="card-value imei-text">' + d.imei + '</div></div></div>';
  html += '<div class="divider"></div><div class="section-title">Acciones</div>';
  if (d.fuente === 'STOCK' && !d.fecha_egreso) {
    html += '<button class="btn btn-success" onclick="irAVender()">💰 Marcar vendido</button>';
    html += '<button class="btn btn-warning" onclick="irAFallado()">⚠️ Registrar fallado</button>';
  } else if (d.fuente === 'FALLADOS') {
    var estado = (d.estado||'').toUpperCase();
    if (estado === 'EN REPARACIÓN') {
      html += '<button class="btn btn-success" onclick="irAReparado()">✅ Marcar reparado</button>';
      html += '<button class="btn btn-danger" onclick="confirmarVendidoRoto()">💀 Vendido roto</button>';
    } else if (estado === 'REPARADO') {
      html += '<div class="card" style="color:#34c759;text-align:center">Reparado — pendiente de sync</div>';
    }
  } else if (d.fuente === 'SCANNER' && d.fecha_egreso && !d.fecha_reingreso) {
    html += '<button class="btn btn-warning" onclick="irAFallado()">⚠️ Registrar como fallado</button>';
  }
  document.getElementById('result-content').innerHTML = html;
}

function irAVender() {
  document.getElementById('vender-modelo-card').innerHTML = '<div class="card-label">Equipo</div><div class="card-value">' + currentInfo.modelo + '</div>';
  document.getElementById('vender-fecha').value = new Date().toISOString().split('T')[0];
  document.getElementById('vender-cliente').value = '';
  showStockScreen('screen-vender');
}
async function confirmarVenta() {
  var cliente = document.getElementById('vender-cliente').value;
  var fecha   = document.getElementById('vender-fecha').value.split('-').reverse().join('/');
  var r = await fetch('/api/sell', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({imei:currentIMEI,cliente:cliente,fecha:fecha}) });
  var d = await r.json();
  if (d.ok) { toast(d.mensaje,'success'); volverHome(); stockData=null; loadStats(); }
  else toast(d.error,'error');
}

function irAFallado() {
  document.getElementById('fallado-modelo-card').innerHTML = '<div class="card-label">Equipo</div><div class="card-value">' + (currentInfo.modelo||currentIMEI) + '</div>';
  document.getElementById('fallado-fecha').value = new Date().toISOString().split('T')[0];
  document.getElementById('fallado-falla').value = '';
  showStockScreen('screen-fallado');
}
async function confirmarFallado() {
  var falla = document.getElementById('fallado-falla').value;
  var fecha = document.getElementById('fallado-fecha').value.split('-').reverse().join('/');
  var r = await fetch('/api/failed', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({imei:currentIMEI,falla:falla,fecha_reingreso:fecha}) });
  var d = await r.json();
  if (d.ok) { toast(d.mensaje,'success'); volverHome(); stockData=null; falladosData=null; loadStats(); }
  else toast(d.error,'error');
}

function irAReparado() {
  document.getElementById('reparado-modelo-card').innerHTML = '<div class="card-label">Equipo</div><div class="card-value">' + currentInfo.modelo + '</div>';
  document.getElementById('reparado-fecha').value = new Date().toISOString().split('T')[0];
  showStockScreen('screen-reparado');
}
async function confirmarReparado() {
  var fecha = document.getElementById('reparado-fecha').value.split('-').reverse().join('/');
  var r = await fetch('/api/repaired', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({imei:currentIMEI,fecha_salida:fecha}) });
  var d = await r.json();
  if (d.ok) { toast(d.mensaje,'success'); volverHome(); stockData=null; falladosData=null; loadStats(); }
  else toast(d.error,'error');
}

async function confirmarVendidoRoto() {
  if (!confirm('¿Confirmar vendido roto?')) return;
  var r = await fetch('/api/vendido-roto', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({imei:currentIMEI}) });
  var d = await r.json();
  if (d.ok) { toast(d.mensaje,'success'); volverHome(); falladosData=null; loadStats(); }
  else toast(d.error,'error');
}

// ─── CUENTAS ────────────────────────────────────────────────────────────────
function showCuentasScreen(id) {
  document.querySelectorAll('#section-cuentas .screen').forEach(function(s) { s.classList.remove('active'); });
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

function switchCuentasTab(tab) {
  document.querySelectorAll('.cuentas-tab').forEach(function(t) { t.style.display = 'none'; });
  document.querySelectorAll('.subnav-btn').forEach(function(b) { b.classList.remove('active'); });
  document.getElementById('cuentas-' + tab).style.display = 'block';
  document.getElementById('snav-' + tab).classList.add('active');
  if (tab === 'cobranzas') loadCobranzas();
  if (tab === 'caja') loadClientesDropdown();
}

async function loadClientesDropdown() {
  try {
    var r = await fetch('/api/clientes');
    var clientes = await r.json();
    var sel = document.getElementById('caja-cliente');
    var curr = sel.value;
    sel.innerHTML = '<option value="">— Seleccionar cliente —</option>';
    clientes.forEach(function(c) {
      sel.innerHTML += '<option value="' + c.id + '"' + (c.id===curr?' selected':'') + '>' + c.nombre + '</option>';
    });
  } catch(e) {}
}

async function confirmarMovimiento() {
  var cliente_id = document.getElementById('caja-cliente').value;
  var tipo       = document.getElementById('caja-tipo').value;
  var monto      = parseFloat(document.getElementById('caja-monto').value);
  var concepto   = document.getElementById('caja-concepto').value.trim();
  var notas      = document.getElementById('caja-notas').value.trim();
  if (!cliente_id) { toast('Seleccioná un cliente', 'error'); return; }
  if (!monto || monto <= 0) { toast('Ingresá un monto válido', 'error'); return; }
  var r = await fetch('/api/movimiento', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cliente_id:cliente_id, tipo:tipo, monto:monto, concepto:concepto, notas:notas}) });
  var d = await r.json();
  if (d.ok) {
    toast(d.mensaje, 'success');
    document.getElementById('caja-monto').value = '';
    document.getElementById('caja-concepto').value = '';
    document.getElementById('caja-notas').value = '';
    document.getElementById('caja-cliente').value = '';
  } else toast(d.error||'Error', 'error');
}

async function loadCobranzas() {
  var el = document.getElementById('cobranzas-list');
  el.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    var r = await fetch('/api/cobranzas');
    var clientes = await r.json();
    if (!Array.isArray(clientes) || !clientes.length) { el.innerHTML = '<div class="loading" style="color:#888">Sin clientes</div>'; return; }
    var html = '';
    clientes.forEach(function(c) {
      var saldo = c.saldo, dias = c.dias;
      var color = '#aaa', borderColor = '#2c2c2e', bg = '#1e1e1e';
      if (saldo > 0) {
        if (dias >= 30)     { color = '#ff3b30'; borderColor = '#ff3b30'; bg = '#1e1010'; }
        else if (dias >= 7) { color = '#ff9500'; borderColor = '#ff9500'; bg = '#1e1800'; }
        else                { color = '#fff';    borderColor = '#34c759'; }
      }
      html += '<div class="list-item" style="background:' + bg + ';border-left:3px solid ' + borderColor + '" onclick="verCliente(\\'' + c.id + '\\',\\'' + esc(c.nombre) + '\\')">' +
        '<div class="list-item-title" style="color:' + color + '">' + c.nombre + '<span style="float:right">$' + saldo.toFixed(2) + '</span></div>' +
        '<div class="list-item-sub">' + (c.responsable||'') + ' · ' + dias + ' días sin actividad</div>' +
        '</div>';
    });
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="err-box">Error de conexión</div>'; }
}

async function verCliente(id, nombre) {
  document.getElementById('cliente-nombre-header').textContent = nombre;
  var el = document.getElementById('cliente-detail');
  el.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  showCuentasScreen('screen-cliente');
  try {
    var r = await fetch('/api/cuenta?id=' + encodeURIComponent(id));
    var d = await r.json();
    var saldo = d.saldo;
    var saldoColor = saldo > 0 ? '#ff3b30' : '#34c759';
    var html = '<div class="card" style="text-align:center"><div class="card-label">Saldo actual</div>' +
      '<div style="font-size:40px;font-weight:700;color:' + saldoColor + '">$' + saldo.toFixed(2) + '</div></div>' +
      '<div class="section-title" style="margin-top:16px">Movimientos</div>';
    if (!d.movimientos.length) {
      html += '<div style="color:#888;text-align:center;padding:20px">Sin movimientos</div>';
    } else {
      d.movimientos.slice().reverse().forEach(function(m) {
        var isPago = m.tipo.indexOf('Pago') >= 0;
        var montoColor = isPago ? '#34c759' : '#ff3b30';
        var signo = isPago ? '-' : '+';
        html += '<div class="list-item">' +
          '<div class="list-item-title">' + (m.concepto||m.tipo) + '<span style="color:' + montoColor + ';float:right">' + signo + '$' + parseFloat(m.monto||0).toFixed(2) + '</span></div>' +
          '<div class="list-item-sub">' + m.fecha + ' · ' + m.tipo + '</div>' +
          (m.notas ? '<div class="list-item-sub">' + m.notas + '</div>' : '') +
          '</div>';
      });
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div class="err-box">Error de conexión</div>'; }
}

async function confirmarNuevoDeudor() {
  var nombre      = document.getElementById('nuevo-nombre').value.trim();
  var responsable = document.getElementById('nuevo-responsable').value;
  var monto       = parseFloat(document.getElementById('nuevo-monto').value || 0);
  var tipo        = document.getElementById('nuevo-tipo').value;
  var concepto    = document.getElementById('nuevo-concepto').value.trim();
  if (!nombre) { toast('Ingresá el nombre', 'error'); return; }
  var r = await fetch('/api/nuevo-deudor', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({nombre:nombre, responsable:responsable, monto:monto, tipo:tipo, concepto:concepto}) });
  var d = await r.json();
  if (d.ok) {
    toast(d.mensaje, 'success');
    document.getElementById('nuevo-nombre').value = '';
    document.getElementById('nuevo-monto').value = '';
    document.getElementById('nuevo-concepto').value = '';
  } else toast(d.error||'Error', 'error');
}

function esc(s) { return String(s||'').replace(/'/g, "&#39;"); }

// ─── Init ───────────────────────────────────────────────────────────────────
loadStats();
</script>
</body>
</html>"""

# ─── HTTP Server ─────────────────────────────────────────────────────────────

_last_imei = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.client_address[0]}] {format % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self.send_html(HTML)
            elif path == "/api/lookup":
                self.send_json(lookup_imei(qs.get("imei",[""])[0]))
            elif path == "/api/stock":
                self.send_json(get_stock())
            elif path == "/api/fallados":
                self.send_json(get_fallados())
            elif path == "/api/clientes":
                self.send_json(get_nombres_clientes())
            elif path == "/api/cobranzas":
                self.send_json(get_cobranzas())
            elif path == "/api/cuenta":
                self.send_json(get_cuenta(qs.get("id",[""])[0]))
            elif path == "/health":
                self.send_json({"status": "ok", "time": datetime.datetime.now().isoformat()})
            elif path == "/get-imei":
                global _last_imei
                imei = _last_imei or ""
                _last_imei = None
                self.send_json({"imei": imei})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/set-imei":
                global _last_imei
                _last_imei = body.get("imei", "")
                self.send_json({"ok": True})
            elif path == "/api/sell":
                self.send_json(marcar_vendido(body.get("imei",""), body.get("cliente",""), body.get("fecha","")))
            elif path == "/api/failed":
                self.send_json(marcar_fallado(body.get("imei",""), body.get("falla",""), body.get("fecha_reingreso","")))
            elif path == "/api/repaired":
                self.send_json(marcar_reparado(body.get("imei",""), body.get("fecha_salida","")))
            elif path == "/api/vendido-roto":
                self.send_json(marcar_vendido_roto(body.get("imei","")))
            elif path == "/api/movimiento":
                self.send_json(registrar_movimiento(
                    body.get("cliente_id",""), body.get("tipo",""),
                    body.get("monto",0), body.get("concepto",""), body.get("notas","")
                ))
            elif path == "/api/nuevo-deudor":
                self.send_json(agregar_deudor(
                    body.get("nombre",""), body.get("responsable",""),
                    body.get("monto",0), body.get("tipo","DEUDOR"), body.get("concepto","")
                ))
            elif path == "/api/sync":
                threading.Thread(target=run_sync, daemon=True).start()
                self.send_json({"ok": True, "mensaje": "Sync iniciado"})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    print(f"""
╔══════════════════════════════════════╗
║      TechCargo — Cloud Server        ║
╠══════════════════════════════════════╣
║  Puerto: {PORT:<29}║
║  Data dir: {str(BASE_DIR):<27}║
╚══════════════════════════════════════╝
""")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
