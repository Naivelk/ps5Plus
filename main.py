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
import code_filter
import telegram_notify

ARCHIVO_VISTOS = "state/seen.json"
MAX_VISTOS = 3000          # el bot corre muchas veces al día; no dejamos crecer sin fin


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


def cargar_vistos():
    try:
        with open(ARCHIVO_VISTOS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def guardar_vistos(vistos):
    """Guarda como lista para conservar el orden y poder podar los más viejos."""
    os.makedirs("state", exist_ok=True)
    with open(ARCHIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(vistos[-MAX_VISTOS:], f, ensure_ascii=False, indent=2)


def _clave_titulo(titulo):
    """Normaliza un título para detectar la misma oferta en fuentes distintas."""
    t = titulo.lower()
    t = re.sub(r"[^a-z0-9áéíóúñ ]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60]


def main():
    cargar_env_local()
    config = cargar_config()
    vistos = cargar_vistos()
    ya_visto = set(vistos)

    # 1) Recolectar de todas las fuentes (cada una reporta sus errores).
    rd_items, rd_err = reddit_source.obtener(config)
    rss_items, rss_err = rss_source.obtener(config)
    items = rd_items + rss_items
    errores = rd_err + rss_err
    print(f"Encontrados {len(items)} elementos en total.")
    if errores:
        print("Errores técnicos:", errores)

    palabras = config.get("palabras_psplus", [])
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
    mi_region = config.get("mi_region")
    max_dias = config.get("max_dias_antiguedad", 5)
    max_envio = config.get("max_por_resumen", 30)
    ocultar_riesgo = config.get("ocultar_sospechosos", False)

    limite_fecha = (datetime.datetime.now(datetime.timezone.utc)
                    .replace(tzinfo=None) - datetime.timedelta(days=max_dias))

    # 2) Filtrar y enriquecer: precio, región, categoría, riesgo.
    nuevos = []
    for it in items:
        if it["id"] in ya_visto:
            continue
        if not code_filter.es_relevante(it, palabras, suscripcion, senales_codigo):
            continue
        if code_filter.esta_excluido(it, excluir, excluir_tit):
            continue
        fecha = it.get("fecha_dt")
        if fecha and fecha < limite_fecha:
            continue

        it["categoria"] = code_filter.categoria(it, senales_codigo)
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
    nuevos.sort(key=lambda x: (
        x["categoria"] != "codigo",
        not x["chollo"],
        bool(mi_region) and x.get("region") not in (None, mi_region),
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
    telegram_notify.enviar_resumen(a_enviar, mi_region=mi_region, nota=nota)
    for it in a_enviar:
        vistos.append(it["id"])
    guardar_vistos(vistos)
    print("Listo.")


if __name__ == "__main__":
    main()
