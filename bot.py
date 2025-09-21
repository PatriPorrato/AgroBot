# bot.py
# APEX Agro Bot — precios pizarra BCR + TC BNA + paridad con urea + gráfico
# Publica a 12:00 ART (mediodía) y 18:00 ART (cierre).
# En el cierre agrega la variación intradía respecto al mediodía.

import os, io, re, json, datetime as dt
import requests
from bs4 import BeautifulSoup
import tweepy
from dotenv import load_dotenv
import matplotlib.pyplot as plt

load_dotenv()

# ------------- Config -------------
BCR_PIZARRA_URL = "https://www.cac.bcr.com.ar/es/precios-de-pizarra"
SERIES_API = "https://datos.gob.ar/series/api/series/"
SERIE_TC_BNA = "168.1_T_CAMBIOR_D_0_0_26"  # Tipo de cambio vendedor BNA (oficial)
INSUMOS_CSV_URL = os.getenv("INSUMOS_CSV_URL", "").strip()
UREA_USD_T_ENV = os.getenv("UREA_USD_T")
BRAND = os.getenv("BRAND", "APEX")
STATE_FILE = os.getenv("STATE_FILE", ".state/mediodia.json")  # se cachea en Actions

# ------------- Utilidades -------------
def parse_money_ar(moneda_str: str) -> float:
    # "$440.000,00" -> 440000.00
    s = re.sub(r"[^\d,\.]", "", moneda_str)
    s = s.replace(".", "").replace(",", ".")
    return float(s)

def fetch_pizarra_bcr():
    """Devuelve dict {fecha, soja_ars, maiz_ars, trigo_ars} (puede faltar alguno)."""
    r = requests.get(BCR_PIZARRA_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Fecha
    fecha = dt.date.today()
    fecha_el = soup.find(string=re.compile(r"Precios Pizarra del día", re.I))
    if fecha_el:
        m = re.search(r"(\d{2}/\d{2}/\d{4})", fecha_el)
        if m:
            try:
                fecha = dt.datetime.strptime(m.group(1), "%d/%m/%Y").date()
            except Exception:
                pass

    # Valores
    valores = {"soja_ars": None, "maiz_ars": None, "trigo_ars": None}
    for card in soup.select("div.card, div.views-row, section"):
        texto = " ".join(card.get_text(" ").split())
        if re.search(r"\bSoja\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto)
            if m: valores["soja_ars"] = parse_money_ar(m.group(0))
        if re.search(r"\bMa[ií]z\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto)
            if m: valores["maiz_ars"] = parse_money_ar(m.group(0))
        if re.search(r"\bTrigo\b", texto, re.I):
            m = re.search(r"\$[\d\.\,]+", texto)
            if m: valores["trigo_ars"] = parse_money_ar(m.group(0))

    if not any(valores.values()):
        raise RuntimeError("No pude leer pizarra BCR. ¿Cambió el HTML?")

    return {"fecha": fecha, **valores}

def fetch_tc_bna():
    """Devuelve último tipo de cambio vendedor BNA (ARS por USD)."""
    params = {"ids": SERIE_TC_BNA, "limit": 1, "sort": "desc"}
    r = requests.get(SERIES_API, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    data = None
    if "data" in j and isinstance(j["data"], list):
        data = j["data"]
    elif "series" in j and j.get("series"):
        data = j["series"][0].get("data", [])
    if not data:
        raise RuntimeError("Sin datos de TC en datos.gob.ar")
    row = data[0]  # ["YYYY-MM-DD", valor]
    return float(row[1])

def fetch_urea_usd():
    """Devuelve precio urea USD/t desde CSV público (si hay) o .env; fallback=760."""
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

def to_usd(ars, tc):
    return None if (ars is None or tc <= 0) else round(ars / tc, 2)

def build_chart(prices_usd, fecha, marca):
    fig = plt.figure(figsize=(6, 4), dpi=160)
    items = list(prices_usd.keys())
    vals = [prices_usd[k] for k in items]
    plt.bar(items, vals)
    plt.title(f"{marca} · {fecha:%d-%m-%Y}")
    plt.ylabel("USD / t")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

def post_to_x(text, image_bytesio=None):
    client = tweepy.Client(
        consumer_key=os.getenv("X_API_KEY"),
        consumer_secret=os.getenv("X_API_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_SECRET"),
    )
    if image_bytesio:
        # Subida de media usa v1.1 en Tweepy
        auth = tweepy.OAuth1UserHandler(
            os.getenv("X_API_KEY"),
            os.getenv("X_API_SECRET"),
            os.getenv("X_ACCESS_TOKEN"),
            os.getenv("X_ACCESS_SECRET"),
        )
        api = tweepy.API(auth)
        media = api.media_upload(filename="chart.png", file=image_bytesio)
        resp = client.create_tweet(text=text, media_ids=[media.media_id])
    else:
        resp = client.create_tweet(text=text)
    print("Publicado:", resp)

# ------------- Estado intradía (cachea mediodía) -------------
def save_mediodia(prices_usd, fecha):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fecha": fecha.strftime("%Y-%m-%d"), "prices_usd": prices_usd}, f)

def load_mediodia(fecha):
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    # Sólo si es el mismo día
    if data.get("fecha") == fecha.strftime("%Y-%m-%d"):
        return data.get("prices_usd")
    return None

# ------------- Main -------------
if __name__ == "__main__":
    info = fetch_pizarra_bcr()
    tc = fetch_tc_bna()
    urea_usd = fetch_urea_usd()

    soja_usd  = to_usd(info.get("soja_ars"), tc)
    maiz_usd  = to_usd(info.get("maiz_ars"), tc)
    trigo_usd = to_usd(info.get("trigo_ars"), tc)

    prices_usd = {}
    if soja_usd is not None:  prices_usd["Soja"] = soja_usd
    if maiz_usd is not None:  prices_usd["Maíz"] = maiz_usd
    if trigo_usd is not None: prices_usd["Trigo"] = trigo_usd
    prices_usd["Urea"] = round(urea_usd, 2)

    # Relaciones (cuántas t de urea compra 1 t de grano)
    def rel(prod_usd):
        return None if (prod_usd is None or urea_usd <= 0) else round(prod_usd / urea_usd, 2)
    rels = []
    if soja_usd is not None:  rels.append(f"1 t soja ≈ {rel(soja_usd)} t urea")
    if maiz_usd is not None:  rels.append(f"1 t maíz ≈ {rel(maiz_usd)} t urea")
    if trigo_usd is not None: rels.append(f"1 t trigo ≈ {rel(trigo_usd)} t urea")

    fecha = info["fecha"]
    lines = [f"🧾 Pizarra BCR {fecha:%d-%m-%Y}"]
    if info.get("soja_ars"):  lines.append(f"Soja: ${info['soja_ars']:,.0f}/t  (~USD {soja_usd})".replace(",", "."))
    if info.get("maiz_ars"):  lines.append(f"Maíz: ${info['maiz_ars']:,.0f}/t  (~USD {maiz_usd})".replace(",", "."))
    if info.get("trigo_ars"): lines.append(f"Trigo: ${info['trigo_ars']:,.0f}/t  (~USD {trigo_usd})".replace(",", "."))
    lines.append(f"💵 TC oficial ~ ${tc:,.2f}".replace(",", "."))

    if rels: lines.append("🧮 " + " | ".join(rels))

    # ¿Estamos en MEDIODÍA o CIERRE?
    # Si usás el workflow recomendado, a las 15:00 UTC (12 ART) es MEDIODÍA;
    # a las 21:00 UTC (18 ART) es CIERRE.
    run_mode = os.getenv("RUN_MODE")  # opcional: MEDIODIA / CIERRE
    hour_utc = dt.datetime.utcnow().hour
    if not run_mode:
        run_mode = "MEDIODIA" if hour_utc == 15 else ("CIERRE" if hour_utc == 21 else "MEDIODIA")

    if run_mode.upper() == "MEDIODIA":
        save_mediodia(prices_usd, fecha)
        lines.append("#Agro #Soja #Maíz #Trigo #Fertilizantes")
    else:
        prev = load_mediodia(fecha)
        if prev:
            variaciones = []
            for k, v in prices_usd.items():
                if k in prev:
                    delta = v - prev[k]
                    signo = "+" if delta >= 0 else ""
                    variaciones.append(f"{k}: {signo}{delta:.1f} USD")
            if variaciones:
                lines.append("📊 Variación intradía: " + ", ".join(variaciones))
        lines.append("#Agro #CierreDeMercado #Soja #Maíz #Trigo")

    text = "\n".join(lines)
    chart_buf = build_chart(prices_usd, fecha, BRAND)
    post_to_x(text, image_bytesio=chart_buf)
