# -*- coding: utf-8 -*-
"""Ofertas de verdad, vía IsThereAnyDeal.

Resuelve tres cosas que las otras fuentes no pueden:

1. "Nunca antes había estado a este precio". ITAD marca cada oferta con un
   flag: N = nuevo mínimo histórico, H = iguala el mínimo, S = mínimo de esa
   tienda. Pedimos solo N (y H si lo activas), que es justo lo que hace que
   una oferta sea noticia y no una más del montón.

2. Juegos buenos, no relleno. Cada juego trae la nota de la crítica/usuarios,
   así que se puede exigir un mínimo. Un shovelware al -90% sigue siendo
   shovelware.

3. Muchas tiendas de una vez: Steam, Fanatical, G2A, Kinguin, Instant Gaming,
   GreenManGaming... sin scrapear ninguna. Esas webs bloquean scripts; ITAD
   ya las recopila y expone una API.

Hace falta ITAD_API_KEY (gratis en isthereanydeal.com/apps/). Sin ella el
módulo se calla y el resto del bot sigue igual.
"""
import os
import requests

BASE = "https://api.isthereanydeal.com"

# H = iguala el mínimo histórico, N = nuevo mínimo, S = mínimo de la tienda.
FLAG_NUEVO_MINIMO = "N"


def _clave():
    return os.environ.get("ITAD_API_KEY")


def _pedir(metodo, ruta, clave, params=None, cuerpo=None, timeout=25):
    params = dict(params or {})
    params["key"] = clave
    url = BASE + ruta
    if metodo == "POST":
        r = requests.post(url, params=params, json=cuerpo, timeout=timeout)
    else:
        r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _precio(obj):
    """Saca (importe, moneda) de un obj.price de ITAD."""
    if not isinstance(obj, dict):
        return None, None
    return obj.get("amount"), obj.get("currency")


def es_famoso(nota, nota_min, resenas_min):
    """¿Es un juego conocido y bien valorado, o relleno con buena media?

    La nota sola no vale: en la primera prueba real llegaron cosas como "no
    sleep for sole" con nota 100 y "Age of Fear 3" con 93. Notas altísimas
    sacadas de un puñado de reseñas. Lo que separa un juegazo de un desconocido
    es CUÁNTA gente lo ha valorado, así que se exigen las dos cosas.

    Sin datos se descarta: si buscas juegos famosos, no tener reseñas ya es
    una respuesta.
    """
    if not nota:
        return False
    valor, cuenta = nota
    if nota_min and valor < nota_min:
        return False
    return not resenas_min or (cuenta or 0) >= resenas_min


def notas(gids, clave, pais):
    """Nota y número de reseñas de cada juego.

    Devuelve {gid: (nota, cuantas_reseñas)}. El número de reseñas importa
    tanto como la nota: es la señal de si el juego es conocido.
    """
    salida = {}
    for gid in gids:
        try:
            datos = _pedir("GET", "/games/info/v2", clave,
                           params={"id": gid, "country": pais})
        except Exception as ex:
            print("[ITAD] info de %s falló: %s" % (gid[:8], str(ex)[:60]))
            continue
        mejor = None
        for rev in (datos.get("reviews") or []):
            score = rev.get("score")
            if score is None:
                continue
            cuenta = rev.get("count") or 0
            if mejor is None or cuenta > mejor[1]:
                mejor = (score, cuenta)
        if mejor:
            salida[gid] = mejor
    return salida


def es_precio_anomalo(oferta, factor=0.5):
    """¿Huele a error de precio de la tienda?

    No hay forma de saberlo con certeza, pero un precio muy por debajo del
    mínimo del último año suele serlo. Se marca para que puedas decidir rápido:
    estas cosas duran minutos porque la tienda las corrige.

    Aviso importante que va también en el mensaje: una tienda puede cancelar
    un pedido hecho a un precio equivocado, y suele hacerlo. No es dinero
    seguro, es una oportunidad con riesgo de que te devuelvan el importe.
    """
    precio = (oferta.get("price") or {}).get("amount")
    if precio is None:
        return False
    anterior = (oferta.get("historyLow_1y") or {}).get("amount")
    if anterior and precio < anterior * factor:
        return True
    # Un -97% tampoco suele ser una promoción pensada.
    return (oferta.get("cut") or 0) >= 97


def _titulo(juego, oferta, nota):
    precio, moneda = _precio(oferta.get("price"))
    regular, _ = _precio(oferta.get("regular"))
    tienda = (oferta.get("shop") or {}).get("name", "?")
    corte = oferta.get("cut")

    trozos = ["%s — %.2f %s" % (juego, precio, moneda)]
    if corte:
        trozos.append("-%d%%" % corte)
    if regular:
        trozos.append("antes %.2f" % regular)
    trozos.append("en %s" % tienda)
    if oferta.get("flag") == FLAG_NUEVO_MINIMO:
        trozos.append("🏆 nunca había estado tan barato")
    if oferta.get("voucher"):
        # Un cupón que hay que meter a mano en el carrito: sin el código, el
        # precio de arriba no sale.
        trozos.append("🎟 con el cupón %s" % oferta["voucher"])
    if es_precio_anomalo(oferta):
        trozos.append("⚡ precio rarísimo, puede ser un error de la tienda")
    if nota:
        # La cantidad de reseñas va delante: es lo que distingue un juegazo
        # de un desconocido con nota alta y cuatro votos.
        valor, cuenta = nota
        if cuenta:
            trozos.append("nota %d (%s reseñas)" % (int(valor), f"{cuenta:,}"))
        else:
            trozos.append("nota %d" % int(valor))
    return " · ".join(trozos)


def obtener(config):
    """Devuelve (items, errores) con las ofertas que de verdad merecen aviso."""
    cfg = config.get("itad", {}) or {}
    if not cfg.get("activo", True):
        return [], []

    clave = _clave()
    if not clave:
        # No es un error: simplemente no está configurado.
        print("[ITAD] sin ITAD_API_KEY, me salto esta fuente.")
        return [], []

    pais = cfg.get("pais", "CO")
    corte_min = cfg.get("descuento_minimo", 60)
    nota_min = cfg.get("nota_minima", 75)
    resenas_min = cfg.get("resenas_minimas", 1000)
    limite = cfg.get("limite", 40)
    solo_nuevos = cfg.get("solo_nuevo_minimo", True)
    solo_juegos = cfg.get("solo_juegos", True)
    tiendas_fuera = [t.lower() for t in cfg.get("tiendas_excluidas", [])]
    plataformas = [p.lower() for p in cfg.get("plataformas", [])]

    filtros = {"cut": {"min": corte_min, "max": 100}}
    if solo_nuevos:
        # Aquí está la diferencia entre "otra rebaja más" y una noticia.
        filtros["flag"] = FLAG_NUEVO_MINIMO

    # vouchers=true para que entren también las ofertas que dependen de meter
    # un código en el carrito; el código viene en el aviso.
    cuerpo = {"country": pais, "limit": limite, "sort": "-cut",
              "vouchers": True, "filter": filtros}
    try:
        datos = _pedir("POST", "/deals/v2", clave, cuerpo=cuerpo)
    except Exception as ex:
        return [], ["ITAD: %s" % str(ex)[:120]]

    lista = datos.get("list") or []
    print("[ITAD] %d ofertas con -%d%% o más%s"
          % (len(lista), corte_min,
             " y nuevo mínimo histórico" if solo_nuevos else ""))
    if not lista:
        return [], []

    puntuaciones = notas([j.get("id") for j in lista if j.get("id")],
                         clave, pais)

    resultados = []
    descartes = {"tipo": 0, "tienda": 0, "plataforma": 0, "poco_conocido": 0}
    for juego in lista:
        oferta = juego.get("deal") or {}
        precio, moneda = _precio(oferta.get("price"))
        if precio is None:
            continue

        # DLC y packs fuera: en la primera prueba llegaron "SOEDESCO
        # Publishing Bundle" y dos "Season Pass" con descuentos enormes que
        # no son juegos.
        if solo_juegos and juego.get("type") not in (None, "game"):
            descartes["tipo"] += 1
            continue

        tienda = (oferta.get("shop") or {}).get("name", "")
        if any(t in tienda.lower() for t in tiendas_fuera):
            descartes["tienda"] += 1
            continue

        if plataformas:
            suyas = [(p.get("name") or "").lower()
                     for p in (oferta.get("platforms") or [])]
            if suyas and not any(
                    any(p in s for s in suyas) for p in plataformas):
                descartes["plataforma"] += 1
                continue

        gid = juego.get("id")
        nota = puntuaciones.get(gid)
        if not es_famoso(nota, nota_min, resenas_min):
            descartes["poco_conocido"] += 1
            continue

        resultados.append({
            "id": "itad:%s:%s:%.2f" % (gid, (oferta.get("shop") or {}).get("id"),
                                       precio),
            "titulo": _titulo(juego.get("title", "?"), oferta, nota),
            "descripcion": "",
            "url": oferta.get("url") or juego.get("urls", {}).get("game", ""),
            "fuente": "ITAD · %s" % (oferta.get("shop") or {}).get("name", "?"),
            "autor": "",
            "fecha_dt": None,
            "imagen": None,
            "categoria": "oferton",
            "precio": precio,
            "moneda": moneda,
            "descuento": oferta.get("cut"),
            "chollo": False,
            "region": None,
            # Viene estructurado de una API: no pasa por los filtros de texto,
            # pero sí por el anti-estafa, que mira el dominio.
            "directo_texto": True,
        })

    print("[ITAD] %d pasan el filtro | descartados: %s"
          % (len(resultados),
             ", ".join("%s=%d" % (k, v) for k, v in descartes.items() if v)
             or "ninguno"))
    return resultados, []
