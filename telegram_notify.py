"""Envía a Telegram lo que encontró el bot, agrupado por urgencia."""
import os
import html
import requests

# Secciones del resumen, en orden. Lo urgente arriba: los códigos gratis
# duran minutos, las ofertas duran días.
SECCIONES = [
    ("codigo", "🎁 <b>Códigos gratis / sorteos</b>"),
    ("chollo", "🔥 <b>Ofertas bajo tu precio objetivo</b>"),
    ("oferta", "💲 <b>Otras ofertas</b>"),
]


def _escapar(t):
    return html.escape(t or "")


def _bucket(it):
    if it.get("categoria") == "codigo":
        return "codigo"
    if it.get("chollo"):
        return "chollo"
    return "oferta"


def _precio_txt(it):
    if it.get("precio") is None:
        return ""
    if it["moneda"] == "COP":
        return f"${it['precio']:,.0f} COP".replace(",", ".")
    return f"US${it['precio']:,.2f}"


def _bloque(it, n, mi_region):
    """Un item compacto; el título es enlace clicable."""
    emoji = it["etiqueta"].split()[0]              # 🟢 / 🟡 / 🔴
    url = _escapar(it["url"])
    linea2 = [f"<i>{_escapar(it['fuente'])}</i>"]

    precio = _precio_txt(it)
    if precio:
        linea2.append(f"<b>{_escapar(precio)}</b>")

    region = it.get("region")
    if region and mi_region and region != mi_region:
        linea2.append(f"⚠️ región {_escapar(region)} — no sirve en tu cuenta")
    elif region:
        linea2.append(f"🌍 {_escapar(region)}")

    bloque = (
        f"{n}. {emoji} <a href=\"{url}\"><b>{_escapar(it['titulo'])}</b></a>\n"
        f"   " + " · ".join(linea2)
    )
    if it.get("motivos"):
        bloque += "\n   ⚠️ " + _escapar(", ".join(it["motivos"]))
    return bloque


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


def enviar_resumen(items, mi_region=None, nota=None, avisar_vacio=False):
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

    grupos = {"codigo": [], "chollo": [], "oferta": []}
    for it in items:
        grupos[_bucket(it)].append(it)

    partes = [f"🎮 <b>PS Plus</b> — {len(items)} novedades"]
    n = 1
    for clave, titulo_sec in SECCIONES:
        grupo = grupos[clave]
        if not grupo:
            continue
        partes.append(f"{titulo_sec}  ({len(grupo)})")
        for it in grupo:
            partes.append(_bloque(it, n, mi_region))
            n += 1

    if grupos["codigo"]:
        partes.append("🔒 <i>Recuerda: canjea a mano en playstation.com. "
                      "Nadie legítimo te pide contraseña ni tarjeta por un "
                      "código gratis.</i>")

    _enviar_en_trozos(base, chat_id, partes)
