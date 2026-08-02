# -*- coding: utf-8 -*-
"""Pruebas rápidas del bot. No usan red: se ejecutan en segundos.

Corre solo en cada push (.github/workflows/ci.yml), y a mano con:

    python smoke_test.py

Cubre lo que de verdad se ha roto alguna vez en este proyecto:
  - que config.yaml sea YAML válido y tenga las claves que el código espera
    (es donde se toca a mano, y un fallo de indentación rompía todo);
  - que la estafa real que se coló marcada como legítima siga saliendo roja;
  - que una oferta repetida meses después vuelva a avisar (el bug del
    historial);
  - que el parseo del PS Store siga leyendo los 9 planes.
"""
import io
import os
import sys
import datetime

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

import yaml

import code_filter
import historial
import store_source
import itad_source
import eneba_watch
import latido
import main as bot

FIXTURE = os.path.join(RAIZ, "tests", "fixtures", "store.html")

fallos = []


def revisar(condicion, descripcion, detalle=""):
    if condicion:
        print("  ok   %s" % descripcion)
    else:
        print("  FALLA %s %s" % (descripcion, detalle))
        fallos.append(descripcion)


def igual(obtenido, esperado, descripcion):
    revisar(obtenido == esperado, descripcion,
            "-> obtenido %r, esperado %r" % (obtenido, esperado))


# --------------------------------------------------------------- config.yaml
def probar_config():
    print("\nconfig.yaml")
    with io.open(os.path.join(RAIZ, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    revisar(isinstance(cfg, dict), "es YAML válido y es un diccionario")

    # Las que el código lee sin valor por defecto o que romperían el sentido.
    for clave in ("palabras_psplus", "palabras_suscripcion",
                  "palabras_tarjeta_psn", "palabras_excluir",
                  "palabras_excluir_titulo",
                  "senales_codigo_gratis", "senales_estafa",
                  "dominios_confiables", "acortadores", "precio_objetivo",
                  "precio_piso", "senales_region", "reddit", "feeds_rss",
                  "store", "latido", "mis_regiones"):
        revisar(clave in cfg, "existe '%s'" % clave)

    for clave in ("palabras_psplus", "senales_estafa", "feeds_rss"):
        valor = cfg.get(clave)
        revisar(isinstance(valor, list) and len(valor) > 0,
                "'%s' es una lista no vacía" % clave)

    regiones = cfg.get("mis_regiones") or []
    revisar(isinstance(regiones, list) and regiones,
            "mis_regiones es una lista no vacía", "-> %r" % regiones)
    for r in regiones:
        revisar(r in ("US", "CO"), "región válida", "-> %r" % r)

    revisar((cfg.get("latido") or {}).get("hora_utc") in range(24),
            "latido.hora_utc está entre 0 y 23")
    revisar(isinstance(cfg.get("palabras_tarjeta_psn"), list)
            and cfg.get("palabras_tarjeta_psn"),
            "palabras_tarjeta_psn es una lista no vacía")

    # El piso tiene que quedar por debajo del objetivo o nada dispararía.
    for moneda, objetivo in (cfg.get("precio_objetivo") or {}).items():
        piso = (cfg.get("precio_piso") or {}).get(moneda)
        revisar(piso is None or piso < objetivo,
                "precio_piso[%s] < precio_objetivo[%s]" % (moneda, moneda))

    revisar((cfg.get("latido") or {}).get("dia") in range(7),
            "latido.dia está entre 0 y 6")

    for url in cfg.get("feeds_rss") or []:
        revisar(url.startswith("http"), "feed con URL válida", "-> %s" % url[:40])
    return cfg


# ------------------------------------------------------------------- filtros
def probar_filtros(cfg):
    print("\nfiltros de texto")
    psplus = cfg["palabras_psplus"]
    susc = cfg["palabras_suscripcion"]
    codigo = cfg["senales_codigo_gratis"]

    casos_relevancia = [
        ("PS Plus 12 Month Essential - $39.99", True,
         "oferta de suscripción"),
        ("PlayStation Plus Offers Rare 50% Discount", True,
         "titular de descuento sin duración"),
        ("Black Ops 1 & 2 are on sale with PS Plus for PS5", False,
         "oferta de un juego, no de la suscripción"),
        ("Baldur's Gate 3 Couch Coop Issues", False, "nada que ver"),
    ]
    for titulo, esperado, desc in casos_relevancia:
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        obtenido = code_filter.es_relevante(it, psplus, susc, codigo)
        igual(bool(obtenido), esperado, "relevancia: %s" % desc)

    # Este pasa es_relevante pero lo tiene que matar la exclusión.
    it = {"titulo": "Call of Duty: Black Ops Out Now, PS Plus Members Get 50% off",
          "descripcion": "", "url": ""}
    revisar(code_filter.esta_excluido(it, cfg["palabras_excluir"],
                                      cfg["palabras_excluir_titulo"]),
            "exclusión: oferta de juego para miembros")

    it = {"titulo": "8 PS Plus Extra, Premium Games for May 2026 Announced",
          "descripcion": "", "url": ""}
    revisar(code_filter.esta_excluido(it, cfg["palabras_excluir"],
                                      cfg["palabras_excluir_titulo"]),
            "exclusión: catálogo mensual")

    # Titulares REALES que llegaron al chat o estaban a punto. El primero es
    # el que se coló de verdad; el resto salieron del mismo feed en español.
    ruido = [
        "PlayStation Plus tendra uno de sus peores meses en agosto y perdera 9 juegos",
        "Llegan siete juegos gratis a PS5 sin necesidad PlayStation Plus: como descargarlos",
        "Filtran el segundo juego que llegara a PlayStation Plus Essential en abril",
        "Tres juegos se van de PlayStation Plus Extra y Premium en diciembre",
        "PlayStation Plus Mayo 2026 anunciado oficialmente con 3 nuevos juegos",
        "Persona 5 Royal y mas juegos llegaran a PlayStation Plus",
    ]
    for titulo in ruido:
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        pasa = (code_filter.es_relevante(it, psplus, susc, codigo)
                and not code_filter.esta_excluido(it, cfg["palabras_excluir"],
                                                  cfg["palabras_excluir_titulo"]))
        revisar(not pasa, "descarta ruido: %s" % titulo[:44])

    # Y estas NO se pueden perder por afinar de más.
    buenas = [
        "PlayStation Plus Offers Rare 50% Discount to Users Quitting",
        "One year of PS Plus Premium is $108 in new Days of Play sale",
        "[PSN] PS Plus 12 Month Essential - $39.99",
        "Membresia de PlayStation sera mas cara en Colombia: ultima oportunidad",
    ]
    for titulo in buenas:
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        pasa = (code_filter.es_relevante(it, psplus, susc, codigo)
                and not code_filter.esta_excluido(it, cfg["palabras_excluir"],
                                                  cfg["palabras_excluir_titulo"]))
        revisar(pasa, "deja pasar oferta: %s" % titulo[:44])


def probar_ruido_real(cfg):
    """Los mensajes que llegaron al chat y no debían.

    Todos salieron de r/PlayStationPlus (un sub de soporte, no de ofertas) o
    de noticias del catálogo. Son preguntas y quejas de usuarios.
    """
    print("\npreguntas y quejas de usuarios (ruido real del chat)")
    ruido = [
        "Is there anyway to get free ps plus for even a day ? Wether new account or old ?",
        "i cancelled my ps plus myself after a failed payment and the ps app said i "
        "can still use it for one more month but now i can't use it",
        "How do I get 12 months of PS Plus cheaper",
        "Alguien sabe si el PS Plus de 12 meses sirve en otra cuenta",
    ]
    for titulo in ruido:
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        precio, _ = code_filter.extraer_precio(titulo)
        descartado = precio is None and code_filter.es_pregunta_o_queja(it)
        revisar(descartado, "descarta consulta: %s" % titulo[:42])

    # Pero si trae precio, aunque acabe en "?", sí interesa.
    it = {"titulo": "PS Plus 12 months for $39.99, worth it?", "descripcion": "",
          "url": ""}
    precio, _ = code_filter.extraer_precio(it["titulo"])
    revisar(precio is not None,
            "conserva una pregunta que SÍ trae precio")

    # Este llegó al chat y a primera vista parece ruido, pero informa de una
    # oferta real (hay 25% de descuento, aunque solo en Premium). El fallo
    # estaba en la fuente, no en el título: venía de r/PlayStationPlus, que
    # ya no se consulta. Se deja pasar a propósito.
    it = {"titulo": "PS Plus 25% off offer only showing for PS Plus Premium",
          "descripcion": "", "url": ""}
    revisar(not code_filter.es_pregunta_o_queja(it),
            "conserva el aviso de un 25% aunque venga en tono de queja")


def probar_tarjetas(cfg):
    """Saldo PSN con descuento: suele ser mejor negocio que la propia rebaja."""
    print("\ntarjetas / saldo PSN")
    palabras = cfg["palabras_tarjeta_psn"]
    casos = [
        ("PlayStation Network Card 100 USD (USA) PSN Key UNITED STATES", True),
        ("[Eneba] PSN Card $50 - 20% off", True),
        ("Tarjeta PSN 100 USD a 273.250 COP", True),
        ("PS Plus 12 Month Essential - $39.99", False),
        ("Baldur's Gate 3 on sale", False),
    ]
    for titulo, esperado in casos:
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        igual(code_filter.es_tarjeta_psn(it, palabras), esperado,
              "tarjeta: %s" % titulo[:44])

    # Una tarjeta no puede morir en el filtro de ruido pensado para noticias.
    it = {"titulo": "PlayStation Network Card 100 USD (USA) PSN Key",
          "descripcion": "", "url": ""}
    revisar(not code_filter.esta_excluido(it, cfg["palabras_excluir"],
                                          cfg["palabras_excluir_titulo"]),
            "la tarjeta sobrevive al filtro de exclusión")


def probar_ofertones(cfg):
    """Descuentos de juegos: el umbral es lo único que evita el diluvio."""
    print("\nofertones de juegos")
    casos = [
        ("[Steam] Elden Ring ($23.99 / 60% off)", 60),
        ("[PSN] God of War Ragnarok - $34.99 (50% off)", 50),
        ("[Eneba] Cyberpunk 2077 -85%", 85),
        ("Rebajas de verano: hasta 90% de descuento", 90),
        ("Pack saga completa, descuento del 75%", 75),
        # Mezcla varios: nos quedamos con el mayor, que es el que engancha.
        ("[Steam] Saga completa 70% off, hasta 85% en packs", 85),
        ("PS Plus Essential 12 meses", None),
    ]
    for texto, esperado in casos:
        igual(code_filter.extraer_descuento(texto), esperado,
              "descuento de %r" % texto[:40])

    # 100% no es un descuento, es un regalo o un error de quien lo escribió.
    igual(code_filter.extraer_descuento("100% off everything"), None,
          "ignora el 100%")

    minimo = (cfg.get("ofertas_juegos") or {}).get("descuento_minimo", 75)
    revisar(50 <= minimo <= 95,
            "descuento_minimo razonable (50-95)", "-> %r" % minimo)

    # Lo que sigues no puede quedar tapado por un ofertón cualquiera.
    seguidos = cfg.get("seguimiento") or []
    revisar(seguidos, "hay lista de seguimiento")
    for titulo in ("GTA VI Deluxe Edition PS5 -20%",
                   "[PSN] EA Sports FC 27 - $41.99 (30% off)",
                   "PS Plus 12 meses"):
        it = {"titulo": titulo, "descripcion": "", "url": ""}
        revisar(code_filter.es_seguido(it, seguidos),
                "sigue: %s" % titulo[:40])

    it = {"titulo": "Random Indie Puzzle Game -90%", "descripcion": "", "url": ""}
    revisar(not code_filter.es_seguido(it, seguidos),
            "no marca como seguido un juego cualquiera")


def probar_nombres_existen():
    """Que no se llame a funciones privadas que no existen.

    Un NameError así tumbó el bot en producción con el CI en verde: al
    renombrar _leer a _leer_rss quedó una llamada al nombre viejo, y eso
    compileall no lo ve. En CI lo cubre pyflakes; aquí se comprueba sin
    dependencias para poder ejecutarlo en cualquier sitio.
    """
    import ast
    import glob as _glob

    print("\nnombres de funciones internas")
    for ruta in sorted(_glob.glob(os.path.join(RAIZ, "*.py"))):
        with io.open(ruta, encoding="utf-8") as f:
            arbol = ast.parse(f.read(), ruta)

        definidas = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                definidas.add(nodo.name)
            elif isinstance(nodo, ast.Name) and isinstance(nodo.ctx, ast.Store):
                definidas.add(nodo.id)
            elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
                for alias in nodo.names:
                    definidas.add(alias.asname or alias.name.split(".")[0])

        # Solo miramos las privadas (_algo): son las del propio módulo, así
        # que no hay falsos positivos con builtins ni con lo importado.
        faltan = set()
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call)
                    and isinstance(nodo.func, ast.Name)
                    and nodo.func.id.startswith("_")
                    and nodo.func.id not in definidas):
                faltan.add(nodo.func.id)

        revisar(not faltan, "%s: sin llamadas a funciones inexistentes"
                % os.path.basename(ruta),
                "-> falta %s" % ", ".join(sorted(faltan)))


def probar_itad(cfg):
    """ITAD: sin key debe callarse, no romper."""
    print("\nIsThereAnyDeal")
    icfg = cfg.get("itad") or {}
    revisar(icfg.get("solo_nuevo_minimo") is True,
            "pide solo NUEVO mínimo histórico (lo que hace noticia una oferta)")
    revisar(50 <= icfg.get("descuento_minimo", 0) <= 95,
            "descuento_minimo razonable", "-> %r" % icfg.get("descuento_minimo"))
    revisar(0 <= icfg.get("nota_minima", -1) <= 100,
            "nota_minima entre 0 y 100", "-> %r" % icfg.get("nota_minima"))
    revisar(len(icfg.get("pais", "")) == 2,
            "pais con código de dos letras", "-> %r" % icfg.get("pais"))

    # Sin la key la fuente se salta sola: no debe tumbar el bot.
    previo = os.environ.pop("ITAD_API_KEY", None)
    try:
        items, errores = itad_source.obtener(cfg)
        igual((items, errores), ([], []), "sin key: se calla y no da error")
    finally:
        if previo is not None:
            os.environ["ITAD_API_KEY"] = previo

    # Y desactivado tampoco hace nada, aunque hubiera key.
    items, errores = itad_source.obtener({"itad": {"activo": False}})
    igual((items, errores), ([], []), "desactivado: no hace nada")

    # El título de un aviso tiene que decir lo importante de un vistazo.
    oferta = {"price": {"amount": 11.99, "currency": "USD"},
              "regular": {"amount": 59.99, "currency": "USD"},
              "shop": {"name": "Fanatical"}, "cut": 80, "flag": "N"}
    t = itad_source._titulo("Elden Ring", oferta, (94, 1200))
    for trozo in ("Elden Ring", "11.99", "-80%", "Fanatical", "nota 94"):
        revisar(trozo in t, "el aviso incluye %r" % trozo)
    revisar("nunca había estado tan barato" in t,
            "avisa de que es mínimo histórico")


def probar_eneba(cfg):
    """Lectura del precio de Eneba a partir del texto de la página."""
    print("\nEneba")
    casos = [
        ("Desde:\n93,43 US$\nNo es el precio final", 93.43, "USD"),
        ("Desde: 273.250 COP", 273250.0, "COP"),
        ("Desde: 1.234,56 COP", 1234.56, "COP"),
        # Página de JUEGO: no escribe "Desde" por ningún lado. Texto real de
        # la de GTA VI, donde el precio bueno es el más bajo de los tres.
        ("Grand Theft Auto VI (PS5) PSN Key INDIA\n31\n76,14 US$\n"
         "76,14 US$\n69,22 US$\n15% de Cashback", 69.22, "USD"),
        ("Sin precio en esta página", None, None),
        # El "31" de valoraciones y el "15" del cashback no son precios.
        ("Valoraciones 31\n15% de Cashback", None, None),
    ]
    for texto, ep, em in casos:
        igual(eneba_watch.extraer(texto), (ep, em),
              "precio Eneba de %r" % texto[:34])

    # Mismo criterio que el Store: avisar por bajada, no por número fijo.
    avisar, _ = eneba_watch.decidir("x", 93.43, "USD", None, None)
    revisar(not avisar, "primera lectura sin objetivo: no avisa")

    avisar, _ = eneba_watch.decidir("x", 88.0, "USD", None, 90)
    revisar(avisar, "primera lectura bajo objetivo: avisa")

    avisar, _ = eneba_watch.decidir("x", 93.43, "USD", {"p": 93.43}, 90)
    revisar(not avisar, "mismo precio que ayer: no avisa")

    avisar, extra = eneba_watch.decidir("x", 85.0, "USD", {"p": 93.43}, None)
    revisar(avisar and extra, "el precio baja: avisa y dice desde cuánto")

    urls = [p.get("url", "") for p in (cfg.get("eneba") or {}).get("productos", [])]
    revisar(urls and all(u.startswith("https://www.eneba.com/") for u in urls),
            "todas las URLs de Eneba son de eneba.com")

    # --- arbitraje entre regiones, con los precios reales medidos ---
    print("\nEneba: comparación entre regiones")
    lecturas = [("EE.UU.", 82.53, "USD"), ("India", 69.22, "USD"),
                ("Europa", 88.0, "USD")]
    h = eneba_watch.comparar_regiones("GTA VI", lecturas, "EE.UU.", 10)
    revisar(h is not None, "detecta que India sale más barata")
    if h:
        igual(h["region"], "India", "elige la región más barata")
        revisar(abs(h["ahorro"] - 13.31) < 0.01, "calcula bien el ahorro")
        revisar(15 < h["pct"] < 17, "calcula bien el porcentaje (~16%)")

    # Si la diferencia es pequeña no merece la pena montar otra cuenta.
    h = eneba_watch.comparar_regiones(
        "X", [("EE.UU.", 80.0, "USD"), ("India", 77.0, "USD")], "EE.UU.", 10)
    revisar(h is None, "ignora diferencias por debajo del mínimo")

    # Mezclar monedas sería inventarse un tipo de cambio.
    h = eneba_watch.comparar_regiones(
        "X", [("EE.UU.", 80.0, "USD"), ("CO", 200000.0, "COP")], "EE.UU.", 10)
    revisar(h is None, "no compara entre monedas distintas")

    h = eneba_watch.comparar_regiones(
        "X", [("EE.UU.", 80.0, "USD"), ("India", None, None)], "EE.UU.", 10)
    revisar(h is None, "una lectura fallida no genera comparación falsa")

    grupos = (cfg.get("eneba") or {}).get("comparar", []) or []
    for g in grupos:
        regiones = [v.get("region") for v in g.get("variantes", [])]
        revisar(g.get("referencia") in regiones,
                "la referencia de %r está entre sus variantes" % g.get("nombre"))


def probar_precios(cfg):
    print("\nextracción de precios")
    casos = [
        ("[PSN] PS Plus 12 Month Essential - $39.99", 39.99, "USD"),
        ("PS Plus Premium - was $159.99 now $89.99", 89.99, "USD"),
        ("PlayStation Plus 12 meses por $150.000 COP", 150000.0, "COP"),
        ("PS Plus monthly games for July 2026", None, None),
    ]
    for texto, ep, em in casos:
        p, m = code_filter.extraer_precio(texto)
        igual((p, m), (ep, em), "precio de %r" % texto[:38])

    umbrales, pisos = cfg["precio_objetivo"], cfg["precio_piso"]
    revisar(code_filter.bajo_umbral(39.99, "USD", umbrales, pisos),
            "39.99 USD cuenta como chollo")
    # Un precio mensual no puede colarse como chollo de 12 meses.
    revisar(not code_filter.bajo_umbral(10.99, "USD", umbrales, pisos),
            "10.99 USD (precio mensual) NO es chollo")


def probar_estafas(cfg):
    print("\nfiltro anti-estafa")
    casos = [
        ("[ NEW ] FREE PS Plus PS4, PS5 FREE Playstation Plus CODES Tutorial "
         "2026 (No Trial) Plaid Cymru Manifesto (vd3UbCkcTD)",
         "https://mshale.com/x", "riesgo", "la estafa real que se coló"),
        ("Free PS Plus Premium Being Offered to Select PS5 Players",
         "https://blog.playstation.com/x", "ok", "regalo oficial de Sony"),
        ("Sony is giving away a free year of PS Plus",
         "https://www.eurogamer.net/x", "duda", "regalo en sitio no conocido"),
        ("PS Plus 12 Month Membership 25% off",
         "https://www.amazon.com/x", "ok", "oferta en tienda conocida"),
    ]
    for titulo, url, esperado, desc in casos:
        it = {"titulo": titulo, "descripcion": "", "url": url}
        it["categoria"] = code_filter.categoria(it, cfg["senales_codigo_gratis"])
        nivel, _ = code_filter.evaluar(it, cfg["senales_estafa"],
                                       cfg["dominios_confiables"],
                                       cfg["acortadores"])
        igual(nivel, esperado, "estafa: %s" % desc)


# ------------------------------------------------------------------ PS Store
def probar_store(cfg):
    print("\nPlayStation Store")
    if not os.path.exists(FIXTURE):
        revisar(False, "existe el fixture del Store", "-> falta %s" % FIXTURE)
        return
    with io.open(FIXTURE, encoding="utf-8") as f:
        filas = store_source.planes(f.read())

    igual(len(filas), 9, "lee los 9 planes")
    combinaciones = set((f["plan"], f["meses"]) for f in filas)
    for plan in ("Essential", "Extra", "Premium"):
        for meses in (1, 3, 12):
            revisar((plan, meses) in combinaciones,
                    "encuentra %s %d meses" % (plan, meses))
    for f in filas:
        revisar(f["precio"] > 0 and f["base"] > 0,
                "%s %dm tiene precio > 0" % (f["plan"], f["meses"]))
        revisar(f["moneda"] == "USD", "%s %dm en USD" % (f["plan"], f["meses"]))

    umbrales = cfg["precio_objetivo"]
    fila = {"plan": "Essential", "meses": 12, "precio": 64.99, "base": 64.99,
            "moneda": "USD"}

    avisar, _ = store_source.evaluar_fila(fila, None, umbrales)
    revisar(not avisar, "primera vez sin rebaja: no avisa")

    rebajada = dict(fila, precio=39.99)
    avisar, motivo = store_source.evaluar_fila(rebajada, None, umbrales)
    revisar(avisar and motivo == "rebaja", "primera vez con rebaja: avisa")

    avisar, _ = store_source.evaluar_fila(fila, {"p": 64.99}, umbrales)
    revisar(not avisar, "precio igual que ayer: no avisa (nada de spam)")

    avisar, motivo = store_source.evaluar_fila(rebajada, {"p": 64.99}, umbrales)
    revisar(avisar and motivo == "bajada", "el precio baja: avisa")


# ----------------------------------------------------------------- historial
def probar_historial():
    print("\nhistorial de precios (el bug de la oferta repetida)")
    datos = {}
    k = historial.clave("en-us", "Essential", 12)
    umbrales = {"USD": 45}
    fila = {"plan": "Essential", "meses": 12, "base": 64.99, "moneda": "USD"}

    # Black Friday del año pasado, se acaba, y vuelve al año siguiente.
    guion = [
        ("2025-11-01", 64.99, False, "precio normal"),
        ("2025-11-28", 39.99, True, "Black Friday: avisa"),
        ("2025-12-20", 64.99, False, "se acaba la promo"),
        ("2026-11-27", 39.99, True, "Black Friday del año siguiente: AVISA"),
    ]
    for fecha, precio, espera_aviso, desc in guion:
        actual = dict(fila, precio=precio)
        previo = historial.ultimo(datos, k)
        avisar, _ = store_source.evaluar_fila(actual, previo, umbrales)
        historial.registrar(datos, k, precio, fila["base"], hoy=fecha)
        igual(bool(avisar), espera_aviso, "historial: %s" % desc)

    # Correr dos veces el mismo día no debe duplicar el historial.
    antes = len(datos[k])
    historial.registrar(datos, k, 39.99, 64.99, hoy="2026-11-27")
    igual(len(datos[k]), antes, "no duplica si el precio no cambió")

    igual(historial.minimo(datos, k), 39.99, "calcula el mínimo histórico")


def probar_vistos():
    print("\nmemoria de lo ya avisado")
    hoy = datetime.date(2026, 7, 28)
    vistos = {
        "rss:reciente": "2026-07-27",
        "rss:viejo": "2026-01-01",
    }
    podado = bot.podar_vistos(vistos, 45, hoy=hoy)
    revisar("rss:reciente" in podado, "conserva lo reciente")
    revisar("rss:viejo" not in podado, "olvida lo viejo (>45 días)")

    # El formato antiguo era una lista; no debe perderse al actualizar.
    ruta = os.path.join(RAIZ, "tests", "_tmp_seen.json")
    with io.open(ruta, "w", encoding="utf-8") as f:
        f.write('["rss:uno", "rss:dos"]')
    try:
        cargado = bot.cargar_vistos(ruta)
        revisar(isinstance(cargado, dict) and len(cargado) == 2,
                "lee el formato viejo (lista) sin perder nada")
    finally:
        os.remove(ruta)


def probar_latido(cfg):
    print("\nlatido semanal")
    cfg_latido = cfg["latido"]
    domingo = datetime.date(2026, 8, 2)
    hora = cfg_latido.get("hora_utc", 14)
    revisar(domingo.weekday() == 6, "el 2026-08-02 es domingo")

    revisar(latido.toca(cfg_latido, None, domingo, hora),
            "domingo a su hora: manda")
    revisar(not latido.toca(cfg_latido, domingo.isoformat(), domingo, hora),
            "si ya se mandó hoy: no repite")
    revisar(not latido.toca(cfg_latido, None, datetime.date(2026, 7, 29), hora),
            "un miércoles: no manda")
    revisar(not latido.toca({"activo": False, "dia": 6}, None, domingo, hora),
            "desactivado: no manda")

    # El spam real: el estado se perdía y creía ser siempre la primera vez.
    # El cerrojo de la hora tiene que aguantarlo aunque eso vuelva a pasar.
    envios = sum(1 for h in range(24)
                 for _ in range(2)          # el bot corre 2 veces por hora
                 if latido.toca(cfg_latido, None, domingo, h))
    igual(envios, 2, "sin estado, como mucho 2 envíos en todo el día "
                     "(antes eran 48)")

    datos = {}
    k = historial.clave("en-us", "Essential", 12)
    historial.registrar(datos, k, 39.99, 64.99, hoy="2026-07-01")
    texto = latido.componer(datos, [("Essential 12 meses", k)], 6,
                            hoy=datetime.date(2026, 7, 28))
    revisar("39.99" in texto, "el resumen incluye el precio actual")
    revisar("rebajado" in texto, "marca que está rebajado")


def main():
    print("=" * 62)
    print("SMOKE TEST — psplus-alert")
    print("=" * 62)
    probar_nombres_existen()
    cfg = probar_config()
    probar_filtros(cfg)
    probar_ruido_real(cfg)
    probar_tarjetas(cfg)
    probar_ofertones(cfg)
    probar_itad(cfg)
    probar_eneba(cfg)
    probar_precios(cfg)
    probar_estafas(cfg)
    probar_store(cfg)
    probar_historial()
    probar_vistos()
    probar_latido(cfg)

    print("\n" + "=" * 62)
    if fallos:
        print("FALLARON %d comprobaciones:" % len(fallos))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("TODO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
