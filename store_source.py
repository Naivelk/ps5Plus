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

LIMITACIÓN CONOCIDA: la región no se puede forzar por URL. Pedir `es-co`
devuelve exactamente los mismos bytes y precios en USD que `en-us`: PS Store
decide la región por geolocalización de la IP. Como GitHub Actions corre en
EE.UU., aquí siempre verás precios de EE.UU. Los precios en pesos llegan por
los feeds de noticias colombianas, no por aquí.
"""
import re
import requests

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


def _planes(html):
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


def obtener(config):
    """Devuelve (items, errores). Solo reporta rebajas o precios interesantes."""
    cfg = config.get("store", {}) or {}
    if not cfg.get("activo", True):
        return [], []
    regiones = cfg.get("regiones", ["en-us"])
    umbrales = config.get("precio_objetivo", {})

    resultados, errores = [], []
    for region in regiones:
        try:
            r = requests.get(URL % (region, PRODUCTO),
                             headers={"User-Agent": NAVEGADOR}, timeout=30)
            r.raise_for_status()
        except Exception as ex:
            errores.append("PS Store (%s): %s" % (region, ex))
            continue

        filas = _planes(r.text)
        if not filas:
            errores.append("PS Store (%s): no se encontraron precios "
                           "(¿cambió la página?)" % region)
            continue

        for f in filas:
            rebajado = f["precio"] < f["base"]
            limite = umbrales.get(f["moneda"])
            interesa = rebajado or (limite is not None and f["meses"] == 12
                                    and f["precio"] <= limite)
            # Sin esto avisaría de los 9 planes cada media hora para siempre.
            if not interesa:
                continue

            if rebajado:
                ahorro = 100 - (f["precio"] * 100.0 / f["base"])
                extra = " (antes %.2f, -%.0f%%)" % (f["base"], ahorro)
            else:
                extra = ""
            titulo = "PS Plus %s %d meses — %.2f %s%s" % (
                f["plan"], f["meses"], f["precio"], f["moneda"], extra)

            resultados.append({
                # El precio va en el id: si cambia, es un aviso nuevo; si no,
                # ya está en "vistos" y no te repite lo mismo cada media hora.
                "id": "store:%s:%s:%d:%.2f" % (region, f["plan"], f["meses"],
                                               f["precio"]),
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
                "categoria": "oferta",
            })

    return resultados, errores
