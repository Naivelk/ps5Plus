# psplus-alert

Bot que vigila ofertas y códigos gratis de **PlayStation Plus** y te avisa por
Telegram. Hermano pequeño de [BotSorteo](https://github.com/Naivelk/BotSorteo):
reusa la misma idea (fuentes → filtro → aviso) aplicada a un solo producto.

## Qué hace y qué NO hace

**Sí:** revisa Reddit y feeds oficiales cada 30 min, extrae el precio del
título, lo compara con tu precio objetivo, detecta si es tu región y puntúa el
riesgo de estafa. Te manda un resumen ordenado por urgencia.

**No:** no entra a tu cuenta PSN ni canjea códigos automáticamente. Eso
requeriría guardar tu contraseña, choca con el captcha de Sony y, si acumulas
intentos fallidos, te pueden bloquear la cuenta — perderías toda tu biblioteca
por ahorrarte 15 segundos de copiar y pegar. **Tú canjeas a mano.**

## El truco que más ahorra: saldo PSN con descuento

Esperar a que Sony rebaje PS Plus no suele ser el mejor negocio. Comprar
**saldo PSN con descuento** sí, y hay ofertas así todo el año.

Ejemplo real (2026-08-02): una tarjeta de 100 USD por 273.250 COP. Al cambio
real de 3.203 COP/USD eso son 85,31 USD por 100 USD de saldo — un **~15% de
descuento** sobre **cualquier** compra de la store. En 12 meses eso son unos
37.600 COP de ahorro en Essential y 75.300 en Premium.

Haz siempre la cuenta con el cambio del día: Eneba muestra los precios en
pesos usando su propia conversión, que no es la del mercado.

Por eso el bot vigila las tarjetas aparte, en su propia sección 💳. Se
configuran en `palabras_tarjeta_psn` (para noticias y Reddit) y en
`eneba.productos` (para precios exactos).

## Ofertas que son noticia, no una más del montón

`itad_source.py` usa [IsThereAnyDeal](https://isthereanydeal.com) y es la
única fuente capaz de decir si un precio **es el más bajo de la historia**.
Solo deja pasar lo que cumple las tres a la vez:

1. **Nuevo mínimo histórico** (`flag: N`) — nunca había estado tan barato;
2. **-60% o más** (`descuento_minimo`);
3. **buena nota** (`nota_minima: 75`) — un juego malo al -90% sigue siendo malo.

De paso cubre Steam, Fanatical, G2A, Kinguin, Instant Gaming y
GreenManGaming en una sola llamada, sin scrapear ninguna (esas webs bloquean
scripts; ITAD ya las recopila).

Necesita el secret `ITAD_API_KEY`, gratis en
[isthereanydeal.com/apps](https://isthereanydeal.com/apps/). **Sin la key
esta fuente se calla y el resto del bot funciona igual.**

Si un juego no tiene nota, se deja pasar: no tener dato significa "no sé", no
"es malo".

## Lo que sigues manda

`seguimiento` en el config es la lista de lo que de verdad te importa (PS
Plus, GTA VI, FIFA 27…). Lo que esté ahí:

- entra **aunque el descuento sea flojo** — un -20% en GTA VI vale más que un
  -85% de un juego que no piensas comprar;
- sale **el primero** del resumen, con 🎯.

## Arbitraje entre regiones

El mismo juego cuesta distinto según la región de la key. Medido el
2026-08-02 en Eneba: **GTA VI (PS5) a 69,22 US$ la de India contra 82,53 la
de EE.UU.** — un 16% menos.

Se configura en `eneba.comparar`: pones las variantes regionales del mismo
juego y cuál es tu `referencia` (la región que ya puedes usar). El bot avisa
cuando otra región baja de `ahorro_minimo_pct`.

**Léelo con cuidado antes de comprar:** una key de otra región normalmente
exige una cuenta PSN de esa misma región. Es una compra aparte, no un
descuento en tu cuenta de siempre. El aviso lo recuerda cada vez, y sólo
compara precios dentro de la misma moneda — convertir monedas aquí sería
inventarse un tipo de cambio.

## Ofertones de cualquier juego

Además de PS Plus, el bot trae lo nuevo de r/GameDeals y r/PS4Deals y se
queda **solo con lo que supere `ofertas_juegos.descuento_minimo`** (por
defecto 80%). Ese umbral es la única defensa contra el ruido: esos subs
publican decenas de ofertas al día. Si ves poco, bájalo a 70; si ves
demasiado, súbelo a 85.

## Eneba

Sí se vigila, pero necesita un navegador de verdad. Su web responde a `curl`
y devuelve 200, pero **el HTML no trae los precios**: la página los pinta con
JavaScript. Se comprobó mirando las peticiones del navegador — el
"Desde: 93,43 US$" que se ve en pantalla no está por ningún lado en lo que
baja `curl`.

Por eso `eneba_watch.py` usa Playwright y corre en **su propio workflow**
(`.github/workflows/eneba.yml`), cada 6 horas. Va aparte a propósito: es la
pieza más frágil del proyecto y no debe poder tumbar los avisos de PS Store,
Reddit y noticias. Si Eneba cambia la página o bloquea el bot, te llega un
aviso y el resto sigue funcionando.

**Para añadir un producto:** busca lo que quieras en Eneba, copia la URL de
la página del producto y pégala en `eneba.productos` del `config.yaml`. El
`objetivo` es opcional.

La extracción va por texto (`Desde: <precio>`), no por selectores CSS: las
clases de Eneba cambian en cada despliegue suyo, ese texto no.

## Aviso importante sobre regiones

Los códigos de PS Plus están **bloqueados por región**. Un código de 12 meses
comprado en Colombia no funciona en una cuenta PSN de EE.UU. y viceversa.

En `mis_regiones` van todas las cuentas que tengas (`US`, `CO`, o ambas). Con
más de una, el bot deja de tratarlo como un problema y te dice **en cuál
canjear** cada oferta. Si una oferta no sirve para ninguna, la marca con ⚠️
pero no la oculta.

## Puesta en marcha

1. Crea un bot en Telegram con [@BotFather](https://t.me/BotFather) y copia el
   token. (O reusa el de sorteos-alert.)
2. Consigue tu `chat_id` escribiéndole a [@userinfobot](https://t.me/userinfobot).
3. Para probar en tu PC:

```bash
cd psplus-alert && pip install -r requirements.txt && cp .env.example .env
```

   Rellena `.env` y corre:

```bash
python main.py
```

4. Para que corra solo: sube el repo a GitHub y añade `TELEGRAM_BOT_TOKEN` y
   `TELEGRAM_CHAT_ID` en *Settings → Secrets and variables → Actions*.

## Cuándo te avisa de un precio

No compara contra un número fijo, sino contra **lo que costaba la última vez**
(`historial.py`). Así:

- Si el precio no cambia, no te escribe, aunque corra cada media hora.
- Si baja, te avisa — aunque sea el mismo precio del año pasado.
- Si es el más bajo del histórico, te lo dice.

Esto arregla un bug real: antes el aviso se recordaba por su precio, así que
el Black Friday de este año te llegaba y el del año siguiente al mismo precio
**no**, porque el bot creía haberlo avisado ya.

Los precios quedan en `state/precios.json`, que crece solo cuando el precio
cambia de verdad.

## El resumen semanal

Los domingos te manda los precios actuales y los mínimos vistos. No es
decorativo: el bot calla cuando no hay ofertas, y sin ese mensaje no habría
forma de distinguir "no hay nada esta semana" de "lleva tres semanas roto".
Si el domingo no llega nada, algo pasa. Se ajusta en `latido` del config.

## Pruebas

```bash
python smoke_test.py
```

No usa red, tarda segundos, y corre solo en cada push (`.github/workflows/ci.yml`).
Cubre lo que de verdad se ha roto aquí alguna vez: que `config.yaml` sea YAML
válido con las claves que el código espera, que la estafa que se coló siga
saliendo roja, que una oferta repetida vuelva a avisar, y que afinar el ruido
no se lleve por delante las ofertas buenas.

Si tocas `config.yaml` y el CI se pone rojo, ahí tienes el motivo antes de
que el bot deje de avisarte en silencio.

## Ajustes que vas a querer tocar

Todo está en `config.yaml`, comentado en español:

| Ajuste | Para qué |
|---|---|
| `mi_region` | `US` o `CO` — marca lo que no te sirve |
| `precio_objetivo` | A partir de qué precio te avisa como 🔥 chollo |
| `ocultar_sospechosos` | `true` si no quieres ver ni los 🔴 |
| `reddit.subs` | Qué subreddits vigila |
| `senales_estafa` | Añade las frases basura que te vayan llegando |
| `latido.dia` | Qué día llega el resumen (0=lunes … 6=domingo) |
| `dias_recordar` | Cuántos días recuerda una noticia ya avisada |

### Precios oficiales del PlayStation Store

`store_source.py` lee las nueve combinaciones (Essential/Extra/Premium ×
1/3/12 meses) directo del Store, con precio de lista y precio rebajado. Solo
avisa cuando hay **rebaja real** o cuando los 12 meses bajan de tu objetivo,
así que en semanas normales no dice nada.

Otras tiendas **no se pueden consultar**, y está comprobado, no supuesto:
G2A devuelve 403, Kinguin y Slickdeals están tras Cloudflare, y Eneba
responde 200 pero arma la página en JavaScript, así que el HTML llega sin
productos ni precios.

**Limitación de región:** no se puede forzar por URL. Pedir `es-co` devuelve
los mismos precios en USD que `en-us`, porque PS Store va por geolocalización
de la IP y GitHub Actions corre en EE.UU. Los precios en pesos llegan por los
feeds de noticias colombianas.

### Si Reddit falla, el bot sigue funcionando

Reddit es la mejor fuente para precios exactos, pero es la más frágil: corta
con 429 y crear una app de API no siempre está disponible. Si falla, quedan
los seis feeds RSS (Google News y el blog de PlayStation), que **no tienen
rate limit** y son los que en las pruebas trajeron los mejores titulares.
El bot te avisa en el mensaje cuando Reddit se cae, así sabes qué está pasando.

Ojo con otras tiendas: CDKeys, Eneba y Slickdeals están tras Cloudflare y
devuelven 403 a cualquier script. Por eso no se scrapean directamente.

### Ojo al añadir subreddits

Cada sub es una petición HTTP, y Reddit corta con **429 (rate limit)** con
facilidad: seis peticiones seguidas dieron 429 en cinco, y el bloqueo duró
más de un minuto. Por eso hay solo tres subs y una pausa de 15s entre ellos.
Si añades más, sube `pausa_segundos`.

Otras dos cosas que se comprobaron probando, por si las tocas:

- La consulta **necesita comillas**. `q=ps plus` devuelve cero resultados;
  `q="ps plus"` devuelve decenas.
- Buscar en todo Reddit filtrando con `subreddit:X OR subreddit:Y` **no
  funciona**: Reddit ignora el texto y devuelve posts al azar de esos subs.

## Cómo leer los avisos

- 🎁 **Códigos gratis** — arriba del todo, son los que vuelan.
- 🔥 **Bajo tu precio objetivo** — chollos reales.
- 💲 **Otras ofertas** — descuentos que no llegan a tu umbral.

Y el semáforo de confianza: 🟢 parece legítimo · 🟡 revísalo · 🔴 sospechoso.

## Realidad sobre los códigos gratis

Dos cosas que conviene saber antes de emocionarse:

1. **Casi todo lo que dice "PS Plus gratis" en internet es fraude**: encuestas,
   generadores falsos y phishing. Los generadores de códigos no existen — los
   códigos se validan contra la base de datos de Sony, no siguen un patrón.
   Por eso `code_filter.py` es más duro aquí que en sorteos-alert.
2. **Lo legítimo existe pero es escaso**: promos oficiales de PlayStation,
   sorteos de creadores conocidos, y la prueba oficial de 7 días (una vez por
   cuenta). El bot te pone por delante de la mayoría, no te garantiza nada.

Donde el bot sí gana claro es en las **ofertas**: los códigos de 12 meses de
Essential se apilan hasta 3 años, así que cuando avise de un buen precio puedes
comprar varios y olvidarte del tema por años.
