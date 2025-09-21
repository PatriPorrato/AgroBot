# ➜ al inicio, debajo de imports:
import sys
import matplotlib
matplotlib.use("Agg")  # headless seguro en Actions

# …dentro de fetch_pizarra_bcr() cambia el requests.get por esto:
r = requests.get(
    BCR_PIZARRA_URL,
    timeout=30,
    headers={"User-Agent": "Mozilla/5.0 (AgroBot; +https://x.com)"},
)

# …y donde hoy decimos:
# if not any(valores.values()):
#     raise RuntimeError("No pude leer pizarra BCR. ¿Cambió el HTML?")
# reemplazalo por:
if not any(valores.values()):
    # devolvemos estructura vacía pero NO rompemos el bot
    return {"fecha": fecha, **valores}

# ➜ Función helper para postear mensaje de error “amigable” sin romper:
def safe_post_error(msg):
    try:
        post_to_x(f"⚠️ {msg}\n#Agro #Bot")
    except Exception as e:
        print("Error posteando aviso:", repr(e))

# ➜ En el main, envolver todo en try/except y hacer fallback:
if __name__ == "__main__":
    try:
        info = fetch_pizarra_bcr()
        tc = fetch_tc_bna()
        urea_usd = fetch_urea_usd()

        # si no conseguimos ningún precio de pizarra, posteamos igual algo útil:
        no_pizarra = not any([info.get("soja_ars"), info.get("maiz_ars"), info.get("trigo_ars")])

        soja_usd  = to_usd(info.get("soja_ars"), tc)
        maiz_usd  = to_usd(info.get("maiz_ars"), tc)
        trigo_usd = to_usd(info.get("trigo_ars"), tc)

        fecha = info["fecha"]

        # si no hubo pizarra, publicamos un “placeholder” con TC + urea y salimos limpio
        if no_pizarra:
            text = (f"🧾 Pizarra BCR {fecha:%d-%m-%Y}\n"
                    f"Hoy no pude leer los precios de pizarra (sitio cambió o sin datos).\n"
                    f"💵 TC oficial ~ ${tc:,.2f}\n"
                    f"Urea ref.: USD {urea_usd:.0f}/t\n"
                    "#Agro #Info")
            post_to_x(text)
            sys.exit(0)

        # ----- (resto de tu lógica normal MEDIODIA/CIERRE/SEMANA igual que antes) -----

    except Exception as e:
        print("ERROR FATAL:", repr(e))
        # mandamos aviso y NO hacemos fallar el job
        safe_post_error("No pude publicar hoy (fuente no respondió). Intento nuevamente en el próximo horario.")
        sys.exit(0)
