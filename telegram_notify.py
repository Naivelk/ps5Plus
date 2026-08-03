"""Envía a Telegram lo que encontró el bot, agrupado por urgencia."""
import os
import html
import requests

# Secciones del resumen, en orden. Lo urgente arriba: los códigos gratis
# duran minutos, las ofertas duran días.
SECCIONES = [
    ("seguimiento", "🎯 <b>LO QUE SIGUES</b>"),
    ("codigo", "🎁 <b>CÓDIGOS GRATIS</b>"),
    ("tarjeta", "💳 <b>SALDO PSN CON DESCUENTO</b>"),
    ("chollo", "🔥 <b>BAJO TU PRECIO OBJETIVO</b>"),
    ("oferta", "💲 <b>OTRAS OFERTAS</b>"),
    ("oferton", "🎮 <b>OFERTONES DE JUEGOS</b>"),
]


def _escapar(t):
    return html.escape(t or "")


def _bucket(it):
    cat = it.get("categoria")
    if cat in ("seguimiento", "codigo", "tarjeta", "oferton"):
        return cat
    if it.get("chollo"):
        return "chollo"
    return "oferta"


RAYA = "─" * 22


def _importe(valor, moneda):
    """Formatea un importe según su moneda."""
    if valor is None:
        return ""
    if moneda == "COP":
        return f"${valor:,.0f}".replace(",", ".")
    return f"{valor:,.2f} US$"


def _precio_txt(it):
    return _importe(it.get("precio"), it.get("moneda"))


def barra(pct, ancho=10):
    """Barra de descuento. Un −80% se ve de un vistazo; un '80%' hay que leerlo.

    Se rellena en proporción al descuento, así que cuanto más llena, mejor
    la oferta.
    """
    if not pct:
        return ""
    llenos = max(1, min(ancho, int(round(pct * ancho / 100.0))))
    return "█" * llenos + "░" * (ancho - llenos)


def _bloque(it, n, mis_regiones):
    """Un item con jerarquía visual: primero qué es, luego cuánto y por qué.

    El orden importa. Antes el emoji de confianza abría la línea y el precio
    quedaba enterrado entre metadatos; ahora manda el precio, que es lo que
    hace decidir.
    """
    url = _escapar(it["url"])
    titulo = _escapar(it["titulo"])
    lineas = [f'<a href="{url}"><b>{titulo}</b></a>']

    # --- línea del dinero -------------------------------------------------
    precio = _precio_txt(it)
    antes = _importe(it.get("precio_antes"), it.get("moneda"))
    dinero = []
    if precio:
        dinero.append(f"<b>{_escapar(precio)}</b>")
    if antes and it.get("precio_antes") != it.get("precio"):
        dinero.append(f"<s>{_escapar(antes)}</s>")
    pct = it.get("descuento")
    if pct:
        dinero.append(f"<b>−{int(pct)}%</b>")
    if dinero:
        lineas.append("   " + "  ".join(dinero))

    # --- barra + ahorro en dinero ----------------------------------------
    # El porcentaje solo no dice mucho: "−80%" impresiona más cuando al lado
    # pone cuántos dólares te ahorras de verdad.
    visual = barra(pct)
    if visual:
        trozo = "   " + visual
        if it.get("precio_antes") and it.get("precio") is not None:
            ahorro = it["precio_antes"] - it["precio"]
            if ahorro > 0:
                trozo += "  ahorras " + _escapar(_importe(ahorro, it["moneda"]))
        lineas.append(trozo)

    # --- por qué merece la pena ------------------------------------------
    notas = []
    if it.get("anomalo"):
        # Va el primero: si de verdad es un error de precio, dura minutos.
        notas.append("⚡ <b>puede ser un error de precio</b>")
    if it.get("minimo_historico"):
        notas.append("🏆 mínimo histórico")
    if it.get("nota"):
        estrella = f"⭐ {int(it['nota'])}"
        if it.get("resenas"):
            estrella += f" ({it['resenas']:,} reseñas)".replace(",", ".")
        notas.append(estrella)
    if it.get("cashback"):
        notas.append(f"💸 {int(it['cashback'])}% cashback")
    if it.get("cupon"):
        notas.append(f"🎟 cupón <code>{_escapar(it['cupon'])}</code>")
    if notas:
        lineas.append("   " + " · ".join(notas))

    # --- región y avisos --------------------------------------------------
    region = it.get("region")
    if region and mis_regiones and region not in mis_regiones:
        lineas.append(f"   ⚠️ región {_escapar(region)} — no sirve en tus cuentas")
    elif region:
        # Con varias cuentas lo útil no es avisar de un problema, sino
        # recordarte en cuál de las dos hay que canjearlo.
        lineas.append(f"   🌍 canjéalo en tu cuenta {_escapar(region)}")

    # El semáforo solo aparece cuando hay algo que mirar: un 🟢 en cada línea
    # es ruido, y de tanto verlo se deja de leer.
    if it.get("nivel") in ("duda", "riesgo"):
        emoji = it["etiqueta"].split()[0]
        aviso = f"   {emoji} {_escapar(it['etiqueta'].split(' ', 1)[1])}"
        if it.get("motivos"):
            aviso += ": " + _escapar(", ".join(it["motivos"][:3]))
        lineas.append(aviso)

    lineas.append(f"   <i>{_escapar(it['fuente'])}</i>")
    return "\n".join(lineas)


def _enviar_texto(base, chat_id, texto):
    try:
        r = requests.post(f"{base}/sendMessage", data={
            "chat_id": chat_id, "text": texto, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[Telegram] Error enviando mensaje: {e}")


def _enviar_en_trozos(base, chat_id, partes):
    """Une las partes en mensajes de <4000 chars (límite de Telegram)."""
    trozo = ""
    for p in partes:
        add = ("\n\n" if trozo else "") + p
        if len(trozo) + len(add) > 3900:
            _enviar_texto(base, chat_id, trozo)
            trozo = p
        else:
            trozo += add
    if trozo.strip():
        _enviar_texto(base, chat_id, trozo)


def enviar_texto_suelto(texto):
    """Manda un mensaje ya compuesto (lo usa el resumen semanal)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[Telegram] Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return
    _enviar_texto("https://api.telegram.org/bot%s" % token, chat_id, texto)


def enviar_resumen(items, mis_regiones=None, nota=None, avisar_vacio=False):
    """Manda el resumen agrupado por secciones. `nota` = aviso técnico.

    avisar_vacio=False porque este bot corre muchas veces al día: no queremos
    un "no hay nada" cada 30 minutos.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[Telegram] Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return
    base = f"https://api.telegram.org/bot{token}"

    if nota:
        _enviar_texto(base, chat_id, nota)

    if not items:
        if avisar_vacio and not nota:
            _enviar_texto(base, chat_id, "🎮 Sin novedades de PS Plus por ahora.")
        return

    grupos = dict((clave, []) for clave, _ in SECCIONES)
    for it in items:
        grupos[_bucket(it)].append(it)

    # La cabecera dice de un vistazo si merece la pena abrir el mensaje: el
    # mejor descuento de la tanda, no solo cuántas cosas hay.
    mejor = max((it.get("descuento") or 0) for it in items)
    cabecera = f"🎮 <b>PS PLUS</b> · {len(items)} novedades"
    if mejor:
        cabecera += f" · hasta <b>−{int(mejor)}%</b>"
    partes = [cabecera]

    n = 1
    for clave, titulo_sec in SECCIONES:
        grupo = grupos[clave]
        if not grupo:
            continue
        partes.append(f"{titulo_sec} · {len(grupo)}\n{RAYA}")
        for it in grupo:
            partes.append(_bloque(it, n, mis_regiones))
            n += 1

    if grupos["codigo"]:
        partes.append("🔒 <i>Recuerda: canjea a mano en playstation.com. "
                      "Nadie legítimo te pide contraseña ni tarjeta por un "
                      "código gratis.</i>")

    _enviar_en_trozos(base, chat_id, partes)
