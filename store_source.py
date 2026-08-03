"""Precios oficiales de PS Plus, directo del PlayStation Store.

Esta es la única tienda que se puede consultar de verdad. Comprobado:
  - G2A         -> 403
  - Kinguin     -> Cloudflare
  - Slickdeals  -> 403 tras Cloudflare
  - Eneba       -> responde 200 pero la página se arma en JavaScript; el HTML
                   llega sin productos ni precios.
  - PS Store    -> 200 y trae los precios en el HTML. Este.

La página del producto incluye las NUEVE combinaciones (Essential/Extra/
Premium x 1/3/12 meses) con `basePriceValue` y `discountedValue` en centavos
como enteros, así que no hay que parsear "US$15.99" ni adivinar decimales.

Cuándo avisa: cuando el precio BAJA respecto a la última vez que lo miramos
(ver historial.py). No cuando está por debajo de un número fijo, porque eso
o te repetía el mismo aviso cada media hora o se callaba para siempre.

LIMITACIÓN CONOCIDA: la región no se puede forzar por URL. Pedir `es-co`
devuelve exactamente los mismos bytes y precios en USD que `en-us`: PS Store
decide la región por geolocalización de la IP. Como GitHub Actions corre en
EE.UU., aquí siempre verás precios de EE.UU. Los precios en pesos llegan por
los feeds de noticias colombianas, no por aquí.
"""
import re
import requests

import historial

# Producto de las suscripciones de PS Plus. Una sola página trae los 9 planes.
PRODUCTO = "IP9101-PPSA06916_00-PLUS1T01M0000000"
URL = "https://store.playstation.com/%s/product/%s"

NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

PLANES = {"1": "Essential", "2": "Extra", "3": "Premium"}

# Del skuId (...PLUS2T12M...) sale el plan y la duración; justo después viene
# su bloque "price". Los 120 caracteres de holgura son el campo "duration".
RE_BLOQUE = re.compile(
    r'PLUS(?P<plan>\d)T(?P<meses>\d+)M[^"]*"'
    r'.{0,120}?'
    r'"price":\{(?P<precio>[^}]*)\}',
    re.S)


def _campo(bloque, nombre):
    m = re.search(r'"%s":(?:"([^"]*)"|([-\d.]+|null|true|false))' % nombre,
                  bloque)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def planes(html):
    """Devuelve las combinaciones plan/duración con su precio."""
    filas, vistos = [], set()
    for m in RE_BLOQUE.finditer(html):
        plan = PLANES.get(m.group("plan"))
        meses = int(m.group("meses"))
        if not plan or (plan, meses) in vistos:
            continue
        bloque = m.group("precio")
        base = _campo(bloque, "basePriceValue")
        ahora = _campo(bloque, "discountedValue")
        if base is None or ahora is None:
            continue
        vistos.add((plan, meses))
        filas.append({
            "plan": plan,
            "meses": meses,
            "base": int(base) / 100.0,
            "precio": int(ahora) / 100.0,
            "moneda": _campo(bloque, "currencyCode") or "USD",
            "texto": _campo(bloque, "discountText") or "",
        })
    return filas


def _descargar(region):
    r = requests.get(URL % (region, PRODUCTO),
                     headers={"User-Agent": NAVEGADOR}, timeout=30)
    r.raise_for_status()
    return r.text


def evaluar_fila(f, previo, umbrales):
    """Decide si esta fila merece aviso y por qué. Devuelve (avisar, motivo).

    Separado de la descarga para poder probarlo sin red.
    """
    rebaja_activa = f["precio"] < f["base"]
    limite = umbrales.get(f["moneda"])
    bajo_umbral = (limite is not None and f["meses"] == 12
                   and f["precio"] <= limite)

    if previo is None:
        # Primera vez que vemos este plan: no hay con qué comparar, así que
        # solo avisamos si el propio Store dice que está rebajado o si es un
        # chollo según tu objetivo. Si no, lo anotamos en silencio.
        if rebaja_activa:
            return True, "rebaja"
        if bajo_umbral:
            return True, "umbral"
        return False, ""

    if f["precio"] < previo.get("p", f["precio"]):
        return True, "bajada"
    return False, ""


def obtener(config, hist=None):
    """Devuelve (items, errores). Solo avisa de bajadas reales de precio."""
    cfg = config.get("store", {}) or {}
    if not cfg.get("activo", True):
        return [], []
    regiones = cfg.get("regiones", ["en-us"])
    umbrales = config.get("precio_objetivo", {})
    ventana = cfg.get("meses_comparar", 6)

    datos = historial.cargar() if hist is None else hist
    resultados, errores = [], []

    for region in regiones:
        try:
            html = _descargar(region)
        except Exception as ex:
            errores.append("PS Store (%s): %s" % (region, ex))
            continue

        filas = planes(html)
        if not filas:
            errores.append("PS Store (%s): no se encontraron precios "
                           "(¿cambió la página?)" % region)
            continue

        for f in filas:
            k = historial.clave(region, f["plan"], f["meses"])
            previo = historial.ultimo(datos, k)
            avisar, motivo = evaluar_fila(f, previo, umbrales)

            # Se registra SIEMPRE, se avise o no: el historial tiene que
            # reflejar la realidad, no solo lo que te contamos.
            minimo_antes = historial.minimo(datos, k, dias=ventana * 30)
            historial.registrar(datos, k, f["precio"], f["base"])

            if not avisar:
                continue

            detalle = []
            if f["precio"] < f["base"]:
                ahorro = 100 - (f["precio"] * 100.0 / f["base"])
                detalle.append("antes %.2f, -%.0f%%" % (f["base"], ahorro))
            if previo and previo.get("p") is not None:
                detalle.append("estaba a %.2f" % previo["p"])
            if minimo_antes is not None and f["precio"] < minimo_antes:
                meses = historial.meses_de_historia(datos, k)
                if meses >= 2:
                    detalle.append("¡el más bajo en %d meses!" % min(meses, ventana))

            titulo = "PS Plus %s %d meses — %.2f %s" % (
                f["plan"], f["meses"], f["precio"], f["moneda"])
            if detalle:
                titulo += " (" + "; ".join(detalle) + ")"

            resultados.append({
                # Sin precio en el id: la decisión de avisar ya la tomó el
                # historial, y meterlo aquí es lo que causaba que una oferta
                # repetida no volviera a avisar nunca.
                "id": "store:%s:%s" % (k, motivo),
                "titulo": titulo,
                "descripcion": f["texto"],
                "url": URL % (region, PRODUCTO),
                "fuente": "PlayStation Store (%s)" % region,
                "autor": "",
                "fecha_dt": None,
                "imagen": None,
                "region": region.split("-")[-1].upper(),
                # Ya viene estructurado: no pasa por los filtros de texto.
                "directo": True,
                "precio": f["precio"],
                "moneda": f["moneda"],
                "precio_antes": f["base"] if f["base"] > f["precio"] else None,
                "descuento": (int(round(100 - f["precio"] * 100.0 / f["base"]))
                              if f["base"] > f["precio"] else None),
                "minimo_historico": (minimo_antes is not None
                                     and f["precio"] < minimo_antes),
                "categoria": "oferta",
            })

    if hist is None:
        historial.guardar(datos)
    return resultados, errores
