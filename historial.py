"""Historial de precios de PS Plus, para avisar con criterio.

Existe por un fallo real: antes, el aviso de una oferta se recordaba para
siempre por su precio. Si PS Plus bajaba a 39.99 en Black Friday, te avisaba;
si el año siguiente volvía a bajar a ese mismo 39.99, el bot se callaba
porque "ya lo había visto". Justo el aviso que más te importaba.

La solución no es recordar precios vistos, sino mirar el MOVIMIENTO: se avisa
cuando el precio baja respecto a la última vez que lo miramos. Así:

  - Si el precio no cambia, no te escribe (aunque corra cada 30 minutos).
  - Si baja, te avisa — da igual que sea el mismo número del año pasado.
  - Y como queda el histórico, podemos decirte si es el más bajo en X meses,
    que es mucho más útil que compararlo con un número fijo.
"""
import io
import os
import json
import datetime

ARCHIVO = "state/precios.json"
MAX_POR_PLAN = 200          # ~ años de historia; evita que el archivo crezca sin fin


def clave(region, plan, meses):
    return "%s:%s:%d" % (region, plan, meses)


def cargar(ruta=ARCHIVO):
    try:
        with io.open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def guardar(datos, ruta=ARCHIVO):
    carpeta = os.path.dirname(ruta)
    if carpeta:
        try:
            os.makedirs(carpeta)
        except OSError:
            pass                      # ya existe
    with io.open(ruta, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True))


def ultimo(datos, k):
    """Última observación de ese plan, o None si nunca lo hemos visto."""
    serie = datos.get(k) or []
    return serie[-1] if serie else None


def registrar(datos, k, precio, base, hoy=None):
    """Anota el precio solo si cambió respecto a la última observación.

    Si anotáramos cada pasada tendríamos 48 entradas iguales al día y el
    historial dejaría de servir para nada.
    """
    hoy = hoy or datetime.date.today().isoformat()
    serie = datos.setdefault(k, [])
    if serie and serie[-1].get("p") == precio and serie[-1].get("b") == base:
        return False
    serie.append({"f": hoy, "p": precio, "b": base})
    del serie[:-MAX_POR_PLAN]
    return True


def _fecha(texto):
    try:
        return datetime.date(*[int(x) for x in texto.split("-")[:3]])
    except (ValueError, TypeError, AttributeError):
        return None


def minimo(datos, k, dias=None, hoy=None):
    """Precio más bajo registrado; si pasas `dias`, solo en esa ventana."""
    serie = datos.get(k) or []
    if not serie:
        return None
    if dias is None:
        precios = [o["p"] for o in serie if o.get("p") is not None]
        return min(precios) if precios else None

    hoy = hoy or datetime.date.today()
    desde = hoy - datetime.timedelta(days=dias)
    precios = [o["p"] for o in serie
               if o.get("p") is not None and (_fecha(o.get("f")) or hoy) >= desde]
    return min(precios) if precios else None


def meses_de_historia(datos, k, hoy=None):
    """Cuántos meses de datos tenemos de ese plan (para no prometer de más)."""
    serie = datos.get(k) or []
    if not serie:
        return 0
    primera = _fecha(serie[0].get("f"))
    if not primera:
        return 0
    hoy = hoy or datetime.date.today()
    return max(0, int((hoy - primera).days / 30))
