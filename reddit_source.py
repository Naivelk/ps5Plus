"""Lee ofertas y códigos de PS Plus desde Reddit.

Dos caminos, y usa el mejor que tenga disponible:

  A) API oficial (si defines REDDIT_CLIENT_ID y REDDIT_CLIENT_SECRET).
     Es gratis, son 100 peticiones por minuto y NO te cortan. Recomendado,
     sobre todo en GitHub Actions.
  B) RSS público (sin configurar nada). Funciona, pero Reddit corta con 429
     con mucha facilidad.

Todo esto salió de probar contra Reddit de verdad, no de suponer:

1. Hay que mandar User-Agent propio. Con el de Python por defecto: 429.
2. Seis peticiones RSS seguidas -> 429 en cinco, y el bloqueo dura minutos.
   Ni siquiera con 15s de pausa entre subs se salva: el límite es por IP y
   arrastra el historial reciente. Por eso existe el camino A.
3. La búsqueda global filtrando por `subreddit:` NO sirve: Reddit ignora el
   texto buscado y devuelve posts al azar de esos subs.
4. La consulta necesita comillas. `q=ps plus` devuelve CERO resultados;
   `q="ps plus"` devuelve decenas.
"""
import os
import time
import datetime
import calendar
import urllib.parse
import requests
import feedparser

# Reddit rechaza los User-Agent genéricos. Este identifica el bot, como pide
# su documentación. A propósito NO lleva correo personal: el repo es público
# y un email en el código termina en listas de spam.
AGENTE = "psplus-alert/1.0 (+https://github.com/Naivelk/ps5Plus)"

URL_TOKEN = "https://www.reddit.com/api/v1/access_token"
URL_API = "https://oauth.reddit.com/r/%s/search"


def _a_fecha(struct_time):
    if not struct_time:
        return None
    return datetime.datetime.utcfromtimestamp(calendar.timegm(struct_time))


def _item(titulo, cuerpo, enlace, sub, autor, fecha):
    return {
        "id": "reddit:" + enlace,
        "titulo": titulo,
        "descripcion": cuerpo,
        "url": enlace,
        "fuente": "r/" + sub,
        "autor": autor,
        "fecha_dt": fecha,
        "imagen": None,
    }


# ------------------------------------------------- camino A: API con OAuth

def _token():
    """Token de solo lectura (application-only). None si no hay credenciales."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    try:
        r = requests.post(
            URL_TOKEN,
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": AGENTE},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as ex:
        print("[Reddit] No se pudo autenticar: %s" % ex)
        return None


def _por_api(token, subs, consulta, ventana, limite):
    resultados, caidos = [], []
    cabeceras = {"Authorization": "bearer " + token, "User-Agent": AGENTE}
    for sub in subs:
        try:
            r = requests.get(
                URL_API % sub,
                headers=cabeceras,
                params={"q": consulta, "restrict_sr": 1, "sort": "new",
                        "t": ventana, "limit": limite},
                timeout=25,
            )
            r.raise_for_status()
            hijos = r.json().get("data", {}).get("children", [])
        except Exception as ex:
            caidos.append(sub)
            print("[Reddit/API] r/%s: %s" % (sub, ex))
            continue

        for hijo in hijos:
            d = hijo.get("data", {})
            permalink = d.get("permalink")
            if not permalink:
                continue
            creado = d.get("created_utc")
            resultados.append(_item(
                d.get("title", ""),
                d.get("selftext", "") or "",
                "https://www.reddit.com" + permalink,
                d.get("subreddit", sub),
                d.get("author", ""),
                datetime.datetime.utcfromtimestamp(creado) if creado else None,
            ))
    return resultados, caidos


# ------------------------------------------------- camino B: RSS público

def construir_url(sub, consulta, ventana):
    """RSS de búsqueda DENTRO de un subreddit, ordenado por más reciente."""
    params = urllib.parse.urlencode({
        "q": consulta, "restrict_sr": "on", "sort": "new", "t": ventana,
    })
    return "https://www.reddit.com/r/%s/search.rss?%s" % (sub, params)


def _leer_rss(url, intentos, espera):
    """Lee un feed aguantando el rate limit. Devuelve (feed, fallo)."""
    for intento in range(intentos):
        try:
            feed = feedparser.parse(url, agent=AGENTE)
        except Exception as ex:
            return None, "error de red (%s)" % ex

        if feed.get("status") == 429:
            if intento + 1 < intentos:
                pausa = espera * (intento + 1)
                print("[Reddit] 429 (rate limit). Reintento en %ss..." % pausa)
                time.sleep(pausa)
                continue
            return None, "rate limit (429) tras %d intentos" % intentos

        if feed.bozo and not feed.entries:
            return None, "feed ilegible (%s)" % feed.get("bozo_exception")
        return feed, None
    return None, "rate limit (429)"


def _por_rss(subs, consulta, ventana, pausa, intentos, espera):
    resultados, caidos = [], []
    for i, sub in enumerate(subs):
        if i:
            time.sleep(pausa)      # sin esta pausa Reddit corta con 429
        feed, fallo = _leer_rss(construir_url(sub, consulta, ventana),
                                intentos, espera)
        if feed is None:
            caidos.append(sub)
            print("[Reddit/RSS] r/%s: %s" % (sub, fallo))
            continue
        for e in feed.entries:
            enlace = e.get("link", "")
            if not enlace:
                continue
            resultados.append(_item(
                e.get("title", ""), e.get("summary", ""), enlace, sub,
                e.get("author", ""), _a_fecha(e.get("published_parsed")),
            ))
    return resultados, caidos


# ----------------------------------------------------------------- entrada

def obtener(config):
    """Devuelve (items, errores) encontrados en Reddit."""
    cfg = config.get("reddit", {}) or {}
    subs = cfg.get("subs", [])
    consulta = cfg.get("consulta", "")
    ventana = cfg.get("ventana", "week")
    if not subs or not consulta:
        return [], []

    token = _token()
    if token:
        resultados, caidos = _por_api(
            token, subs, consulta, ventana, cfg.get("limite", 50))
        via = "API"
    else:
        resultados, caidos = _por_rss(
            subs, consulta, ventana,
            cfg.get("pausa_segundos", 15), cfg.get("intentos", 3),
            cfg.get("espera_429", 30))
        via = "RSS público"
    print("[Reddit] %d posts vía %s (%d subs caídos)"
          % (len(resultados), via, len(caidos)))

    errores = []
    # Solo avisar si se cayeron TODOS: que falle uno es normal.
    if subs and len(caidos) == len(subs):
        aviso = "Reddit: fallaron todos los subs (%s)" % ", ".join(caidos)
        if via == "RSS público":
            aviso += ". Configura REDDIT_CLIENT_ID/SECRET para usar la API."
        errores.append(aviso)
    return resultados, errores
