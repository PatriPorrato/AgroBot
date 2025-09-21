# bot.py
# APEX Agro Bot — Pizarra BCR + TC BNA + Paridad Urea + Gráfico
# Modos:
#  - MEDIODIA (12:00 ART)
#  - CIERRE   (18:00 ART) con variación intradía vs mediodía
#  - SEMANA   (domingos 20:00 ART): promedio semanal + variación vs semana previa + gráfico de tendencia
#
# RUN_MODE (opcional): MEDIODIA / CIERRE / SEMANA — si no está, se infiere por hora UTC y día.

import os, io, re, json, csv, datetime as dt
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
import tweepy
from dotenv import load_dotenv
import matplotlib.pyplot as plt

load_dotenv()

# ---------- Config ----------
BCR_PIZARRA_URL = "https://www.cac.bcr.com.ar/es/precios-de-pizarra"
SERIES_API = "https://datos.gob.ar/series/api/series/"
SERIE_TC_BNA = "168.1_T_CAMBIOR_D_0_0_26"  # TC vendedor BNA (oficial)
INSUMOS_CSV_URL = os.getenv("INSUMOS_CSV_URL", "").strip()
UREA_USD_T_ENV = os.getenv("UREA_USD_T")
BRAND = os.getenv("BRAND", "APEX")

STATE_DIR = ".state"
STATE_FILE_MEDIO = os.path.join(STATE_DIR, "mediodia.json")
STATE_FILE_DAILY = os.path.join(STATE_DIR, "daily.csv")  # historial diario en USD

# ---------- Utils scraping / datos ----------
def parse_money_ar(moneda_str: str) -> float:
    s = re.sub(r"[^\d,\.]", "", moneda_str)
    s = s.replace(".", "").replace(",", ".")
    return float(s)

def fetch_pizarra_bcr():
    r = requests.get(BCR_PIZARRA_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    fecha = dt.date.today()
    fecha_el = soup.find(string=re.compile(r"Precios Pizarra del día", re.I))
    if fecha_el:
        m = re.search(r"(\d{2}/\d{2}/\d{4})", fecha_el)
        if m:
            try:
                fecha = dt.datetime.strptime(m.group(1), "%d/%m/%Y").date()
            except Exception:
                pass

    valores = {"soja_ars": None, "maiz_ars": None, "trigo_ars": None}
    for card in soup.select("div.card, div.views-row, section"):
        texto = " ".join(card.get_text(" ").split())
        if re.search(r"\bSoja\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto);  valores["soja_ars"]  = parse_money_ar(m.group(0)) if m else valores["soja_ars"]
        if re.search(r"\bMa[ií]z\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto);  valores["maiz_ars"]  = parse_money_ar(m.group(0)) if m else valores["maiz_ars"]
        if re.search(r"\bTrigo\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto);  valores["trigo_ars"] = parse_money_ar(m.group(0)) if m else valores["trigo_ars"]

    if not any(valores.values()):
        raise RuntimeError("No pude leer pizarra BCR. ¿Cambió el HTML?")
    return {"fecha": fecha, **valores}

def fetch_tc_bna() -> float:
    params = {"ids": SERIE_TC_BNA, "limit": 1, "sort": "desc"}
    r = requests.get(SERIES_API, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    data = j.get("data") if isinstance(j.get("data"), list) else (j.get("series",[{}])[0].get("data",[]))
    if not data:
        raise RuntimeError("Sin datos de TC en datos.gob.ar")
    return float(data[0][1])  # ["YYYY-MM-DD", valor]

def fetch_urea_usd() -> float:
    if INSUMOS_CSV_URL:
        try:
            r = requests.get(INSUMOS_CSV_URL, timeout=30)
            r.raise_for_status()
            lines = r.text.strip().splitlines()
            for line in reversed(lines[1:]):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and parts[1].lower() == "urea":
                    return float(parts[2])
        except Exception:
            pass
    if UREA_USD_T_ENV:
        try:
            return float(UREA_USD_T_ENV)
        except Exception:
            pass
    return 760.0

def to_usd(ars: Optional[float], tc: float) -> Optional[float]:
    return None if (ars is None or tc <= 0) else round(ars / tc, 2)

# ---------- Posteo / gráficos ----------
def build_bar_chart(prices_usd: Dict[str,float], fecha: dt.date, marca: str) -> io.BytesIO:
    fig = plt.figure(figsize=(6,4), dpi=160)
    items = list(prices_usd.keys()); vals = [prices_usd[k] for k in items]
    plt.bar(items, vals)
    plt.title(f"{marca} · {fecha:%d-%m-%Y}")
    plt.ylabel("USD / t")
    plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig); buf.seek(0); return buf

def build_week_line_chart(rows: List[Dict[str,str]], marca: str, title_suffix="Resumen semanal") -> io.BytesIO:
    # rows: lista con dicts {"date","soja","maiz","trigo"} (strings). Tomamos hasta 7 días.
    dates = [dt.datetime.strptime(r["date"], "%Y-%m-%d").date() for r in rows]
    soja  = [float(r["soja"])  if r["soja"]  else None for r in rows]
    maiz  = [float(r["maiz"])  if r["maiz"]  else None for r in rows]
    trigo = [float(r["trigo"]) if r["trigo"] else None for r in rows]

    fig = plt.figure(figsize=(7,4), dpi=160)
    # Matplotlib ignora None y corta la línea; para simplicidad, interpolación no
    plt.plot(dates, soja,  marker="o", label="Soja")
    plt.plot(dates, maiz,  marker="o", label="Maíz")
    plt.plot(dates, trigo, marker="o", label="Trigo")
    plt.title(f"{marca} · {title_suffix}")
    plt.xlabel("Fecha"); plt.ylabel("USD / t")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend()
    plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig); buf.seek(0); return buf

def post_to_x(text: str, image_bytesio: Optional[io.BytesIO]=None):
    client = tweepy.Client(
        consumer_key=os.getenv("X_API_KEY"),
        consumer_secret=os.getenv("X_API_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_SECRET"),
    )
    if image_bytesio:
        # Subida media v1.1
        auth = tweepy.OAuth1UserHandler(
            os.getenv("X_API_KEY"), os.getenv("X_API_SECRET"),
            os.getenv("X_ACCESS_TOKEN"), os.getenv("X_ACCESS_SECRET"),
        )
        api = tweepy.API(auth)
        media = api.media_upload(filename="chart.png", file=image_bytesio)
        resp = client.create_tweet(text=text, media_ids=[media.media_id])
    else:
        resp = client.create_tweet(text=text)
    print("Publicado:", resp)

# ---------- Estado intradía / historial ----------
def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)

def save_mediodia(prices_usd: Dict[str,float], fecha: dt.date):
    ensure_state_dir()
    with open(STATE_FILE_MEDIO, "w", encoding="utf-8") as f:
        json.dump({"fecha": fecha.strftime("%Y-%m-%d"), "prices_usd": prices_usd}, f)

def load_mediodia(fecha: dt.date) -> Optional[Dict[str,float]]:
    if not os.path.exists(STATE_FILE_MEDIO):
        return None
    with open(STATE_FILE_MEDIO, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("fecha") == fecha.strftime("%Y-%m-%d"):
        return data.get("prices_usd")
    return None

def upsert_daily_csv(fecha: dt.date, soja: Optional[float], maiz: Optional[float], trigo: Optional[float]):
    """Guarda/actualiza fila US$ del día en .state/daily.csv (date,soja,maiz,trigo)."""
    ensure_state_dir()
    rows: List[Dict[str,str]] = []
    if os.path.exists(STATE_FILE_DAILY):
        with open(STATE_FILE_DAILY, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    date_str = fecha.strftime("%Y-%m-%d")
    found = False
    for r in rows:
        if r["date"] == date_str:
            r["soja"]  = "" if soja  is None else f"{soja:.2f}"
            r["maiz"]  = "" if maiz  is None else f"{maiz:.2f}"
            r["trigo"] = "" if trigo is None else f"{trigo:.2f}"
            found = True; break
    if not found:
        rows.append({
            "date": date_str,
            "soja":  "" if soja  is None else f"{soja:.2f}",
            "maiz":  "" if maiz  is None else f"{maiz:.2f}",
            "trigo": "" if trigo is None else f"{trigo:.2f}",
        })
    with open(STATE_FILE_DAILY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date","soja","maiz","trigo"])
        w.writeheader(); w.writerows(sorted(rows, key=lambda r: r["date"]))

def load_window_rows(end_date: dt.date, days: int) -> List[Dict[str,str]]:
    """Devuelve últimas 'days' filas hasta end_date inclusive."""
    if not os.path.exists(STATE_FILE_DAILY):
        return []
    with open(STATE_FILE_DAILY, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    start = end_date - dt.timedelta(days=days-1)
    out = [r for r in rows if start.strftime("%Y-%m-%d") <= r["date"] <= end_date.strftime("%Y-%m-%d")]
    # Garantizar orden por fecha
    out.sort(key=lambda r: r["date"])
    return out

def mean_safe(vals: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return round(sum(xs)/len(xs), 1) if xs else None

# ---------- Lógica principal ----------
if __name__ == "__main__":
    # Detectar modo
    run_mode = (os.getenv("RUN_MODE") or "").upper().strip()
    now_utc = dt.datetime.utcnow()
    if not run_mode:
        # Por defecto: 15 UTC = MEDIODIA; 21 UTC = CIERRE; domingo 23 UTC = SEMANA
        if now_utc.weekday() == 6 and now_utc.hour == 23:
            run_mode = "SEMANA"
        elif now_utc.hour == 21:
            run_mode = "CIERRE"
        else:
            run_mode = "MEDIODIA"

    # Datos del día
    info = fetch_pizarra_bcr()
    tc = fetch_tc_bna()
    urea_usd = fetch_urea_usd()

    soja_usd  = to_usd(info.get("soja_ars"), tc)
    maiz_usd  = to_usd(info.get("maiz_ars"), tc)
    trigo_usd = to_usd(info.get("trigo_ars"), tc)

    fecha = info["fecha"]

    # Texto base diario
    lines = [f"🧾 Pizarra BCR {fecha:%d-%m-%Y}"]
    if info.get("soja_ars"):  lines.append(f"Soja: ${info['soja_ars']:,.0f}/t  (~USD {soja_usd})".replace(",", "."))
    if info.get("maiz_ars"):  lines.append(f"Maíz: ${info['maiz_ars']:,.0f}/t  (~USD {maiz_usd})".replace(",", "."))
    if info.get("trigo_ars"): lines.append(f"Trigo: ${info['trigo_ars']:,.0f}/t  (~USD {trigo_usd})".replace(",", "."))
    lines.append(f"💵 TC oficial ~ ${tc:,.2f}".replace(",", "."))

    prices_usd = {}
    if soja_usd  is not None: prices_usd["Soja"]  = soja_usd
    if maiz_usd  is not None: prices_usd["Maíz"]  = maiz_usd
    if trigo_usd is not None: prices_usd["Trigo"] = trigo_usd
    prices_usd["Urea"] = round(urea_usd, 2)

    # Relaciones rápidas
    def rel(p): return None if (p is None or urea_usd <= 0) else round(p/urea_usd, 2)
    rels = []
    if soja_usd  is not None: rels.append(f"1 t soja ≈ {rel(soja_usd)} t urea")
    if maiz_usd  is not None: rels.append(f"1 t maíz ≈ {rel(maiz_usd)} t urea")
    if trigo_usd is not None: rels.append(f"1 t trigo ≈ {rel(trigo_usd)} t urea")
    if rels: lines.append("🧮 " + " | ".join(rels))

    if run_mode == "MEDIODIA":
        save_mediodia(prices_usd, fecha)
        upsert_daily_csv(fecha, soja_usd, maiz_usd, trigo_usd)
        lines.append("#Agro #Soja #Maíz #Trigo #Fertilizantes")
        text = "\n".join(lines)
        img = build_bar_chart(prices_usd, fecha, BRAND)
        post_to_x(text, img)

    elif run_mode == "CIERRE":
        # variación intradía
        prev = load_mediodia(fecha)
        if prev:
            variaciones = []
            for k, v in prices_usd.items():
                if k in prev and isinstance(prev[k], (int,float)):
                    delta = v - prev[k]
                    signo = "+" if delta >= 0 else ""
                    variaciones.append(f"{k}: {signo}{delta:.1f} USD")
            if variaciones:
                lines.append("📊 Variación intradía: " + ", ".join(variaciones))
        upsert_daily_csv(fecha, soja_usd, maiz_usd, trigo_usd)
        lines.append("#Agro #CierreDeMercado #Soja #Maíz #Trigo")
        text = "\n".join(lines)
        img = build_bar_chart(prices_usd, fecha, BRAND)
        post_to_x(text, img)

    elif run_mode == "SEMANA":
        # Semana que termina hoy (domingo): tomamos últimos 7 días
        end_date = fecha
        week_rows = load_window_rows(end_date, 7)
        prev_rows = load_window_rows(end_date - dt.timedelta(days=7), 7)

        def to_float_list(rows, key):
            out = []
            for r in rows:
                try: out.append(float(r[key])) if r[key] else out.append(None)
                except: out.append(None)
            return out

        soja_w  = to_float_list(week_rows, "soja")
        maiz_w  = to_float_list(week_rows, "maiz")
        trigo_w = to_float_list(week_rows, "trigo")
        soja_p  = to_float_list(prev_rows, "soja")
        maiz_p  = to_float_list(prev_rows, "maiz")
        trigo_p = to_float_list(prev_rows, "trigo")

        mean_soja = mean_safe(soja_w); mean_maiz = mean_safe(maiz_w); mean_trigo = mean_safe(trigo_w)
        mean_soja_prev = mean_safe(soja_p); mean_maiz_prev = mean_safe(maiz_p); mean_trigo_prev = mean_safe(trigo_p)

        lines = [f"📅 Semana { (end_date - dt.timedelta(days=6)).strftime('%d-%m') }–{ end_date.strftime('%d-%m-%Y') }"]
        if mean_soja is not None:
            delta = (mean_soja - mean_soja_prev) if (mean_soja_prev is not None) else None
            s = f"Soja: prom. USD {mean_soja:.1f}/t"
            if delta is not None: s += f" (vs semana pasada {'+' if delta>=0 else ''}{delta:.1f})"
            lines.append(s)
        if mean_maiz is not None:
            delta = (mean_maiz - mean_maiz_prev) if (mean_maiz_prev is not None) else None
            s = f"Maíz: prom. USD {mean_maiz:.1f}/t"
            if delta is not None: s += f" (vs semana pasada {'+' if delta>=0 else ''}{delta:.1f})"
            lines.append(s)
        if mean_trigo is not None:
            delta = (mean_trigo - mean_trigo_prev) if (mean_trigo_prev is not None) else None
            s = f"Trigo: prom. USD {mean_trigo:.1f}/t"
            if delta is not None: s += f" (vs semana pasada {'+' if delta>=0 else ''}{delta:.1f})"
            lines.append(s)

        lines.append("#Agro #Semana #Soja #Maíz #Trigo")
        text = "\n".join(lines)

        # gráfico de tendencia (últimos 7 días)
        img = build_week_line_chart(week_rows, BRAND, "Resumen semanal")
        post_to_x(text, img)

    else:
        raise RuntimeError(f"RUN_MODE desconocido: {run_mode}")
