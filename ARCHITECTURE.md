# Arquitectura

La app está organizada en capas: cada módulo sólo depende de los de una capa
más baja. Esa regla es lo que permite testear la lógica sin abrir ventanas
(`tkinter`/`pystray` no hacen falta para la mayoría de los tests) y es la razón
por la que hay muchos archivos chicos en vez de un `app.py` gigante.

## Capas (de abajo hacia arriba)

| Capa | Módulo | Responsabilidad |
|---|---|---|
| 0 | `platform/` | Único lugar que sabe en qué sistema operativo corre. Registro de inicio, área de trabajo, notificaciones, hover de la bandeja, flags de subprocess. `_windows.py`, `_posix.py`, `_darwin.py` implementan la misma interfaz; `__init__.py` elige. |
| 1 | `paths.py` | Ubicación de los archivos en disco (`%APPDATA%\ClaudeUsage\`) y de los recursos empaquetados. |
| 1 | `jsonstore.py` | Lectura/escritura de JSON de forma atómica (temp + rename) y manejo de archivos corruptos. |
| 1 | `theme.py` | Paletas de color (oscuro/claro) y fuentes por rol. |
| 1 | `quotas.py` | Nombres de las ventanas de cuota (`session`, `weekly`, `weekly_fable`) y sus etiquetas. Fuente única para que el tooltip, el popup y las alertas llamen a cada cuota igual. |
| 1 | `formatting.py` | Convierte datos crudos en texto para mostrar (porcentajes, fechas, tooltip). Sin dependencias de UI. |
| 2 | `config.py` | Modelo de perfiles: alta, edición, reordenamiento, borrado, identidad estable por `id`, semilla de primer arranque. |
| 2 | `settings.py` | Preferencias del usuario (tema, tamaño de letra, colores hex, alertas) y su serialización. |
| 2 | `alerts.py` | Máquina de estados de las alertas: edge-triggered por (perfil × ventana × umbral), re-armado al reiniciarse una cuota, y cooldown. Es pura (sin reloj ni I/O) para poder testearla. |
| 2 | `budget.py` | El techo de uso que un agente no debe pasar: cuál es el porcentaje que manda, la política pura de permitir / avisar / bloquear, y el store por sesión (`CLAUDE_CODE_SESSION_ID`). |
| 2 | `icons.py` | Dibuja el icono de la bandeja y de la ventana con los colores del tema actual. |
| 2 | `logging_setup.py` | Configura el log a `%APPDATA%\ClaudeUsage\claude-usage.log` y captura excepciones no manejadas (clave con `--windowed`, que descarta la consola). |
| 3 | `api.py` | Habla con la API de uso de Anthropic y maneja backoff / rate limiting. |
| 3 | `accounts.py` | De qué instalación de Claude se informa cuando no hay un usuario a quien preguntarle. Lee `CLAUDE_CONFIG_DIR`. |
| 4 | `ui/` | Todas las ventanas. Ver abajo. |
| 5 | `app.py` | El orquestador: arma el menú de la bandeja, la cola de UI, el loop de polleo y conecta todo. No contiene lógica de negocio ni construcción de widgets. |
| 5 | `mcp_server/` | Segundo punto de entrada: expone el uso como un tool MCP para que un agente lo consulte. Ver abajo. |
| 5 | `hooks/` | Tercer punto de entrada: el hook que el harness corre y que **frena** al agente al llegar al techo. Ver abajo. |

## `ui/`

| Módulo | Qué es |
|---|---|
| `widgets.py` | Piezas reutilizables (botones, filas de progreso, icono de ventana, vista con scroll). |
| `scroll.py` | Aritmética del scroll (pura, sin `tkinter`): asegura que ninguna ventana supere el alto de la pantalla. |
| `dismiss.py` | Cierre al hacer clic afuera, con alcance seguro para varias ventanas flotantes. |
| `hover.py` / `hover_popup.py` | Detección de hover sobre el icono de la bandeja y el tooltip propio que muestra. |
| `popup.py` | Popup de uso. |
| `main_window.py` | Ventana principal con pestañas de Perfiles y Configuración. |
| `profiles_window.py` / `settings_window.py` / `colors.py` | Contenido de cada pestaña. `colors.py` es la lógica pura de "color mostrado" vs "color fijado por el usuario". |

## `mcp_server/`

Un segundo frontend sobre el mismo núcleo, no una segunda implementación.
`app.py` dibuja el uso para una persona que mira un icono; `mcp_server/` lo
devuelve para un modelo que decide si le da la nafta para otro turno. Ninguno
conoce al otro, y la lógica de fetch, caché y backoff se escribe una sola vez,
en `api.py`.

| Módulo | Qué es |
|---|---|
| `report.py` | Puro: convierte el payload de `api.fetch_usage` en el objeto que lee el modelo. Sin reloj propio (`now` se pasa) ni I/O. |
| `jsonrpc.py` | El cable: JSON-RPC 2.0 por stdin/stdout, una trama por línea. Sólo transporte. |
| `server.py` | Declara los dos tools (`get_claude_usage`, `set_usage_budget`) y ata los handlers. |

De qué cuenta informa lo decide `accounts.py` (L3), que empezó acá adentro con
un solo consumidor y tuvo que bajar cuando `hooks/` fue el segundo.

Está en la capa 5, al lado de `app.py`, no en la 4. Ambos son puntos de entrada
que componen capas de abajo, así que el gate de layering prohíbe una arista
entre ellos **en las dos direcciones**: la bandeja no puede depender de un
server de fondo, y el server no puede necesitar que la bandeja esté corriendo.

Dos decisiones que conviene no revertir sin leer los docstrings:

- **Sin el SDK de `mcp`.** El protocolo que se usa son cuatro métodos. Escrito
  contra la stdlib, el server arranca con un intérprete de sistema pelado, sin
  virtualenv ni paso de instalación — y `requirements.txt` sigue siendo la
  entrada del build del `.exe`, sin dependencias que ese build no necesita.
- **Sólo informa, no frena.** Nada de lo que devuelve un tool MCP es
  vinculante: el modelo lo lee y decide. Cortar un turno de verdad es otro
  mecanismo (un hook que corre el harness) y a propósito no vive acá.

`accounts.py`, `report.py` y `jsonrpc.py` están en `CLEAN_MODULES` de
`test_import_hygiene.py`: resolver una cuenta, armar el reporte y hablar
JSON-RPC tienen que seguir funcionando sin `requests`, que entra sólo por
`server.py` a través de `api.py`.

## `hooks/`

Lo que el MCP no puede hacer. El server le *dice* al agente dónde está la
cuota; nada de esa respuesta es vinculante y un agente que decide seguir,
sigue. Este paquete es la otra mitad: un hook que corre **el harness**, fuera
del alcance del modelo, y que rechaza el turno cuando la cuenta pasa el techo.

| Módulo | Qué es |
|---|---|
| `cache.py` | La cifra de uso, cacheada en disco. El harness levanta un intérprete nuevo por evento, así que la caché en memoria de `api.py` vale cero acá. |
| `gate.py` | El veredicto y el JSON exacto que cada evento espera. |

Enganchado a dos eventos, porque frenan cosas distintas y ninguno solo alcanza:

- **`UserPromptSubmit`** dispara una vez por turno, incluidas las vueltas que
  inyecta un loop programado. Ahí va el aviso — es el único momento en que el
  agente está *entre* trabajos y todavía puede guardar — y también el bloqueo.
- **`PreToolUse`** dispara antes de cada tool. Existe porque un turno autónomo
  puede durar mucho y `UserPromptSubmit` no vuelve a disparar hasta que
  termine. Sin esto, un agente ya pasado del techo sigue gastando hasta que
  decida parar solo, que es justo la suposición que este paquete elimina.

Tres invariantes que no conviene romper:

- **Falla abierto, siempre.** Sin presupuesto, uso desconocido, API caída,
  archivo corrupto, excepción adentro del gate: todo termina en `allow` y
  exit 0. Un gate que falla cerrado te deja afuera de la sesión que
  necesitarías para desbloquearlo. Gastar de más se recupera; no poder
  escribir, no.
- **La escotilla vive en el prompt.** `usage-override` pasa el turno sin
  tocar la red. Tiene que estar en el prompt porque, una vez alcanzado el
  techo, todo camino para subirlo pasa por un prompt que este mismo hook
  bloquearía — un candado con la llave adentro no es una medida de seguridad.
- **El token no lleva `!`.** Un `!` inicial es el prefijo de shell de Claude
  Code: el prompt se va a bash y el hook nunca lo ve. La posición más natural
  para escribir la escotilla era la única en la que no podía funcionar. Sin
  prefijo, además, el token queda contenido en `/usage-override`, así que el
  slash command *es* la escotilla y hay una sola cosa que recordar. El costo
  es que un mensaje que mencione la palabra al pasar también abre el gate —
  aceptable para un compuesto con guión que nadie escribe por accidente.
- **El alcance es la sesión, no la máquina.** Claude Code exporta
  `CLAUDE_CODE_SESSION_ID` a todo proceso que arranca y un subagente hereda el
  del padre, así que "este agente y lo que él levantó" es una sola clave que el
  escritor (el tool MCP) y el lector (el hook) ven igual. Verificado leyendo
  `/proc/<pid>/environ` de un server MCP hijo vivo y el `session_id` de un
  evento `PreToolUse` real: los tres coinciden. Un archivo por sesión, no un
  mapa compartido, porque `write_json_atomic` reemplaza el archivo entero y dos
  sesiones fijando techo a la vez serían una carrera con un perdedor silencioso.
- **`hooks/` no puede importar `mcp_server/`.** Los dos son capa 5. Por eso
  `accounts.py` bajó a L3 y `budget.py` está en L2: la cifra que se le muestra
  al agente y la que lo frena tienen que ser la misma función, o el día que
  discrepen va a ser a las tres de la mañana.

## Por qué

`app.py` era un único archivo de ~1000 líneas que mezclaba paleta, dibujo,
lógica de dominio, integración con Windows y dos ventanas. Separarlo en capas
con dependencias hacia abajo hace que:

- La lógica testeable (config, alertas, formato, scroll) no dependa de la UI.
- Todo lo específico de Windows viva en un solo lugar (`platform/`), lo que deja
  el camino listo para un port a Linux sin tocar el resto.
- Un cambio de comportamiento tenga un hogar obvio en vez de sumar líneas a un
  archivo que ya nadie podía revisar.
