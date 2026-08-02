"""Resumen semanal: la prueba de que el bot sigue vivo.

El bot calla cuando no hay ofertas, que es lo correcto, pero tiene un efecto
feo: tres semanas de silencio son exactamente iguales tanto si no hay nada
como si el bot lleva tres semanas roto. Y un bot en el que no confías deja
de servir para nada.

Una vez por semana manda el estado: precio actual de los planes que sigues y
el mínimo que hemos visto. Si ese mensaje no llega, algo pasa.
"""
import io
import os
import json
import datetime

import historial
import telegram_notify

ARCHIVO = "state/latido.json"


def _leer(ruta=ARCHIVO):
    try:
        with io.open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def _escribir(datos, ruta=ARCHIVO):
    carpeta = os.path.dirname(ruta)
    if carpeta:
        try:
            os.makedirs(carpeta)
        except OSError:
            pass
    with io.open(ruta, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False, indent=1))


def toca(cfg, ultimo, hoy=None, hora=None):
    """¿Toca mandar resumen?

    Dos cerrojos, no uno. El primero (¿ya se mandó hoy?) depende de que el
    estado sobreviva entre ejecuciones, y eso falló de verdad: el workflow no
    guardaba latido.json, así que cada pasada creía ser la primera y llegaban
    48 resúmenes el mismo domingo.

    El segundo cerrojo es la hora: aunque el estado se vuelva a perder, fuera
    de su hora no manda nada. Un fallo así vuelve a ser molesto, pero ya no
    puede inundarte el chat.
    """
    if not cfg.get("activo", True):
        return False
    hoy = hoy or datetime.date.today()
    if hoy.weekday() != cfg.get("dia", 6):        # 0=lunes ... 6=domingo
        return False
    hora = datetime.datetime.utcnow().hour if hora is None else hora
    if hora != cfg.get("hora_utc", 14):           # 14 UTC = 9 a.m. en Colombia
        return False
    return ultimo != hoy.isoformat()


def componer(datos, planes_seguidos, ventana_meses=6, hoy=None):
    """Arma el texto del resumen a partir del historial."""
    lineas = ["🩺 <b>Resumen semanal de PS Plus</b>",
              "<i>Si recibes esto, el bot está funcionando.</i>", ""]
    hay = False

    for etiqueta, k in planes_seguidos:
        ultimo = historial.ultimo(datos, k)
        if not ultimo:
            continue
        hay = True
        precio = ultimo.get("p")
        base = ultimo.get("b")
        texto = "• <b>%s</b>: %.2f" % (etiqueta, precio)
        if base is not None and precio is not None and precio < base:
            texto += " <b>(rebajado desde %.2f)</b>" % base
        minimo = historial.minimo(datos, k, dias=ventana_meses * 30, hoy=hoy)
        if minimo is not None and precio is not None and minimo < precio:
            texto += "\n   mínimo visto: %.2f" % minimo
        lineas.append(texto)

    if not hay:
        lineas.append("Todavía no tengo historial de precios. "
                      "En unos días esto tendrá datos.")
    return "\n".join(lineas)


def quizas_enviar(config, datos=None, hoy=None, hora=None):
    """Manda el resumen si toca. Devuelve True si lo mandó."""
    cfg = config.get("latido", {}) or {}
    estado = _leer()
    hoy = hoy or datetime.date.today()
    if not toca(cfg, estado.get("ultimo"), hoy, hora):
        return False

    datos = historial.cargar() if datos is None else datos
    regiones = (config.get("store", {}) or {}).get("regiones", ["en-us"])
    region = regiones[0] if regiones else "en-us"

    seguidos = []
    for plan in cfg.get("planes", ["Essential", "Extra", "Premium"]):
        for meses in cfg.get("duraciones", [12]):
            seguidos.append(("%s %d meses" % (plan, meses),
                             historial.clave(region, plan, meses)))

    texto = componer(datos, seguidos,
                     (config.get("store", {}) or {}).get("meses_comparar", 6),
                     hoy=hoy)
    telegram_notify.enviar_texto_suelto(texto)

    estado["ultimo"] = hoy.isoformat()
    _escribir(estado)
    return True
