#!/usr/bin/env python3
"""
TechCargo Sync — Cloud-ready version.
Lee BASE_DIR desde env var APP_DATA_DIR (default: directorio del script).
Lee credenciales Google desde token.json en BASE_DIR.
"""

import sys, warnings, json, re, os
warnings.filterwarnings("ignore")
from pathlib import Path
from collections import defaultdict

# ─── Setup de paths ───────────────────────────────────────────────────────────

BASE_DIR   = Path(os.environ.get("APP_DATA_DIR", Path(__file__).parent / "data"))
TOKEN_FILE = BASE_DIR / "token.json"
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Inicializar token desde env var si no existe
if not TOKEN_FILE.exists():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if creds_json:
        with open(TOKEN_FILE, "w") as f:
            f.write(creds_json)
        print("Token inicializado desde GOOGLE_CREDENTIALS_JSON")
    else:
        print("ERROR: No hay token.json ni GOOGLE_CREDENTIALS_JSON")
        sys.exit(1)

# ─── Google Auth ─────────────────────────────────────────────────────────────

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
if creds.expired and creds.refresh_token:
    try:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("Token renovado OK")
    except Exception as e:
        print(f"ADVERTENCIA: No se pudo renovar token ({e}). Usando existente.")

service = build("sheets", "v4", credentials=creds)
sheets  = service.spreadsheets()

# ─── Configuración ───────────────────────────────────────────────────────────

SPEC_ID    = "1MjTLP-7ZRqmaUa4uMkY3O7gXsMT8U9fnCk8fOhHUaVs"
CAJA_ID    = "1KP58_1-qOWYYn4JM7F2kSYthRB9b7qHOraiyINy2P5g"
SPEC_SHEET = "ESPECIFICACIÓN STOCK - TECHCARGO"

MODELO_MAP = {
    "16 PRO 256 AB":         "iPhone 16 Pro 256GB",
    "16 PRO 128 AB":         "iPhone 16 Pro 128GB",
    "16 PRO 128GB AB":       "iPhone 16 Pro 128GB",
    "16 PRO MAX 256 GB AB":  "iPhone 16 Pro Max 256GB",
    "15 PRO MAX 256 AB":     "iPhone 15 Pro Max 256GB",
    "15 PRO MAX 256":        "iPhone 15 Pro Max 256GB",
    "15 PRO 128 AB":         "iPhone 15 Pro 128GB",
    "15 128 AB":             "iPhone 15 128GB",
    "14 PRO 128GB AB":       "iPhone 14 Pro 128GB",
    "14 PRO 128 AB":         "iPhone 14 Pro 128GB",
    "14 PRO 128":            "iPhone 14 Pro 128GB",
    "14 128GB AB":           "iPhone 14 128GB",
    "14 128":                "iPhone 14 128GB",
    "13 PRO MAX 128":        "iPhone 13 Pro Max 128GB",
    "13 PRO 128 AB":         "iPhone 13 Pro 128GB",
    "13 PRO 128":            "iPhone 13 Pro 128GB",
    "13 128 GB AB":          "iPhone 13 128GB",
    "13 128":                "iPhone 13 128GB",
    "16 128GB AB":           "iPhone 16 128GB",
    "16 128 AB":             "iPhone 16 128GB",
    "16 128":                "iPhone 16 128GB",
}

ACCESORIOS_KEYWORDS = ["RAYBAN", "CARGADOR", "CABLE", "MACBOOK", "AIRPOD", "WATCH", "IPAD", "PENCIL"]

def es_accesorio(nombre):
    nombre_upper = str(nombre).upper()
    return any(k in nombre_upper for k in ACCESORIOS_KEYWORDS)

def parse_qty(val):
    try:
        return int(str(val).strip())
    except:
        return None

def pad_row(row, n=12):
    row = list(row)
    while len(row) < n:
        row.append("")
    return row[:n]

def get_val(row, idx, default=""):
    if idx < len(row):
        return str(row[idx]).strip()
    return default

print("=== TECHCARGO SYNC ===")

# PASO 1: Leer ESPECIFICACIÓN STOCK
print("\n[1] Leyendo ESPECIFICACIÓN STOCK...")
resp = sheets.values().get(spreadsheetId=SPEC_ID, range=f"{SPEC_SHEET}!A1:L").execute()
spec_all = resp.get("values", [])
spec_header = spec_all[0] if spec_all else []
spec_data = spec_all[1:] if len(spec_all) > 1 else []

egresados = []
con_imei = []
placeholders = []
ultimo_numero = 0

for row in spec_data:
    row_p = pad_row(row, 12)
    num_str = get_val(row_p, 0)
    try:
        n = int(num_str)
        if n > ultimo_numero:
            ultimo_numero = n
    except:
        pass
    imei = get_val(row_p, 3)
    fecha_egreso = get_val(row_p, 8)
    if fecha_egreso:
        egresados.append(row_p)
    elif imei:
        con_imei.append(row_p)
    else:
        placeholders.append(row_p)

print(f"  Egresados: {len(egresados)}, Con IMEI: {len(con_imei)}, Placeholders: {len(placeholders)}, Último N°: {ultimo_numero}")

# PASO 2: Leer CAJA TECHCARGO
print("\n[2] Leyendo CAJA TECHCARGO...")
resp_caja = sheets.values().get(spreadsheetId=CAJA_ID, range="CAJA!A1:Z").execute()
caja_all = resp_caja.get("values", [])

stock_caja = defaultdict(lambda: {"qty": 0, "costo": "", "colores": ""})
fallados_caja = defaultdict(lambda: {"qty": 0, "colores": ""})

for row in caja_all[1:]:
    nombre = get_val(row, 16)
    estado = get_val(row, 19).upper()
    pcs_raw = get_val(row, 20)
    costo = get_val(row, 21)
    colores = get_val(row, 25)

    if es_accesorio(nombre):
        continue

    qty = parse_qty(pcs_raw)
    if qty is None:
        continue

    mapped = MODELO_MAP.get(nombre.strip())
    if not mapped:
        if estado in ("STOCK USED", "FALLADO"):
            print(f"  IGNORADO (sin mapeo): '{nombre}' [{estado}]")
        continue

    if estado == "STOCK USED":
        stock_caja[mapped]["qty"] += qty
        stock_caja[mapped]["costo"] = costo
        stock_caja[mapped]["colores"] = colores
    elif estado == "FALLADO":
        fallados_caja[mapped]["qty"] += qty
        fallados_caja[mapped]["colores"] = colores

print(f"  Modelos STOCK USED: {len(stock_caja)}")
for m, v in sorted(stock_caja.items()):
    print(f"    {m}: {v['qty']} uds @ {v['costo']}")
print(f"  Modelos FALLADO: {len(fallados_caja)}")
for m, v in sorted(fallados_caja.items()):
    print(f"    {m}: {v['qty']} uds")

# PASO 4: Mover egresados al historial SCANNER
if egresados:
    print(f"\n[4] Moviendo {len(egresados)} egresados a SCANNER...")
    scanner_rows = []
    for row in egresados:
        scanner_row = pad_row(row, 12) + [""]  # 13 columnas
        scanner_rows.append(scanner_row)
    sheets.values().append(
        spreadsheetId=SPEC_ID,
        range="SCANNER!A14",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": scanner_rows}
    ).execute()
    print(f"  OK: {len(egresados)} filas movidas a SCANNER")
else:
    print("\n[4] Sin egresados para mover.")

# PASO 3: Calcular ajuste de placeholders
print("\n[3] Calculando ajuste de placeholders...")

imei_por_modelo = defaultdict(list)
for row in con_imei:
    modelo = get_val(row, 1)
    imei_por_modelo[modelo].append(row)

placeholder_por_modelo = defaultdict(list)
for row in placeholders:
    modelo = get_val(row, 1)
    placeholder_por_modelo[modelo].append(row)

for modelo, info in stock_caja.items():
    qty_caja = info["qty"]
    qty_imei = len(imei_por_modelo[modelo])
    qty_ph_necesaria = max(0, qty_caja - qty_imei)
    qty_ph_actual = len(placeholder_por_modelo[modelo])
    diff = qty_ph_necesaria - qty_ph_actual
    print(f"  {modelo}: caja={qty_caja}, IMEI={qty_imei}, PH actual={qty_ph_actual}, PH necesaria={qty_ph_necesaria}, diff={diff:+d}")

    if diff > 0:
        for _ in range(diff):
            ultimo_numero += 1
            costo_raw = str(info.get("costo", "")).replace("$", "").replace(".", "").strip()
            ph_row = [str(ultimo_numero), modelo, info["colores"], "", "", costo_raw, "", "", "", "", "", ""]
            placeholder_por_modelo[modelo].append(ph_row)
            print(f"    + Nuevo placeholder N°{ultimo_numero}")
    elif diff < 0:
        exceso = -diff
        ph_list = placeholder_por_modelo[modelo]
        placeholder_por_modelo[modelo] = ph_list[:len(ph_list)-exceso]
        print(f"    - Eliminados {exceso} placeholders")

# PASO 5: Sincronizar FALLADOS desde CAJA
print("\n[5] Sincronizando FALLADOS...")
resp_f = sheets.values().get(spreadsheetId=SPEC_ID, range="FALLADOS!A1:K").execute()
fallados_all = resp_f.get("values", [])
fallados_data = fallados_all[1:] if len(fallados_all) > 1 else []

fallados_con_imei = []
fallados_placeholders_por_modelo = defaultdict(list)
ultimo_numero_fallados = 0

for row in fallados_data:
    row_p = pad_row(row, 11)
    num_str = get_val(row_p, 0)
    try:
        n = int(num_str)
        if n > ultimo_numero_fallados:
            ultimo_numero_fallados = n
    except:
        pass
    imei = get_val(row_p, 1)
    modelo = get_val(row_p, 2)
    if imei:
        fallados_con_imei.append(row_p)
    else:
        fallados_placeholders_por_modelo[modelo].append(row_p)

print(f"  Con IMEI: {len(fallados_con_imei)}, Último N°: {ultimo_numero_fallados}")

fallados_imei_por_modelo = defaultdict(int)
for row in fallados_con_imei:
    fallados_imei_por_modelo[get_val(row, 2)] += 1

for modelo, info in fallados_caja.items():
    qty_caja = info["qty"]
    qty_imei_f = fallados_imei_por_modelo[modelo]
    qty_ph_necesaria = max(0, qty_caja - qty_imei_f)
    qty_ph_actual = len(fallados_placeholders_por_modelo[modelo])
    diff = qty_ph_necesaria - qty_ph_actual
    print(f"  FALLADO {modelo}: caja={qty_caja}, IMEI={qty_imei_f}, PH actual={qty_ph_actual}, PH necesaria={qty_ph_necesaria}, diff={diff:+d}")

    if diff > 0:
        for _ in range(diff):
            ultimo_numero_fallados += 1
            ph_row = [str(ultimo_numero_fallados), "", modelo, info["colores"], "", "", "", "EN REPARACIÓN", "", "", ""]
            fallados_placeholders_por_modelo[modelo].append(ph_row)
            print(f"    + Nuevo PH FALLADOS N°{ultimo_numero_fallados}")
    elif diff < 0:
        exceso = -diff
        ph_list = fallados_placeholders_por_modelo[modelo]
        fallados_placeholders_por_modelo[modelo] = ph_list[:len(ph_list)-exceso]
        print(f"    - Eliminados {exceso} PH FALLADOS")

all_ph_fallados = []
for phs in fallados_placeholders_por_modelo.values():
    all_ph_fallados.extend(phs)
all_ph_fallados.sort(key=lambda r: int(get_val(r, 0)) if get_val(r, 0).isdigit() else 0)

fallados_finales = fallados_con_imei + all_ph_fallados
sheets.values().clear(spreadsheetId=SPEC_ID, range="FALLADOS!A2:Z", body={}).execute()
if fallados_finales:
    sheets.values().update(
        spreadsheetId=SPEC_ID, range="FALLADOS!A2",
        valueInputOption="RAW", body={"values": fallados_finales}
    ).execute()
print(f"  FALLADOS reescrito: {len(fallados_finales)} filas")

# PASO 7: Auto-completar CLIENTE en FALLADOS
print("\n[7] Auto-completando CLIENTE en FALLADOS...")
resp_ped = sheets.values().get(spreadsheetId=SPEC_ID, range="PEDIDOS!A1:Z").execute()
pedidos_all = resp_ped.get("values", [])
pedidos_rows = pedidos_all[1:] if len(pedidos_all) > 1 else []

imei_to_cliente = {}
for row in pedidos_rows:
    imei = get_val(row, 6)
    cliente = get_val(row, 3)
    if imei and cliente:
        imei_to_cliente[imei] = cliente

resp_f2 = sheets.values().get(spreadsheetId=SPEC_ID, range="FALLADOS!A2:K").execute()
fallados_updated = resp_f2.get("values", [])
updates_cliente = 0
for i, row in enumerate(fallados_updated):
    imei = get_val(row, 1)
    cliente_actual = get_val(row, 4)
    if imei and not cliente_actual and imei in imei_to_cliente:
        row_number = i + 2
        sheets.values().update(
            spreadsheetId=SPEC_ID,
            range=f"FALLADOS!E{row_number}",
            valueInputOption="RAW",
            body={"values": [[imei_to_cliente[imei]]]}
        ).execute()
        updates_cliente += 1
        print(f"  FALLADOS fila {row_number}: IMEI {imei} → cliente {imei_to_cliente[imei]}")
print(f"  Clientes auto-completados: {updates_cliente}")

# PASO 8: Procesar FALLADOS → SCANNER
print("\n[8] Procesando FALLADOS → SCANNER...")

resp_sc = sheets.values().get(spreadsheetId=SPEC_ID, range="SCANNER!A1:M").execute()
scanner_all = resp_sc.get("values", [])
scanner_history = scanner_all[13:] if len(scanner_all) > 13 else []

scanner_imei_map = {}
ultimo_num_scanner = 0
for i, row in enumerate(scanner_history):
    num_str = get_val(row, 0)
    try:
        n = int(num_str)
        if n > ultimo_num_scanner:
            ultimo_num_scanner = n
    except:
        pass
    imei = get_val(row, 3)
    if imei:
        scanner_imei_map[imei] = (13 + i + 1, row)

print(f"  Scanner history: {len(scanner_history)} filas, último N°: {ultimo_num_scanner}")

resp_f3 = sheets.values().get(spreadsheetId=SPEC_ID, range="FALLADOS!A2:K").execute()
fallados_for_scanner = resp_f3.get("values", [])

imeis_reparados_egresados = []
spec_imeis_actuales = set(get_val(r, 3) for r in con_imei)

for row in fallados_for_scanner:
    imei = get_val(row, 1)
    if not imei:
        continue

    modelo_f = get_val(row, 2)
    color_f = get_val(row, 3)
    fecha_venta_orig = get_val(row, 5)
    fecha_reingreso = get_val(row, 6)
    estado_f = get_val(row, 7).upper()
    fecha_salida_taller = get_val(row, 9)

    if imei in scanner_imei_map:
        sc_row_num, sc_row = scanner_imei_map[imei]
        fecha_reingreso_sc = get_val(sc_row, 12)
        if not fecha_reingreso_sc and fecha_reingreso:
            sheets.values().update(
                spreadsheetId=SPEC_ID,
                range=f"SCANNER!M{sc_row_num}",
                valueInputOption="RAW",
                body={"values": [[fecha_reingreso]]}
            ).execute()
            print(f"  Scanner fila {sc_row_num}: IMEI {imei} → FECHA REINGRESO '{fecha_reingreso}'")
    else:
        ultimo_num_scanner += 1
        nueva_fila_sc = [
            str(ultimo_num_scanner), modelo_f, color_f, imei,
            "", "", "STOCK USED", "",
            fecha_venta_orig, "Sin registro previo",
            "", "", fecha_reingreso
        ]
        sheets.values().append(
            spreadsheetId=SPEC_ID,
            range="SCANNER!A14",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [nueva_fila_sc]}
        ).execute()
        scanner_imei_map[imei] = (None, nueva_fila_sc)
        print(f"  Scanner: IMEI {imei} sin registro previo → agregado N°{ultimo_num_scanner}")

    if estado_f == "REPARADO" and fecha_salida_taller:
        if imei not in spec_imeis_actuales:
            ultimo_numero += 1
            fila_reparado = [
                str(ultimo_numero), modelo_f, color_f, imei,
                "", "", "STOCK USED", fecha_reingreso,
                "", "", "", ""
            ]
            con_imei.append(fila_reparado)
            spec_imeis_actuales.add(imei)
            imeis_reparados_egresados.append(imei)
            print(f"  REPARADO: IMEI {imei} agregado a SPEC STOCK N°{ultimo_numero}")
        else:
            imeis_reparados_egresados.append(imei)
            print(f"  REPARADO: IMEI {imei} ya en SPEC STOCK, eliminando de FALLADOS")

if imeis_reparados_egresados:
    resp_f4 = sheets.values().get(spreadsheetId=SPEC_ID, range="FALLADOS!A2:K").execute()
    fallados_current = resp_f4.get("values", [])
    fallados_final2 = [r for r in fallados_current if str(get_val(r, 1)).strip() not in imeis_reparados_egresados]
    sheets.values().clear(spreadsheetId=SPEC_ID, range="FALLADOS!A2:Z", body={}).execute()
    if fallados_final2:
        sheets.values().update(
            spreadsheetId=SPEC_ID, range="FALLADOS!A2",
            valueInputOption="RAW", body={"values": fallados_final2}
        ).execute()
    print(f"  FALLADOS: eliminados {len(imeis_reparados_egresados)} reparados")

# Reconciliación final de placeholders
print("\n[8b] Reconciliando placeholders post-REPARADO...")
for modelo in list(placeholder_por_modelo.keys()):
    qty_caja = stock_caja.get(modelo, {}).get("qty", 0)
    qty_imei = len([r for r in con_imei if get_val(r, 1) == modelo])
    qty_ph_needed = max(0, qty_caja - qty_imei)
    qty_ph_actual = len(placeholder_por_modelo[modelo])
    if qty_ph_actual > qty_ph_needed:
        removed = qty_ph_actual - qty_ph_needed
        placeholder_por_modelo[modelo] = placeholder_por_modelo[modelo][:qty_ph_needed]
        print(f"  {modelo}: eliminados {removed} PH sobrantes (caja={qty_caja}, IMEI={qty_imei})")

# PASO 11: Escribir ESPECIFICACIÓN STOCK
print("\n[11] Escribiendo ESPECIFICACIÓN STOCK...")

all_placeholders = []
for phs in placeholder_por_modelo.values():
    all_placeholders.extend(phs)

output_rows = con_imei + all_placeholders

MODEL_ORDER = [
    "iPhone 13 128GB", "iPhone 13 Pro 128GB", "iPhone 13 Pro Max 128GB",
    "iPhone 14 128GB", "iPhone 14 Pro 128GB", "iPhone 14 Pro Max 256GB",
    "iPhone 15 128GB", "iPhone 15 Pro 128GB", "iPhone 15 Pro Max 256GB",
    "iPhone 16 128GB", "iPhone 16 Pro 128GB", "iPhone 16 Pro 256GB", "iPhone 16 Pro Max 256GB",
]

def model_sort_idx(modelo):
    try:
        return MODEL_ORDER.index(modelo)
    except ValueError:
        return len(MODEL_ORDER)

def sort_key(r):
    modelo = get_val(r, 1)
    imei = get_val(r, 3)
    has_imei = 0 if imei else 1
    num = int(get_val(r, 0)) if get_val(r, 0).isdigit() else 99999
    return (model_sort_idx(modelo), has_imei, num)

output_rows.sort(key=sort_key)
output_rows = [pad_row(r, 12) for r in output_rows]

sheets.values().clear(spreadsheetId=SPEC_ID, range=f"{SPEC_SHEET}!A2:Z", body={}).execute()
if output_rows:
    sheets.values().update(
        spreadsheetId=SPEC_ID,
        range=f"{SPEC_SHEET}!A2",
        valueInputOption="RAW",
        body={"values": output_rows}
    ).execute()
print(f"  Escrito: {len(output_rows)} filas ({len(con_imei)} con IMEI, {len(all_placeholders)} placeholders)")

# PASO 9: Actualizar RESUMEN CLIENTES
print("\n[9] Actualizando RESUMEN CLIENTES...")

resp_sc2 = sheets.values().get(spreadsheetId=SPEC_ID, range="SCANNER!A1:M").execute()
scanner_all2 = resp_sc2.get("values", [])
scanner_history2 = scanner_all2[13:] if len(scanner_all2) > 13 else []

scanner_precio = {}
for row in scanner_history2:
    imei = get_val(row, 3)
    precio_str = get_val(row, 5).replace("$", "").replace(".", "").replace(",", "").strip()
    if imei and precio_str:
        try:
            scanner_precio[imei] = int(precio_str)
        except:
            pass

resp_ped2 = sheets.values().get(spreadsheetId=SPEC_ID, range="PEDIDOS!A1:Z").execute()
pedidos_all2 = resp_ped2.get("values", [])
pedidos_rows2 = pedidos_all2[1:] if len(pedidos_all2) > 1 else []

resp_f5 = sheets.values().get(spreadsheetId=SPEC_ID, range="FALLADOS!A2:K").execute()
fallados_rows2 = resp_f5.get("values", [])

resumen = defaultdict(lambda: {"equipos": 0, "total_lista": 0, "devueltos": 0, "valor_devuelto": 0})

for row in pedidos_rows2:
    cliente = get_val(row, 3)
    imei = get_val(row, 6)
    if not cliente or not imei:
        continue
    precio = scanner_precio.get(imei, 0)
    resumen[cliente]["equipos"] += 1
    resumen[cliente]["total_lista"] += precio

for row in fallados_rows2:
    cliente = get_val(row, 4)
    imei = get_val(row, 1)
    if not cliente or not imei:
        continue
    precio = scanner_precio.get(imei, 0)
    resumen[cliente]["devueltos"] += 1
    resumen[cliente]["valor_devuelto"] += precio

resumen_rows = []
total_equipos = total_lista = total_devueltos = total_valor_dev = 0
for cliente in sorted(resumen.keys()):
    d = resumen[cliente]
    resumen_rows.append([cliente, d["equipos"], d["total_lista"], d["devueltos"], d["valor_devuelto"]])
    total_equipos += d["equipos"]
    total_lista += d["total_lista"]
    total_devueltos += d["devueltos"]
    total_valor_dev += d["valor_devuelto"]

resumen_rows.append(["TOTAL", total_equipos, total_lista, total_devueltos, total_valor_dev])

sheets.values().update(
    spreadsheetId=SPEC_ID, range="RESUMEN CLIENTES!A1:E1",
    valueInputOption="RAW",
    body={"values": [["CLIENTE", "EQUIPOS", "TOTAL LISTA ($)", "DEVUELTOS", "VALOR DEVUELTO ($)"]]}
).execute()
sheets.values().clear(spreadsheetId=SPEC_ID, range="RESUMEN CLIENTES!A2:Z", body={}).execute()
if resumen_rows:
    sheets.values().update(
        spreadsheetId=SPEC_ID, range="RESUMEN CLIENTES!A2",
        valueInputOption="RAW", body={"values": resumen_rows}
    ).execute()
print(f"  Clientes: {len(resumen)}, Total equipos: {total_equipos}, Total lista: ${total_lista}")

# Obtener sheetIds para formato
meta = sheets.get(spreadsheetId=SPEC_ID, fields="sheets.properties").execute()
sheet_id_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
resumen_sheet_id = sheet_id_map.get("RESUMEN CLIENTES")
pedidos_sheet_id = sheet_id_map.get("PEDIDOS")

n_data_rows = len(resumen_rows)
total_row_idx = n_data_rows

if resumen_sheet_id is not None:
    fmt_requests = [
        {"repeatCell": {
            "range": {"sheetId": resumen_sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }},
        {"repeatCell": {
            "range": {"sheetId": resumen_sheet_id, "startRowIndex": total_row_idx, "endRowIndex": total_row_idx+1, "startColumnIndex": 0, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
                "textFormat": {"bold": True}
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }},
    ]
    sheets.batchUpdate(spreadsheetId=SPEC_ID, body={"requests": fmt_requests}).execute()
    print("  Formato RESUMEN CLIENTES aplicado")

# PASO 10: Colores PEDIDOS
print("\n[10] Actualizando colores PEDIDOS...")

PALETTE = [
    (173, 216, 230), (216, 191, 216), (144, 238, 144), (255, 218, 185),
    (135, 206, 235), (221, 160, 221), (152, 251, 152), (255, 228, 196),
    (176, 224, 230), (238, 130, 238), (240, 230, 140), (175, 238, 238),
    (255, 160, 122), (100, 149, 237), (60, 179, 113),
]

colors_file = BASE_DIR / "client_colors.json"
if colors_file.exists():
    with open(colors_file) as f:
        client_colors = json.load(f)
else:
    client_colors = {}

clientes_pedidos = sorted(set(
    get_val(row, 3) for row in pedidos_rows2
    if get_val(row, 3)
))

palette_idx = len(client_colors)
for cliente in clientes_pedidos:
    if cliente not in client_colors:
        color = PALETTE[palette_idx % len(PALETTE)]
        client_colors[cliente] = color
        palette_idx += 1
        print(f"  Nuevo cliente '{cliente}': color {color}")

with open(colors_file, "w") as f:
    json.dump(client_colors, f, indent=2)

if pedidos_sheet_id is not None:
    color_requests = []
    for i, row in enumerate(pedidos_rows2):
        cliente = get_val(row, 3)
        if cliente in client_colors:
            r_val, g_val, b_val = client_colors[cliente]
            color_requests.append({"repeatCell": {
                "range": {"sheetId": pedidos_sheet_id, "startRowIndex": i+1, "endRowIndex": i+2, "startColumnIndex": 0, "endColumnIndex": 20},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": r_val/255, "green": g_val/255, "blue": b_val/255}
                }},
                "fields": "userEnteredFormat.backgroundColor"
            }})

    if color_requests:
        for batch_start in range(0, len(color_requests), 100):
            batch = color_requests[batch_start:batch_start+100]
            sheets.batchUpdate(spreadsheetId=SPEC_ID, body={"requests": batch}).execute()
        print(f"  Colores aplicados a {len(color_requests)} filas de PEDIDOS")

print("\n=== SYNC COMPLETADO ===")
