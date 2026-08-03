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

# El cashback de Eneba es un descuento real que cambia con las campañas: se
# vio al 15% y al 18% en la misma tarjeta con días de diferencia. Sobre 93 USD
# son casi 17 de vuelta, así que merece salir en el aviso.
# Los cupones tipo LUCKY7 NO están aquí: son campañas que se aplican en el
# carrito y no aparecen en la página del producto, así que el bot no los ve.
RE_CASHBACK = re.compile(r"(\d{1,2})\s*%\s*de\s*Cashback", re.IGNORECASE)


def cashback(texto):
    """Porcentaje de cashback anunciado, o None."""
    m = RE_CASHBACK.search(texto or "")
    return int(m.group(1)) if m else None

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


# En las tarjetas, tras "Valor:" viene la tabla de denominaciones sueltas:
# 1 USD a 0,96 US$, 2 USD a 1,93 US$... hasta 30 USD a 30,65 US$. Esos NO son
# el precio del producto, y por su culpa el bot llegó a informar de la tarjeta
# de 25 "a 21,09" (que es el precio de otra denominación).
#
# Comprobado en la página de la de 25 USD:
#   antes de "Valor:"  -> 27,15 y 23,60   (precios reales, el bueno es 23,60)
#   después            -> 0,96 · 1,93 · 2,88 ...  (la tabla)
#
# Las páginas de juegos no llevan esta sección, así que se quedan enteras.
CORTES = ("Valor:", "Value:", "Wartość:")


def _recortar(texto):
    """Se queda con la parte de arriba, antes de la tabla de denominaciones."""
    for marca in CORTES:
        i = texto.find(marca)
        if i > 0:
            return texto[:i]
    return texto


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
    texto = _recortar(texto)

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
# El techo es 0.99 y no 1.0 por una razón de fondo: una tarjeta de saldo
# SIEMPRE cuesta menos que su valor de cara — nadie compra 100 USD de saldo
# por 100 USD. Así que un precio igual (o mayor) al nominal no es un precio,
# es que hemos leído el valor nominal por error. Pasó: la tarjeta de 100 se
# informó tres veces a "100.00" cuando su página decía "Desde: 93,37 US$".
MARGEN_BAJO = 0.60
MARGEN_ALTO = 0.99


def _plausible(valor, nominal):
    """Descarta precios imposibles para una tarjeta de valor conocido."""
    if not nominal:
        return True
    return nominal * MARGEN_BAJO <= valor <= nominal * MARGEN_ALTO


# Una bandera se distingue de un vistazo mejor que el nombre del país, que en
# una lista de precios todos parecen iguales.
BANDERAS = {
    "india": "🇮🇳", "ee.uu.": "🇺🇸", "eeuu": "🇺🇸", "usa": "🇺🇸",
    "estados unidos": "🇺🇸", "europa": "🇪🇺", "global": "🌐",
    "turquía": "🇹🇷", "turquia": "🇹🇷", "brasil": "🇧🇷", "méxico": "🇲🇽",
    "mexico": "🇲🇽", "españa": "🇪🇸", "espana": "🇪🇸", "francia": "🇫🇷",
    "colombia": "🇨🇴", "reino unido": "🇬🇧",
}


def _bandera(region):
    """Bandera + nombre, para que la región se lea sin esfuerzo."""
    emoji = BANDERAS.get((region or "").strip().lower(), "🏳")
    return "%s %s" % (emoji, region or "?")


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


def decidir(nombre, precio, moneda, previo, objetivo, bajada_minima=2.0):
    """¿Avisar? Devuelve (avisar, texto_extra).

    Mismo criterio que el PS Store: avisamos si BAJA respecto a la última
    lectura, no si está por debajo de un número fijo. Si no, o repites el
    mismo aviso cada pocas horas o te callas para siempre.

    `bajada_minima` es el porcentaje que tiene que bajar para molestarte. Sin
    él llegaban avisos como "47.03 (estaba a 47.04)": un céntimo. Los precios
    de Eneba se mueven solos entre vendedores, así que sin un mínimo esto se
    convierte en una alarma cada seis horas por nada.
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
        if anterior:
            caida = (anterior - precio) * 100.0 / anterior
            if caida >= bajada_minima:
                avisar = True
                extra.append("estaba a %.2f (-%.0f%%)" % (anterior, caida))
                if objetivo is not None and precio <= objetivo:
                    extra.append("bajo tu objetivo")
    return avisar, extra


def _texto_pagina(pagina, url, espera, intentos=2):
    # Un timeout suelto es normal (le pasó a la variante de Europa en un run);
    # con un reintento se salva sin perder toda la comparación de regiones.
    for intento in range(intentos):
        try:
            pagina.goto(url, wait_until="networkidle", timeout=45000)
            break
        except Exception:
            if intento + 1 >= intentos:
                raise
            print("[Eneba] timeout, reintento: %s" % url[-45:])
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

            # Agotado no es un fallo del bot: es que ahora mismo no se vende.
            # Distinguirlo importa porque, si contara como error, un producto
            # agotado de forma permanente acabaría disparando la alarma de
            # "Eneba no se puede leer" cuando en realidad todo va bien.
            if RE_AGOTADO.search(texto):
                print("[Eneba] %s -> agotado" % nombre)
                continue

            precio, moneda = extraer(texto, prod.get("nominal"))
            if precio is None:
                errores.append("%s: no se encontró el precio "
                               "(¿bloqueado o cambió la página?)" % nombre)
                print("[Eneba] %s -> sin precio" % nombre)
                continue

            vuelta = cashback(texto)
            print("[Eneba] %s -> %.2f %s%s"
                  % (nombre, precio, moneda,
                     " (%d%% cashback)" % vuelta if vuelta else ""))
            clave = url
            previo = estado.get(clave)
            avisar, extra = decidir(nombre, precio, moneda, previo,
                                    prod.get("objetivo"),
                                    cfg.get("bajada_minima_pct", 2.0))

            # Se guarda siempre, se avise o no.
            estado[clave] = {"p": precio, "m": moneda,
                             "f": datetime.date.today().isoformat(),
                             "nombre": nombre}
            if avisar:
                anterior = (previo or {}).get("p")
                avisos.append({
                    "nombre": nombre, "precio": precio, "moneda": moneda,
                    "url": url, "extra": extra, "cashback": vuelta,
                    "antes": anterior,
                    "pct": ((anterior - precio) * 100.0 / anterior
                            if anterior and anterior > precio else None),
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
        # Mismo lenguaje visual que el resto del bot: precio primero, luego
        # la barra con el ahorro en dinero, y el contexto debajo.
        lineas = ["🛒 <b>ENEBA</b> · bajó de precio\n" + telegram_notify.RAYA]
        for a in avisos:
            bloque = ['<a href="%s"><b>%s</b></a>' % (a["url"], a["nombre"])]
            dinero = ["<b>%s</b>" % telegram_notify._importe(a["precio"],
                                                             a["moneda"])]
            if a.get("antes"):
                dinero.append("<s>%s</s>" % telegram_notify._importe(
                    a["antes"], a["moneda"]))
            if a.get("pct"):
                dinero.append("<b>−%.0f%%</b>" % a["pct"])
            bloque.append("   " + "  ".join(dinero))
            if a.get("pct"):
                ahorro = a["antes"] - a["precio"]
                bloque.append("   %s  ahorras %s"
                              % (telegram_notify.barra(a["pct"]),
                                 telegram_notify._importe(ahorro, a["moneda"])))
            if a.get("cashback"):
                bloque.append("   💸 %d%% cashback" % a["cashback"])
            lineas.append("\n".join(bloque))
        telegram_notify.enviar_texto_suelto("\n\n".join(lineas))
        print("Avisos enviados: %d" % len(avisos))
    else:
        print("Sin bajadas de precio.")

    if comparaciones:
        lineas = ["🌍 <b>MÁS BARATO EN OTRA REGIÓN</b>\n" + telegram_notify.RAYA]
        for c in comparaciones:
            url = enlaces.get((c["nombre"], c["region"]), "")
            titulo = ('<a href="%s"><b>%s</b></a>' % (url, c["nombre"])
                      if url else "<b>%s</b>" % c["nombre"])
            # Las dos regiones alineadas una debajo de otra: así se compara de
            # un vistazo, sin buscar los números dentro de una frase.
            lineas.append(
                "%s\n"
                "   %s <b>%s</b>\n"
                "   %s %s\n"
                "   %s  ahorras %s <b>(−%.0f%%)</b>"
                % (titulo,
                   _bandera(c["region"]),
                   telegram_notify._importe(c["precio"], c["moneda"]),
                   _bandera(c["region_ref"]),
                   telegram_notify._importe(c["precio_ref"], c["moneda"]),
                   telegram_notify.barra(c["pct"]),
                   telegram_notify._importe(c["ahorro"], c["moneda"]),
                   c["pct"]))
        lineas.append("⚠️ <i>Una key de otra región suele necesitar una "
                      "cuenta PSN de esa misma región. Es una compra aparte, "
                      "no un descuento en tu cuenta de siempre — comprueba las "
                      "restricciones en la página antes de pagar.</i>")
        telegram_notify.enviar_texto_suelto("\n\n".join(lineas))
        print("Comparaciones de región enviadas: %d" % len(comparaciones))

    # Solo damos la voz de alarma si fallaron TODOS: que falle uno es normal.
    if errores and len(errores) >= len(productos):
        telegram_notify.enviar_texto_suelto(
            "⚠️ <b>Eneba</b>: no se pudo leer ningún precio. "
            "Puede que hayan cambiado la página o estén bloqueando el bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
