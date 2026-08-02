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


def es_seguido(item, seguimiento):
    """True si el título nombra algo de tu lista de seguimiento.

    Lo que está aquí se salta el umbral de descuento y sale primero: si no,
    un -20% en el juego que llevas meses esperando queda enterrado bajo un
    -85% de algo que no piensas comprar.
    """
    return _contiene(item.get("titulo", ""), seguimiento)


def es_tarjeta_psn(item, palabras_tarjeta):
    """True si el título habla de saldo/tarjeta PSN.

    Merece categoría propia porque suele ser mejor negocio que esperar una
    rebaja de PS Plus: una tarjeta de 100 USD al 30% de descuento abarata
    CUALQUIER compra de la store, suscripción incluida, y hay ofertas así
    todo el año.
    """
    return _contiene(item.get("titulo", ""), palabras_tarjeta)


def esta_excluido(item, excluir, excluir_titulo=()):
    """True si hay que descartarlo (noticias, juegos del mes, posts viejos)."""
    if _contiene(item.get("titulo", ""), excluir_titulo):
        return True
    return _contiene(_todo(item), excluir)


# Alguien contando su problema o preguntando algo. En los subs de PS Plus es
# la mayoría del contenido, y no es una oferta por mucho que nombre PS Plus.
_ARRANQUES_PREGUNTA = (
    "is there", "is it", "are there", "how do", "how can", "how to",
    "can i", "can you", "should i", "does anyone", "did anyone", "anyone",
    "why is", "why does", "why did", "what happens", "what is", "help",
    "question", "psa", "i cancelled", "i canceled", "i bought", "i just",
    "i have", "i can't", "i cant", "my ps plus", "my playstation",
    "my subscription", "alguien sabe", "cómo puedo", "como puedo",
    "me pueden", "tengo un problema", "ayuda",
)


def es_pregunta_o_queja(item):
    """True si el título es una consulta personal en vez de una oferta.

    Si trae un precio no lo descartamos: "PS Plus 12m $39.99, worth it?" sí
    interesa aunque acabe en interrogación.
    """
    titulo = (item.get("titulo") or "").strip().lower()
    if not titulo:
        return False
    if any(titulo.startswith(p) for p in _ARRANQUES_PREGUNTA):
        return True
    return "?" in titulo


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


# "75% off", "-80%", "(90% de descuento)", "descuento del 85%"
_RE_DESCUENTO = re.compile(
    r"(?:-\s*)?(\d{1,3})\s*%\s*(?:off|de\s+descuento|descuento|dto)?"
    r"|descuento\s+del\s+(\d{1,3})\s*%", re.IGNORECASE)


def extraer_descuento(texto):
    """Mayor porcentaje de descuento que aparezca en el texto, o None.

    Nos quedamos con el mayor porque los títulos de ofertas suelen mezclar
    varios ("70% off, hasta 85% en la saga"): el que engancha es el grande.
    """
    mejores = []
    for m in _RE_DESCUENTO.finditer(texto or ""):
        bruto = m.group(1) or m.group(2)
        try:
            valor = int(bruto)
        except (TypeError, ValueError):
            continue
        if 1 <= valor <= 99:          # 100% no es descuento, es regalo o error
            mejores.append(valor)
    return max(mejores) if mejores else None


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


# Los sitios de spam SEO meten un identificador aleatorio en el titular, tipo
# "FREE PS Plus CODES Tutorial 2026 (vd3UbCkcTD)". Ningún medio real hace eso.
_RE_TOKEN_SPAM = re.compile(r"\(\s*[A-Za-z0-9]{8,}\s*\)")


def evaluar(item, senales_estafa, dominios_confiables, acortadores):
    """Devuelve (nivel, motivos). nivel: 'ok' | 'duda' | 'riesgo'.

    El terreno de "PS Plus gratis" está lleno de encuestas, generadores falsos
    y phishing, así que somos más duros que en sorteos-alert: una sola señal
    fuerte ya manda el item a rojo."""
    texto = f"{_todo(item)} {item.get('url', '')}".lower()
    motivos = [s for s in senales_estafa if _patron(s).search(texto)]

    if any(a in texto for a in acortadores):
        motivos.append("usa enlace acortado")

    if _RE_TOKEN_SPAM.search(item.get("titulo", "")):
        motivos.append("código aleatorio en el título (spam SEO)")

    dom = _dominio(item.get("url", ""))
    confiable = any(dom == d or dom.endswith("." + d) for d in dominios_confiables)
    item["dominio"] = dom
    item["confiable"] = confiable

    if len(motivos) >= 2:
        nivel = "riesgo"
    elif len(motivos) == 1:
        nivel = "riesgo" if item.get("categoria") == "codigo" else "duda"
    else:
        nivel = "ok"

    # Un dominio conocido (playstation.com, amazon, cdkeys...) compensa una
    # señal suelta, pero no dos: nadie legítimo pide encuesta Y tarjeta.
    if confiable and len(motivos) <= 1:
        nivel = "ok" if not motivos else "duda"

    # Regla dura, aprendida de un fallo real: un titular de spam SEO
    # ("FREE PS Plus CODES Tutorial 2026 (No Trial)") salió marcado 🟢 porque
    # no disparaba ninguna señal concreta. Un "código gratis" alojado en un
    # dominio desconocido NUNCA puede ser verde: como mucho, amarillo.
    if item.get("categoria") == "codigo" and not confiable and nivel == "ok":
        nivel = "duda"
        motivos.append("código gratis en un sitio no conocido")

    return nivel, motivos


ETIQUETA = {
    "ok": "🟢 Parece legítimo",
    "duda": "🟡 Revísalo con cuidado",
    "riesgo": "🔴 Sospechoso (posible estafa)",
}
