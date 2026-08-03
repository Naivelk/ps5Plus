# -*- coding: utf-8 -*-
"""Vigila precios en Eneba usando un navegador de verdad.

Va SEPARADO del bot principal y en su propio workflow, a propósito: es la
pieza más frágil de todo esto y no debe poder tumbar los avisos de PS Store,
Reddit y noticias, que sí son estables.

Por qué hace falta un navegador: la web de Eneba responde 200 a curl, pero el
HTML que devuelve NO contiene los precios — la página los pinta con
JavaScript. Comprobado mirando las peticiones del navegador: el precio
"Desde: 93,43 US$" existe en pantalla y no aparece por ningún lado en el HTML
que baja curl. De ahí Playwright.

La extracción va por TEXTO, no por selectores CSS: se busca el patrón
"Desde: <precio> <moneda>" en el texto visible. Las clases de Eneba cambian
con cada despliegue suyo; ese texto lleva años igual.
"""
import io
import os
import re
import sys
import json
import datetime

import telegram_notify

ARCHIVO = "state/eneba.json"

# "Desde: 93,43 US$" — aparece en tarjetas y recargas.
RE_DESDE = re.compile(
    r"Desde:?\s*([\d.,]+)\s*(US\$|USD|COP|EUR|€|\$)", re.IGNORECASE)

# Cualquier importe con moneda. Hace falta porque las páginas de JUEGOS no
# escriben "Desde" por ningún lado: comprobado en la de GTA VI, donde el
# precio bueno (69,22 US$) sale suelto. Buscando solo "Desde" no se leía nada.
#
# OJO CON EL SÍMBOLO: Eneba distingue dos cosas que se parecen mucho.
#   "93,42 US$"  -> un PRECIO (lleva el símbolo $)
#   "100 USD"    -> el VALOR NOMINAL de la tarjeta, o una denominación
# Aceptar "USD" hacía que la tarjeta de 100 se leyera como "cuesta 100.00",
# cuando en realidad cuesta 93,42. Por eso aquí NO entra "USD" a secas.
RE_IMPORTE = re.compile(
    r"([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(US\$|COP|€)")

# Si el producto no está a la venta no hay precio que leer, y cualquier número
# que saquemos de la página será de otra cosa. Pasó con PS Plus Extra 12
# meses: estaba agotado y se leyó un "344.95" que no era su precio.
RE_AGOTADO = re.compile(
    r"agotado|sold\s*out|out\s*of\s*stock|no\s*disponible", re.IGNORECASE)

# Rangos de cordura por moneda, para no confundir un precio con el número de
# valoraciones, un porcentaje de cashback o el año.
LIMITES = {"USD": (1.0, 1000.0), "EUR": (1.0, 1000.0),
           "COP": (3000.0, 5000000.0)}


def _a_numero(bruto):
    """'93,43' -> 93.43 ; '273.250' -> 273250.0

    Eneba escribe en formato latino: el punto separa miles y la coma decimales.
    """
    b = bruto.strip()
    if "," in b:                      # hay decimales: el punto es de miles
        return float(b.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", b):
        return float(b.replace(".", ""))
    return float(b)


def _moneda(simbolo):
    s = (simbolo or "").upper()
    if "COP" in s:
        return "COP"
    if "EUR" in s or "€" in s:
        return "EUR"
    return "USD"


def extraer(texto, nominal=None):
    """Saca (precio, moneda) del texto visible de la página.

    Orden de preferencia:
      1. "Desde: X US$" — lo que usan tarjetas y recargas, y es inequívoco.
      2. El importe más bajo con símbolo de moneda — para las páginas de
         juegos, que no escriben "Desde" y donde varios vendedores ofrecen lo
         mismo: el que interesa es el más barato.

    `nominal` es el valor de cara de una tarjeta (100 para la de 100 USD). Si
    se pasa, se exige que el precio esté entre el 40% y el 150% de esa cifra.
    Sirve de red: en la página de la tarjeta de 100 conviven el precio real
    (93,42) y los de las denominaciones sueltas (0,96 la de 1 USD), y sin este
    filtro el "más barato" sería 0,96.
    """
    texto = texto or ""
    if RE_AGOTADO.search(texto):
        return None, None

    m = RE_DESDE.search(texto)
    if m:
        try:
            valor = _a_numero(m.group(1))
            if _plausible(valor, nominal):
                return valor, _moneda(m.group(2))
        except ValueError:
            pass

    candidatos = []
    for m in RE_IMPORTE.finditer(texto):
        try:
            valor = _a_numero(m.group(1))
        except ValueError:
            continue
        moneda = _moneda(m.group(2))
        bajo, alto = LIMITES.get(moneda, (1.0, 1000.0))
        if bajo <= valor <= alto and _plausible(valor, nominal):
            candidatos.append((valor, moneda))
    if not candidatos:
        return None, None
    return min(candidatos, key=lambda c: c[0])


# Cuánto puede alejarse el precio de una tarjeta de su valor de cara. Las
# tarjetas PSN se venden con un descuento moderado (5-15%), nunca a mitad de
# precio, así que por debajo del 60% lo que hemos leído es otra cosa.
#
# Esto no es teoría: la página de la tarjeta de 50 USD lista los importes
#   54,04 · 46,98 · 0,96 · 1,93 · 2,88 ...
# donde 46,98 es el precio y el resto son la tabla de denominaciones sueltas.
# Con el margen anterior (40%) se colaba un 21,09 y el bot avisaba de una
# "bajada" que no existía.
MARGEN_BAJO = 0.60
MARGEN_ALTO = 1.30


def _plausible(valor, nominal):
    """Descarta precios imposibles para una tarjeta de valor conocido."""
    if not nominal:
        return True
    return nominal * MARGEN_BAJO <= valor <= nominal * MARGEN_ALTO


def comparar_regiones(nombre, lecturas, referencia, ahorro_minimo):
    """¿Merece la pena comprar la versión de otra región?

    `lecturas` es [(region, precio, moneda), ...]. Se compara contra la región
    de referencia (la que ya puedes usar). Devuelve None si no hay nada que
    contar, o un dict con la mejor alternativa.

    Ojo con lo que esto significa en la práctica: una key de otra región
    normalmente exige una cuenta PSN de ESA región. Es una compra distinta,
    no un descuento sin más, y por eso el aviso lo dice.
    """
    validas = [(r, p, m) for r, p, m in lecturas if p is not None]
    if len(validas) < 2:
        return None

    ref = next((x for x in validas if x[0] == referencia), None)
    if ref is None:
        return None
    _, precio_ref, moneda_ref = ref

    # Solo comparamos dentro de la misma moneda: convertir aquí sería inventar.
    alternativas = [x for x in validas
                    if x[0] != referencia and x[2] == moneda_ref]
    if not alternativas:
        return None

    mejor = min(alternativas, key=lambda x: x[1])
    region, precio, moneda = mejor
    if precio >= precio_ref:
        return None
    ahorro = precio_ref - precio
    pct = ahorro * 100.0 / precio_ref
    if pct < ahorro_minimo:
        return None
    return {"nombre": nombre, "region": region, "precio": precio,
            "moneda": moneda, "precio_ref": precio_ref,
            "region_ref": referencia, "ahorro": ahorro, "pct": pct}


def _leer_estado():
    try:
        with io.open(ARCHIVO, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def _guardar_estado(datos):
    carpeta = os.path.dirname(ARCHIVO)
    if carpeta:
        try:
            os.makedirs(carpeta)
        except OSError:
            pass
    with io.open(ARCHIVO, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True))


def decidir(nombre, precio, moneda, previo, objetivo):
    """¿Avisar? Devuelve (avisar, texto_extra).

    Mismo criterio que el PS Store: avisamos si BAJA respecto a la última
    lectura, no si está por debajo de un número fijo. Si no, o repites el
    mismo aviso cada pocas horas o te callas para siempre.
    """
    extra = []
    avisar = False

    if previo is None:
        # Primera lectura: solo molestamos si ya está por debajo del objetivo.
        if objetivo is not None and precio <= objetivo:
            avisar = True
            extra.append("por debajo de tu objetivo (%.2f)" % objetivo)
    else:
        anterior = previo.get("p")
        if anterior is not None and precio < anterior:
            avisar = True
            extra.append("estaba a %.2f" % anterior)
            if objetivo is not None and precio <= objetivo:
                extra.append("bajo tu objetivo")
    return avisar, extra


def _texto_pagina(pagina, url, espera):
    pagina.goto(url, wait_until="networkidle", timeout=60000)
    # El precio se pinta después de hidratar React. Sin esperar, la primera
    # lectura sale VACÍA y parece que Eneba nos ha bloqueado — pasó de verdad
    # al probar la página de GTA VI. Esperamos a que aparezca cualquier
    # importe con moneda, no la palabra "Desde", que en juegos no existe.
    # Se espera al SÍMBOLO de moneda (US$ / € / COP), nunca a "USD" a secas.
    # Esperar a "USD" era inútil: el nombre del producto ya lleva "100 USD"
    # desde el primer instante, así que la espera terminaba de inmediato y se
    # leía la página antes de que el precio existiera. De ahí que la tarjeta
    # de 100 USD se leyera como "cuesta 100.00".
    try:
        pagina.wait_for_function(
            "() => /[\\d][\\d.,]*\\s*(US\\$|COP|\\u20ac)|agotado|sold\\s*out/i"
            ".test(document.body.innerText)",
            timeout=espera * 1000)
    except Exception:
        pass                          # seguimos: quizá cambió el formato
    # Un respiro extra tras la condición: los precios de los distintos
    # vendedores no aparecen todos a la vez. En un mismo run, GTA VI Global se
    # leyó bien en un sitio y salió vacío en otro por llegar demasiado pronto.
    pagina.wait_for_timeout(2500)
    return pagina.inner_text("body")


def main():
    import yaml
    with io.open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    cfg = config.get("eneba", {}) or {}
    if not cfg.get("activo", False):
        print("Eneba desactivado en config.yaml")
        return 0
    productos = cfg.get("productos", []) or []
    if not productos:
        print("No hay productos configurados.")
        return 0

    espera = cfg.get("espera_segundos", 20)
    estado = _leer_estado()
    avisos, errores = [], []
    comparaciones, enlaces = [], {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        contexto = navegador.new_context(
            locale=cfg.get("idioma", "es-419"),
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        pagina = contexto.new_page()

        for prod in productos:
            nombre = prod.get("nombre") or prod.get("url", "")[:50]
            url = prod.get("url")
            if not url:
                continue
            try:
                texto = _texto_pagina(pagina, url, espera)
            except Exception as ex:
                errores.append("%s: %s" % (nombre, str(ex)[:120]))
                print("[Eneba] %s -> ERROR %s" % (nombre, str(ex)[:120]))
                continue

            precio, moneda = extraer(texto, prod.get("nominal"))
            if precio is None:
                errores.append("%s: no se encontró el precio "
                               "(¿agotado, bloqueado o cambió la página?)"
                               % nombre)
                print("[Eneba] %s -> sin precio" % nombre)
                continue

            print("[Eneba] %s -> %.2f %s" % (nombre, precio, moneda))
            clave = url
            previo = estado.get(clave)
            avisar, extra = decidir(nombre, precio, moneda, previo,
                                    prod.get("objetivo"))

            # Se guarda siempre, se avise o no.
            estado[clave] = {"p": precio, "m": moneda,
                             "f": datetime.date.today().isoformat(),
                             "nombre": nombre}
            if avisar:
                avisos.append({
                    "nombre": nombre, "precio": precio, "moneda": moneda,
                    "url": url, "extra": extra,
                })

        # --- Arbitraje entre regiones -------------------------------------
        # El mismo juego cuesta distinto según la región de la key. Medido en
        # vivo: GTA VI (PS5) a 69,22 US$ la de India contra 82,53 la de EE.UU.
        for grupo in cfg.get("comparar", []) or []:
            nombre = grupo.get("nombre", "?")
            lecturas = []
            for var in grupo.get("variantes", []) or []:
                url = var.get("url")
                if not url:
                    continue
                try:
                    texto = _texto_pagina(pagina, url, espera)
                except Exception as ex:
                    print("[Eneba/regiones] %s: %s" % (nombre, str(ex)[:80]))
                    continue
                precio, moneda = extraer(texto)
                print("[Eneba/regiones] %s (%s) -> %s %s"
                      % (nombre, var.get("region"), precio, moneda))
                lecturas.append((var.get("region"), precio, moneda))
                enlaces[(nombre, var.get("region"))] = url

            hallazgo = comparar_regiones(
                nombre, lecturas, grupo.get("referencia"),
                cfg.get("ahorro_minimo_pct", 10))
            if hallazgo:
                comparaciones.append(hallazgo)

        navegador.close()

    _guardar_estado(estado)

    if avisos:
        lineas = ["🛒 <b>Eneba — bajada de precio</b>", ""]
        for a in avisos:
            detalle = (" (" + "; ".join(a["extra"]) + ")") if a["extra"] else ""
            lineas.append('• <a href="%s"><b>%s</b></a>\n   %.2f %s%s'
                          % (a["url"], a["nombre"], a["precio"], a["moneda"],
                             detalle))
        telegram_notify.enviar_texto_suelto("\n".join(lineas))
        print("Avisos enviados: %d" % len(avisos))
    else:
        print("Sin bajadas de precio.")

    if comparaciones:
        lineas = ["🌍 <b>Más barato en otra región</b>", ""]
        for c in comparaciones:
            url = enlaces.get((c["nombre"], c["region"]), "")
            titulo = ('<a href="%s"><b>%s</b></a>' % (url, c["nombre"])
                      if url else "<b>%s</b>" % c["nombre"])
            lineas.append(
                "• %s\n   %s: <b>%.2f %s</b> · %s: %.2f %s\n"
                "   ahorras %.2f %s (%.0f%%)"
                % (titulo, c["region"], c["precio"], c["moneda"],
                   c["region_ref"], c["precio_ref"], c["moneda"],
                   c["ahorro"], c["moneda"], c["pct"]))
        lineas.append("\n⚠️ <i>Una key de otra región suele necesitar una "
                      "cuenta PSN de esa misma región. Es una compra aparte, "
                      "no un descuento en tu cuenta de siempre — comprueba las "
                      "restricciones en la página antes de pagar.</i>")
        telegram_notify.enviar_texto_suelto("\n".join(lineas))
        print("Comparaciones de región enviadas: %d" % len(comparaciones))

    # Solo damos la voz de alarma si fallaron TODOS: que falle uno es normal.
    if errores and len(errores) >= len(productos):
        telegram_notify.enviar_texto_suelto(
            "⚠️ <b>Eneba</b>: no se pudo leer ningún precio. "
            "Puede que hayan cambiado la página o estén bloqueando el bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
