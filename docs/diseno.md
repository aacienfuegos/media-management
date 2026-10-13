# Diseño

El sistema visual de media-management tal como está en el CSS. Hay dos hojas
independientes que no se comparten: la del panel, `src/media_management/panel/static/panel.css`
(abreviada `panel.css`), y la de la página pública,
`src/media_management/public/static/share.css` (abreviada `share.css`). Este documento
resume las reglas; los valores exactos y el resto de las reglas están en esos ficheros.

## Tokens de color

Las dos hojas definen sus tokens en `:root` con `light-dark(claro, oscuro)`
(`panel.css:1-25`, `share.css:1-23`). Columna "Público": el nombre del token equivalente
en `share.css` cuando existe; "igual" si tiene el mismo nombre y el mismo valor.

| Token del panel | Claro | Oscuro | `panel.css` | Público | `share.css` |
|---|---|---|---|---|---|
| `--bg` | `#f6f4f0` | `#121110` | 3 | igual | 3 |
| `--surface` | `#ffffff` | `#1b1a18` | 4 | igual | 4 |
| `--surface-2` | `#efece6` | `#252320` | 5 | igual | 5 |
| `--text` | `#1d1b18` | `#ece8e1` | 6 | igual | 6 |
| `--muted` | `#6a6459` | `#a39d92` | 7 | igual | 7 |
| `--border` | `#e3ded5` | `#2f2c28` | 8 | igual | 8 |
| `--border-strong` | `#cbc4b8` | `#45413a` | 9 | igual | 9 |
| `--accent` | `#b34d12` | `#f08a4b` | 10 | igual | 10 |
| `--accent-hover` | `#8f3c0c` | `#f6a574` | 11 | igual | 11 |
| `--accent-soft` | `#fbeade` | `#3a2417` | 12 | igual | 12 |
| `--on-accent` | `#ffffff` | `#1a0d05` | 13 | igual | 13 |
| `--ok` | `#1f6b3a` | `#6fcf8e` | 14 | igual | 14 |
| `--ok-bg` | `#e4f2e8` | `#142a1c` | 15 | `--ok-soft` (mismo valor) | 15 |
| `--warn` | `#7a5a00` | `#d9b54a` | 16 | `--warn-text`, otro valor: `#5c4300` / `#e3c566` | 19 |
| `--warn-bg` | `#f7efd6` | `#2a2412` | 17 | `--warn-soft` (mismo valor) | 18 |
| `--bad` | `#b3261e` | `#ff8f86` | 18 | `--error` (mismo valor) | 16 |
| `--bad-bg` | `#fcebea` | `#3a1614` | 19 | `--error-soft` (mismo valor) | 17 |
| `--row-hover` | `#faf8f4` | `#201e1b` | 20 | no existe | |
| `--row-selected` | `#fbeade` | `#2e2016` | 21 | no existe | |
| `--shadow-color` | `rgb(40 30 15 / .08)` | `rgb(0 0 0 / .45)` | 22 | igual | 20 |

Tokens que no son de color:

| Token | Valor | Dónde |
|---|---|---|
| `--shadow` (panel) | `0 1px 2px var(--shadow-color), 0 1px 3px var(--shadow-color)` | `panel.css:23` |
| `--shadow` (público) | `0 2px 4px var(--shadow-color), 0 12px 28px var(--shadow-color)` | `share.css:21` |
| `--radius` (solo panel) | `10px` | `panel.css:24` |
| `--font-display` (solo público) | `ui-rounded, "SF Pro Rounded", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` | `share.css:22` |

## Tipografía

- **Texto**: pila `system-ui` (con `-apple-system`, `Segoe UI`, Roboto y Helvetica Neue).
  Panel a 14 px y 15 px en móvil (`panel.css:30`, `panel.css:258`); público a 17 px y
  16 px en móvil (`share.css:26`, `share.css:139`).
- **Monoespaciada** (`ui-monospace`, SFMono-Regular, Menlo, Consolas): en el panel para
  `code`, `kbd` y la clase `.name` (rutas y nombres de fichero) (`panel.css:41`); en el
  público para el código personal y su campo (`share.css:61`, `share.css:91`).
- **Títulos**: el panel usa la misma pila del texto con `h1` a 1,4 rem
  (`panel.css:37`); el público usa `--font-display` en `h1` a 1,6 rem
  (`share.css:37`).
- Cifras tabulares en tablas y tarjetas del panel (`panel.css:155`, `panel.css:182`).

## Radios y sombras

- Panel: `--radius` (10 px) en tarjetas, paneles, avisos y tablas; 6 px en botones,
  campos y pestañas; 999 px en etiquetas y chips (`panel.css:24`, `panel.css:85`,
  `panel.css:96`, `panel.css:121`, `panel.css:125`).
- Público: no hay token de radio; los valores van escritos en cada regla: 18 px en la
  tarjeta, 10 px en campos, 12 px en botones y 14 px en botones grandes y filas
  (`share.css:31`, `share.css:57`, `share.css:67`, `share.css:79`, `share.css:109`).
- Sombras: `--shadow` en cada hoja (tabla de arriba); el menú "Más" del panel escribe a
  mano la sombra larga (`panel.css:66`), y la barra de selección y el diálogo de atajos
  usan sombras propias (`panel.css:209`, `panel.css:241`).

## Puntos de corte

| Hoja | Ancho | Qué cambia | Dónde |
|---|---|---|---|
| Panel | 1000 px | El panel lateral (`.grid-side`) pasa debajo | `panel.css:248-251` |
| Panel | 800 px | `.grid2` a una columna; se ocultan las columnas `.opt` | `panel.css:252-256` |
| Panel | 640 px | Cabecera en dos filas con las pestañas debajo, se ocultan las columnas `.opt-sm`, controles más altos (2,5 rem), miniaturas más pequeñas | `panel.css:257-279` |
| Público | 560 px | Tarjeta ancha sin borde ni franja, miniaturas cuadradas, botones de fila solo con icono | `share.css:138-151` |

Las dos hojas desactivan animaciones y transiciones con `prefers-reduced-motion`
(`panel.css:280-282`, `share.css:152-155`).

## Tema

- **Panel**: sigue al sistema por defecto, con selector Sistema/Claro/Oscuro en el menú
  "Más" (`src/media_management/panel/templates/base.html:32-36`). La elección se guarda en
  `localStorage` (`mm-theme`) y se aplica como `data-theme` en `<html>` antes de pintar
  (`src/media_management/panel/static/theme.js`,
  `src/media_management/panel/static/panel.js:176-195`), que fija `color-scheme`
  (`panel.css:26-27`).
- **Público**: solo sigue al sistema; no carga `theme.js` ni tiene selector
  (`src/media_management/public/static/index.html:7-13`).

## Componentes

### Panel (`panel.css`)

| Componente | Clases | Dónde | Notas |
|---|---|---|---|
| Cabecera y navegación | `.top`, `.brand`, `.nav`, `.more` | `panel.css:53-72` | Pestaña actual con `--accent-soft` |
| Botones | `.btn` / `button` con `.primary`, `.danger`, `.solid-danger`, `.ghost`, `.sm` | `panel.css:83-95` | |
| Campos | `input`, `select`, `textarea`, `.field`, `.check`, `.inline-fields` | `panel.css:96-107` | |
| Barra de herramientas y búsqueda | `.toolbar`, `.search` (con `kbd` dentro) | `panel.css:109-115` | |
| Selector segmentado | `.segmented` | `panel.css:116-119` | Raíces en Biblioteca, Activos/Todos en Enlaces |
| Chips | `.chip`, `.chips` | `panel.css:120-123` | Carpetas de la Biblioteca |
| Etiquetas | `.tag` con `.ok`, `.warn`, `.bad`, `.accent` | `panel.css:125-130` | Las tres primeras llevan un punto delante |
| Avisos | `.banner` con `.bad`, `.warn`, `.ok`, `.info` | `panel.css:132-137` | Borde izquierdo de 4 px |
| Aviso tras una acción | `.flash` | `panel.css:138-140` | Animación `flash-in` |
| Paneles | `.panel`, `.panel.divided`, `.stack` | `panel.css:142-147` | |
| Rejillas | `.grid2`, `.grid-side` | `panel.css:148-149` | `.grid-side` con columna lateral de 22 rem |
| Tarjetas de cifras | `.stats`, `.stat`, `.stat.attention` | `panel.css:151-163` | `attention` usa los tokens de `warn` |
| Tablas | `.table-wrap`, `table`, `table.kv`, `.cell-*`, `.num` | `panel.css:170-191` | Cabecera fija; `kv` para fichas clave-valor |
| Orden por columna | `.sortable`, `.sorted-asc`, `.sorted-desc` | `panel.css:192-197` | |
| Miniaturas | `.thumb`, `.thumb.big` | `panel.css:198-199` | 80x45 px; la grande en 16:9 |
| Estados vacíos | `.empty` | `panel.css:202-207` | |
| Barra de selección | `.selbar` | `panel.css:209-212` | Fija abajo; oculta si no hay selección |
| Opciones de modo | `.choices`, `.choice`, `.reveal` | `panel.css:215-225` | Resalta la marcada con `:has()` |
| Atajos de días | `.presets` | `panel.css:226-229` | |
| Secreto que se copia | `.secret-box` | `panel.css:231-232` | URL del enlace y token nuevo |
| Solicitud | `.request-list`, `.request` | `panel.css:233-239` | Borde izquierdo con `--warn` |
| Diálogo de atajos | `dialog.shortcuts` | `panel.css:241-245` | |
| Teclas | `kbd` | `panel.css:44` | |

### Página pública (`share.css`)

| Componente | Clases | Dónde | Notas |
|---|---|---|---|
| Tarjeta | `.card`, `.card.wide`, `.center` | `share.css:30-36` | 30 rem; la ancha, 50 rem |
| Franja de película | `.card::before` | `share.css:33` | Banda de 5 px arriba, en `--accent` a trazos de 10 px con huecos de 6 px |
| Insignia de estado | `.badge` con `.b-error`, `.b-ok`, `.b-neutral`, `.b-wait` | `share.css:44-48` | Círculo con icono |
| Carga | `.loading`, `.spinner`, `.spinner.sm` | `share.css:49-52` | |
| Campos | `form`, `label`, `input`, `textarea`, `.code-input`, `.with-toggle` | `share.css:54-65` | Campos de 3 rem de alto |
| Botones | `button` con `.primary`, `.link`, `.soft`, `.block`, `.big` | `share.css:67-80` | |
| Error y nota | `.error`, `.notice` | `share.css:83-86` | `.notice` usa `--warn-soft` y `--warn-text` |
| Separador | `.divider` | `share.css:87-88` | |
| Código personal | `.code` | `share.css:91` | Monoespaciada, borde discontinuo |
| Cabecera del enlace | `.share-head`, `.mark`, `.eyebrow`, `.meta` | `share.css:34-35`, `share.css:93-96` | |
| Descargar todo | `.all`, `.zip-slot`, `.separate` | `share.css:97-106` | |
| Estado de descarga | `.status` con `.ok`, `.bad` | `share.css:100-103` | |
| Lista de ficheros | `.files`, `.file`, `.file.done`, `.single` | `share.css:108-124` | |
| Miniatura y ampliación | `.thumb`, `.thumb.icon`, `.thumb-btn`, `.zoom` | `share.css:111-112`, `share.css:125-127` | |
| Vista previa | `.preview`, `.preview-foot`, `.preview-actions` | `share.css:128-135` | Diálogo |

### Iconos

Los dos lados dibujan iconos SVG en línea con `fill="currentColor"`, sin fichero de
iconos. La marca (un fotograma de película) es el mismo trazado en
`src/media_management/panel/templates/base.html:16` y en `ICONS.mark` de
`src/media_management/public/static/share-ui.js:5`.

## Observado

No se corrige en este cambio.

- **Tokens duplicados con nombres distintos.** Las dos hojas repiten la paleta en lugar de
  compartirla, y cuatro tokens cambian de nombre con el mismo valor: `--ok-bg`/`--ok-soft`,
  `--warn-bg`/`--warn-soft`, `--bad`/`--error` y `--bad-bg`/`--error-soft`
  (`panel.css:15-19`, `share.css:15-18`).
- **Amarillo de aviso distinto.** El texto de aviso del panel (`--warn`, `#7a5a00` /
  `#d9b54a`) no es el del público (`--warn-text`, `#5c4300` / `#e3c566`)
  (`panel.css:16`, `share.css:19`).
- **`--shadow` con el mismo nombre y distinto valor** en las dos hojas
  (`panel.css:23`, `share.css:21`). La del público es la que el panel escribe a mano en el
  menú "Más" (`panel.css:66`).
- **`theme-color` fuera de la paleta.** La página pública declara `#f3f4f6` (claro) y
  `#0f1115` (oscuro) (`src/media_management/public/static/index.html:8-9`), que no son su
  `--bg` (`#f6f4f0` / `#121110`, `share.css:3`).
- **Gris de la miniatura de reserva fuera de la paleta.** El SVG que sirve el panel cuando
  no hay miniatura usa `#8b93a3` escrito en el código
  (`src/media_management/panel/catalog_routes.py:108-112`).
- **Clases sin uso.** `.banner.info` (`panel.css:136`) no aparece en ninguna plantilla del
  panel, y `.button` (`share.css:67`) no lo usa `share.js` ni `share-ui.js`.
- **El público no tiene selector de tema** y el panel sí (ver "Tema").
