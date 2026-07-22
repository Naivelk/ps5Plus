"""Filtros para ofertas y códigos de PS Plus.

Tres trabajos:
  1. Decidir si un item habla de verdad de PS Plus (no de un juego cualquiera).
  2. Separar OFERTA (descuento) de CÓDIGO GRATIS (regalo/sorteo).
  3. Puntuar el riesgo de estafa, que en el mundo de "PS Plus gratis" es alto.

Igual que en sorteos-alert, las coincidencias son por palabra completa para
que "ps5" no haga match dentro de otra cosa.
"""
import re

_PATRONES = {}


def _patron(frase):
    """Compila (y cachea) una frase como coincidencia de palabra completa."""
    p = _PATRONES.get(frase)
    if p is None:
        limpia = re.escape(frase.lower().strip())
        p = re.compile(r"(?<!\w)" + limpia + r"(?!\w)")
        _PATRONES[frase] = p
    return p


def _contiene(texto, frases):
    texto = (texto or "").lower()
    return any(_patron(f).search(texto) for f in frases if f and f.strip())


def _todo(item):
    return f"{item.get('titulo', '')} {item.get('descripcion', '')}"


# ---------------------------------------------------------------- relevancia

def es_relevante(item, palabras_psplus, palabras_suscripcion=(), senales_codigo=()):
    """Solo mira el TÍTULO, y exige DOS cosas.

    Nombrar PS Plus no basta: "Black Ops 1 & 2 on sale with PS Plus" menciona
    PS Plus pero está vendiendo un juego, no la suscripción. Así que además
    del nombre pedimos contexto de suscripción (12 month, membership, el
    nombre de un plan...) o de regalo (giveaway, código gratis).
    """
    titulo = item.get("titulo", "")
    if not _contiene(titulo, palabras_psplus):
        return False
    return (_contiene(titulo, palabras_suscripcion)
            or _contiene(titulo, senales_codigo))


def esta_excluido(item, excluir, excluir_titulo=()):
    """True si hay que descartarlo (noticias, juegos del mes, posts viejos)."""
    if _contiene(item.get("titulo", ""), excluir_titulo):
        return True
    return _contiene(_todo(item), excluir)


# ------------------------------------------------------------------ precios

_RE_MONTO = re.compile(
    r"(?P<pre>us\s*\$|usd|cop|col\s*\$|\$)?\s*"
    r"(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?P<post>usd|cop|dólares|dolares|mil)?",
    re.IGNORECASE,
)


def _normalizar(bruto):
    """Convierte el número escrito a float, aguantando los dos formatos.

    '150.000' -> 150000.0   (formato latino: el punto separa miles)
    '1,299.00' -> 1299.0    (formato anglo: la coma separa miles)
    '39.99' -> 39.99        (decimal simple)
    """
    b = bruto.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", b):
        return float(b.replace(".", ""))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", b):
        return float(b.replace(",", ""))
    return float(b.replace(",", "."))


def extraer_precio(texto):
    """Saca (valor, moneda) del texto. Devuelve (None, None) si no encuentra.

    Los posts de r/PS4Deals ponen el precio en el título casi siempre
    ("[PSN] PS Plus 12 Month Essential - $39.99"), así que esto acierta mucho.
    Nos quedamos con el monto MÁS BAJO plausible: si el título dice
    "de $79.99 a $39.99", el que importa es el segundo.
    """
    texto = texto or ""
    candidatos = []
    for m in _RE_MONTO.finditer(texto):
        pre = (m.group("pre") or "").lower().replace(" ", "")
        post = (m.group("post") or "").lower()
        if not pre and not post:
            continue                      # un número suelto no es un precio
        try:
            valor = _normalizar(m.group("num"))
        except ValueError:
            continue

        if "cop" in pre or "cop" in post or pre == "col$":
            moneda = "COP"
        elif valor > 500:
            moneda = "COP"                # nadie cobra 150.000 dólares por PS Plus
        else:
            moneda = "USD"

        if moneda == "USD" and not 1 <= valor <= 500:
            continue
        if moneda == "COP" and not 5000 <= valor <= 2_000_000:
            continue
        candidatos.append((valor, moneda))

    if not candidatos:
        return None, None
    # El precio de oferta es el más bajo de los que aparecen.
    return min(candidatos, key=lambda c: (c[1], c[0]))


def bajo_umbral(precio, moneda, umbrales, pisos=None):
    """True si el precio es un chollo REAL de 12 meses.

    El piso importa tanto como el techo: un titular como "Sony sube PS Plus a
    $10.99" trae un precio MENSUAL, y sin suelo se marcaba como chollo por ser
    menor que el umbral anual.
    """
    if precio is None or moneda is None:
        return False
    limite = umbrales.get(moneda)
    if limite is None or precio > limite:
        return False
    piso = (pisos or {}).get(moneda)
    return piso is None or precio >= piso


# ------------------------------------------------------------------- región

def detectar_region(item, precio_moneda, senales):
    """Devuelve 'US', 'CO' o None.

    Importa de verdad: los códigos de PS Plus están bloqueados por región.
    Un código de 12 meses comprado en Colombia NO se canjea en una cuenta de
    EE.UU. Por eso etiquetamos cada oferta y avisamos si no es tu región.
    """
    texto = f"{_todo(item)} {item.get('url', '')}"
    for region, frases in senales.items():
        if _contiene(texto, frases):
            return region
    if precio_moneda == "COP":
        return "CO"
    if precio_moneda == "USD":
        return "US"
    return None


# ---------------------------------------------------------------- categoría

def categoria(item, senales_codigo):
    """'codigo' (regalo/sorteo) u 'oferta' (descuento).

    Los códigos gratis son lo urgente: vuelan en minutos. Las ofertas duran
    días, así que van después en el resumen."""
    if _contiene(_todo(item), senales_codigo):
        return "codigo"
    return "oferta"


# ---------------------------------------------------------------- anti-estafa

def _dominio(url):
    m = re.match(r"https?://([^/]+)", url or "", re.IGNORECASE)
    return (m.group(1).lower().replace("www.", "") if m else "")


def evaluar(item, senales_estafa, dominios_confiables, acortadores):
    """Devuelve (nivel, motivos). nivel: 'ok' | 'duda' | 'riesgo'.

    El terreno de "PS Plus gratis" está lleno de encuestas, generadores falsos
    y phishing, así que somos más duros que en sorteos-alert: una sola señal
    fuerte ya manda el item a rojo."""
    texto = f"{_todo(item)} {item.get('url', '')}".lower()
    motivos = [s for s in senales_estafa if _patron(s).search(texto)]

    if any(a in texto for a in acortadores):
        motivos.append("usa enlace acortado")

    dom = _dominio(item.get("url", ""))
    confiable = any(dom == d or dom.endswith("." + d) for d in dominios_confiables)
    item["dominio"] = dom
    item["confiable"] = confiable

    # Un dominio conocido (playstation.com, amazon, cdkeys...) compensa una
    # señal suelta, pero no dos: nadie legítimo pide encuesta Y tarjeta.
    if confiable and len(motivos) <= 1:
        return ("ok" if not motivos else "duda"), motivos
    if len(motivos) >= 2:
        return "riesgo", motivos
    if len(motivos) == 1:
        return "riesgo" if item.get("categoria") == "codigo" else "duda", motivos
    return "ok", motivos


ETIQUETA = {
    "ok": "🟢 Parece legítimo",
    "duda": "🟡 Revísalo con cuidado",
    "riesgo": "🔴 Sospechoso (posible estafa)",
}
