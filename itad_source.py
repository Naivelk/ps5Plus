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


def notas(gids, clave, pais):
    """Nota de cada juego, para poder descartar el relleno.

    Devuelve {gid: (nota, cuantas_reseñas)}. Si algo falla devolvemos vacío:
    quedarse sin nota debe significar "no sé", no "es malo".
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
    if nota:
        trozos.append("nota %d" % int(nota[0]))
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
    limite = cfg.get("limite", 40)
    solo_nuevos = cfg.get("solo_nuevo_minimo", True)

    filtros = {"cut": {"min": corte_min, "max": 100}}
    if solo_nuevos:
        # Aquí está la diferencia entre "otra rebaja más" y una noticia.
        filtros["flag"] = FLAG_NUEVO_MINIMO

    cuerpo = {"country": pais, "limit": limite, "sort": "-cut",
              "filter": filtros}
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
                         clave, pais) if nota_min else {}

    resultados, sin_nota = [], 0
    for juego in lista:
        oferta = juego.get("deal") or {}
        precio, moneda = _precio(oferta.get("price"))
        if precio is None:
            continue
        gid = juego.get("id")
        nota = puntuaciones.get(gid)

        # Sin nota no descartamos: significa "no sé", no "es malo".
        if nota_min and nota and nota[0] < nota_min:
            continue
        if nota is None:
            sin_nota += 1

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

    if sin_nota:
        print("[ITAD] %d sin nota (se dejan pasar)" % sin_nota)
    return resultados, []
