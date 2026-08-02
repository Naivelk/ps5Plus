"""Cazador de ofertas y códigos de PS Plus -> te avisa por Telegram.

Corre solo (GitHub Actions o tu PC). NO canjea nada ni entra a tu cuenta PSN:
solo te avisa para que TÚ redimas a mano. Canjear con bot te expone a captcha,
detección antibot y a que Sony te bloquee la cuenta.
"""
import json
import os
import re
import html
import datetime
import yaml

import reddit_source
import rss_source
import store_source
import itad_source
import code_filter
import telegram_notify
import latido

ARCHIVO_VISTOS = "state/seen.json"
MAX_VISTOS = 5000          # el bot corre muchas veces al día; no dejamos crecer sin fin


def cargar_env_local():
    """Si existe un archivo .env (solo para probar en tu PC), lo carga."""
    if not os.path.exists(".env"):
        return
    with open(".env", "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip())


def cargar_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_vistos(ruta=ARCHIVO_VISTOS):
    """Devuelve {id: 'YYYY-MM-DD'}.

    Acepta también el formato viejo (lista de ids) para no perder lo ya
    avisado al actualizar: se le pone la fecha de hoy.
    """
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    if isinstance(datos, list):
        hoy = datetime.date.today().isoformat()
        return dict((i, hoy) for i in datos)
    return datos if isinstance(datos, dict) else {}


def podar_vistos(vistos, dias, hoy=None):
    """Olvida lo avisado hace mucho.

    Sin esto, un id quedaba recordado para siempre. Para las noticias da
    igual (cada URL es única), pero era lo que hacía que una oferta repetida
    meses después no volviera a avisar nunca.
    """
    hoy = hoy or datetime.date.today()
    corte = (hoy - datetime.timedelta(days=dias)).isoformat()
    return dict((k, v) for k, v in vistos.items() if v >= corte)


def guardar_vistos(vistos, ruta=ARCHIVO_VISTOS):
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    # Si aun así se desmadra, nos quedamos con lo más reciente.
    if len(vistos) > MAX_VISTOS:
        recientes = sorted(vistos.items(), key=lambda kv: kv[1])[-MAX_VISTOS:]
        vistos = dict(recientes)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(vistos, f, ensure_ascii=False, indent=1, sort_keys=True)


def _clave_titulo(titulo):
    """Normaliza un título para detectar la misma oferta en fuentes distintas."""
    t = titulo.lower()
    t = re.sub(r"[^a-z0-9áéíóúñ ]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60]


def main():
    cargar_env_local()
    config = cargar_config()
    vistos = podar_vistos(cargar_vistos(), config.get("dias_recordar", 45))
    hoy = datetime.date.today().isoformat()

    # 1) Recolectar de todas las fuentes (cada una reporta sus errores).
    rd_items, rd_err = reddit_source.obtener(config)
    of_items, of_err = reddit_source.obtener_ofertas(config)
    rss_items, rss_err = rss_source.obtener(config)
    st_items, st_err = store_source.obtener(config)
    it_items, it_err = itad_source.obtener(config)
    items = st_items + it_items + rd_items + rss_items + of_items
    errores = rd_err + rss_err + st_err + of_err + it_err
    print(f"Encontrados {len(items)} elementos en total.")
    if errores:
        print("Errores técnicos:", errores)

    palabras = config.get("palabras_psplus", [])
    palabras_tarjeta = config.get("palabras_tarjeta_psn", [])
    suscripcion = config.get("palabras_suscripcion", [])
    excluir = config.get("palabras_excluir", [])
    excluir_tit = config.get("palabras_excluir_titulo", [])
    senales_codigo = config.get("senales_codigo_gratis", [])
    senales_estafa = config.get("senales_estafa", [])
    senales_region = config.get("senales_region", {})
    confiables = config.get("dominios_confiables", [])
    acortadores = config.get("acortadores", [])
    umbrales = config.get("precio_objetivo", {})
    pisos = config.get("precio_piso", {})
    # Acepta el ajuste viejo (una sola región) por si queda algún config sin
    # migrar; lo normal ahora es la lista, porque se pueden tener dos cuentas.
    mis_regiones = config.get("mis_regiones")
    if not mis_regiones:
        una = config.get("mi_region")
        mis_regiones = [una] if una else []
    max_dias = config.get("max_dias_antiguedad", 5)
    max_envio = config.get("max_por_resumen", 30)
    ocultar_riesgo = config.get("ocultar_sospechosos", False)
    min_descuento = (config.get("ofertas_juegos", {}) or {}).get(
        "descuento_minimo", 75)
    seguimiento = config.get("seguimiento", [])

    limite_fecha = (datetime.datetime.now(datetime.timezone.utc)
                    .replace(tzinfo=None) - datetime.timedelta(days=max_dias))

    # 2) Filtrar y enriquecer: precio, región, categoría, riesgo.
    nuevos = []
    for it in items:
        # El PS Store ya entrega plan, duración y precio exactos, y quien
        # decide si avisar es el historial de precios. No pasa ni por los
        # filtros de texto ni por "vistos": meterlo en vistos era justo lo
        # que impedía volver a avisar de una oferta repetida.
        if it.get("directo"):
            it["chollo"] = code_filter.bajo_umbral(it["precio"], it["moneda"],
                                                   umbrales, pisos)
            it["nivel"] = "ok"
            it["etiqueta"] = code_filter.ETIQUETA["ok"]
            it["motivos"] = []
            nuevos.append(it)
            continue

        if it["id"] in vistos:
            continue

        # ITAD llega ya estructurado desde una API: título, precio, descuento
        # y si es mínimo histórico. Pasarlo por los filtros pensados para
        # titulares de prensa solo lo estropearía. Sí pasa el anti-estafa,
        # que mira el dominio de destino.
        if it.get("directo_texto"):
            if code_filter.es_seguido(it, seguimiento):
                it["categoria"] = "seguimiento"
            nivel, motivos = code_filter.evaluar(it, senales_estafa, confiables,
                                                 acortadores)
            it["nivel"] = nivel
            it["etiqueta"] = code_filter.ETIQUETA[nivel]
            it["motivos"] = motivos
            if ocultar_riesgo and nivel == "riesgo":
                continue
            nuevos.append(it)
            continue

        # Novedades de los subs de ofertas: no hablan de PS Plus, así que no
        # pasan por el filtro de relevancia. Solo entran si el descuento
        # supera tu umbral; si no, esto sería una manguera de decenas al día.
        if it.get("solo_oferton"):
            pct = code_filter.extraer_descuento(it["titulo"])
            # Lo que sigues entra aunque el descuento sea flojo: un -20% en
            # GTA VI te interesa más que un -85% de un juego cualquiera.
            seguido = code_filter.es_seguido(it, seguimiento)
            if not seguido and (pct is None or pct < min_descuento):
                continue
            it["categoria"] = "seguimiento" if seguido else "oferton"
            it["descuento"] = pct
            it["precio"], it["moneda"] = code_filter.extraer_precio(it["titulo"])
            it["chollo"] = False
            it["region"] = None
            nivel, motivos = code_filter.evaluar(it, senales_estafa, confiables,
                                                 acortadores)
            it["nivel"] = nivel
            it["etiqueta"] = code_filter.ETIQUETA[nivel]
            it["motivos"] = motivos
            if ocultar_riesgo and nivel == "riesgo":
                continue
            nuevos.append(it)
            continue

        es_tarjeta = code_filter.es_tarjeta_psn(it, palabras_tarjeta)
        if not es_tarjeta and not code_filter.es_relevante(
                it, palabras, suscripcion, senales_codigo):
            continue
        if code_filter.esta_excluido(it, excluir, excluir_tit):
            continue
        # Preguntas y quejas de usuarios: llegaban al chat cosas como "is
        # there anyway to get free ps plus" o "i cancelled my ps plus after a
        # failed payment". Si trae precio, no se descarta.
        precio_titulo, _ = code_filter.extraer_precio(it["titulo"])
        if precio_titulo is None and code_filter.es_pregunta_o_queja(it):
            continue
        fecha = it.get("fecha_dt")
        if fecha and fecha < limite_fecha:
            continue

        it["categoria"] = ("tarjeta" if es_tarjeta
                           else code_filter.categoria(it, senales_codigo))
        precio, moneda = code_filter.extraer_precio(it["titulo"])
        it["precio"], it["moneda"] = precio, moneda
        it["chollo"] = code_filter.bajo_umbral(precio, moneda, umbrales, pisos)
        it["region"] = code_filter.detectar_region(it, moneda, senales_region)

        nivel, motivos = code_filter.evaluar(it, senales_estafa, confiables,
                                             acortadores)
        it["nivel"] = nivel
        it["etiqueta"] = code_filter.ETIQUETA[nivel]
        it["motivos"] = motivos

        if ocultar_riesgo and nivel == "riesgo":
            continue
        nuevos.append(it)

    print(f"Novedades relevantes: {len(nuevos)}")

    # 3) Ordenar: códigos gratis primero (vuelan), luego chollos, luego el resto.
    #    Dentro de cada grupo, lo de tu región antes que lo que no puedes usar.
    orden_nivel = {"ok": 0, "duda": 1, "riesgo": 2}
    orden_cat = {"seguimiento": 0, "codigo": 1, "tarjeta": 2, "oferton": 4}
    nuevos.sort(key=lambda x: (
        orden_cat.get(x["categoria"], 2),
        not x["chollo"],
        bool(mis_regiones) and x.get("region") not in [None] + list(mis_regiones),
        orden_nivel.get(x["nivel"], 3),
    ))

    # 3b) Quitar la misma oferta repetida entre subs (mismo título normalizado).
    vistos_titulo = set()
    unicos = []
    for it in nuevos:
        clave = _clave_titulo(it["titulo"])
        if clave in vistos_titulo:
            continue
        vistos_titulo.add(clave)
        unicos.append(it)
    a_enviar = unicos[:max_envio]

    # 4) Si TODO salió vacío por errores, avisar del fallo (si no, callar).
    nota = None
    if errores and not a_enviar:
        detalle = "\n• ".join(html.escape(e) for e in errores)
        nota = "⚠️ <b>Aviso técnico:</b> el bot tuvo problemas:\n• " + detalle

    # 5) Avisar y recordar lo enviado.
    telegram_notify.enviar_resumen(a_enviar, mis_regiones=mis_regiones, nota=nota)
    for it in a_enviar:
        if not it.get("directo"):
            vistos[it["id"]] = hoy
    guardar_vistos(vistos)

    # 6) Latido semanal: sin esto, tres semanas sin ofertas y sin mensajes
    #    son indistinguibles de un bot roto.
    latido.quizas_enviar(config)
    print("Listo.")


if __name__ == "__main__":
    main()
