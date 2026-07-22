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

## Aviso importante sobre regiones

Los códigos de PS Plus están **bloqueados por región**. Un código de 12 meses
comprado en Colombia no funciona en una cuenta PSN de EE.UU. y viceversa.
Configura `mi_region` en `config.yaml` y el bot marcará con ⚠️ las ofertas que
no puedes usar. No las oculta, por si algún día abres una cuenta de esa región.

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

## Ajustes que vas a querer tocar

Todo está en `config.yaml`, comentado en español:

| Ajuste | Para qué |
|---|---|
| `mi_region` | `US` o `CO` — marca lo que no te sirve |
| `precio_objetivo` | A partir de qué precio te avisa como 🔥 chollo |
| `ocultar_sospechosos` | `true` si no quieres ver ni los 🔴 |
| `reddit.subs` | Qué subreddits vigila |
| `senales_estafa` | Añade las frases basura que te vayan llegando |

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
