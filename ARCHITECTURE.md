# Arquitectura

La app está organizada en capas: cada módulo sólo depende de los de una capa
más baja. Esa regla es lo que permite testear la lógica sin abrir ventanas
(`tkinter`/`pystray` no hacen falta para la mayoría de los tests) y es la razón
por la que hay muchos archivos chicos en vez de un `app.py` gigante.

## Capas (de abajo hacia arriba)

| Capa | Módulo | Responsabilidad |
|---|---|---|
| 0 | `platform/` | Único lugar que sabe en qué sistema operativo corre. Registro de inicio, área de trabajo, notificaciones, hover de la bandeja, flags de subprocess, lectura del almacén de credenciales del sistema y dueño del event loop de la bandeja. `_windows.py`, `_posix.py`, `_darwin.py` implementan la misma interfaz; `__init__.py` elige. |
| 1 | `paths.py` | Ubicación de los archivos en disco (`%APPDATA%\ClaudeUsage\` en Windows, `~/Library/Application Support/ClaudeUsage/` en macOS) y de los recursos empaquetados. |
| 1 | `jsonstore.py` | Lectura/escritura de JSON de forma atómica (temp + rename) y manejo de archivos corruptos. |
| 1 | `theme.py` | Paletas de color (oscuro/claro) y fuentes por rol. |
| 1 | `providers/` | Un adapter por CLI (Claude Code, Codex, ...). Sabe dónde viven las credenciales, qué request hace falta para leer el uso y cómo mapear la respuesta a las ventanas de cuota compartidas. **Es la única capa que conoce un servicio concreto.** |
| 1 | `quotas.py` | Nombres de las ventanas de cuota (`session`, `weekly`, `weekly_fable`) y sus etiquetas. Fuente única para que el tooltip, el popup y las alertas llamen a cada cuota igual. |
| 1 | `formatting.py` | Convierte datos crudos en texto para mostrar (porcentajes, fechas, tooltip). Sin dependencias de UI. |
| 2 | `config.py` | Modelo de perfiles: alta, edición, reordenamiento, borrado, identidad estable por `id`, semilla de primer arranque. |
| 2 | `settings.py` | Preferencias del usuario (tema, tamaño de letra, colores hex, alertas) y su serialización. |
| 2 | `alerts.py` | Máquina de estados de las alertas: edge-triggered por (perfil × ventana × umbral), re-armado al reiniciarse una cuota, y cooldown. Es pura (sin reloj ni I/O) para poder testearla. |
| 2 | `budget.py` | El techo de uso que un agente no debe pasar: cuál es el porcentaje que manda, la política pura de permitir / avisar / bloquear, y el store por sesión (`CLAUDE_CODE_SESSION_ID`). |
| 2 | `icons.py` | Dibuja el icono de la bandeja y de la ventana con los colores del tema actual. |
| 2 | `logging_setup.py` | Configura el log a `%APPDATA%\ClaudeUsage\claude-usage.log` y captura excepciones no manejadas (clave con `--windowed`, que descarta la consola). |
| 3 | `api.py` | Ejecuta el pedido que arma el adapter y maneja cache, backoff y rate limiting. Es agnóstico del proveedor. |
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
| `button_state.py` | Lo que decide `widgets.FlatButton` (pura, sin `tkinter`): qué superficie corresponde a cada estado, si un release cuenta como clic, y qué opciones de `configure()` responde el botón en vez de reenviar. El botón se dibuja a mano porque en macOS Tk pinta el `tk.Button` nativo e ignora todos los colores. |
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
  del padre, así que "este agente y lo que él levantó" es una sola clave. Un
  archivo por sesión, no un mapa compartido, porque `write_json_atomic`
  reemplaza el archivo entero y dos sesiones fijando techo a la vez serían una
  carrera con un perdedor silencioso.
- **Escritor y lector NO siempre ven el mismo id.** Verificado leyendo
  `/proc/<pid>/environ` de un server MCP hijo vivo y el `session_id` de un
  evento `PreToolUse` real: coinciden en una sesión recién arrancada, pero una
  sesión *resumida* (`--resume`, o retomar una conversación vieja) rompe esa
  igualdad. La conversación conserva su id original -- el que lee el hook
  directamente del evento -- pero Claude Code levanta un proceso de server MCP
  nuevo, con `CLAUDE_CODE_SESSION_ID` seteado a un id propio, distinto. El
  server MCP (`set_usage_budget`) sólo ve ese segundo id, así que un techo que
  fija desde una sesión resumida queda archivado bajo un id que el hook nunca
  busca. El hook es el único lugar que tiene los dos ids a la vez -- el evento
  le da uno y el entorno el otro -- así que graba la correspondencia (un
  archivo por id de entorno, en `aliases/`) cada vez que difieren, y el store
  de `budget.py` la sigue -- encadenando varios saltos si hace falta, con un
  tope de saltos y detección de ciclos para no colgarse ante un mapa
  corrupto -- pero *sólo* cuando el llamador no nombra un id explícito. Un id
  que un llamador ya nombra (el que el hook le pasa a `load_budget` al leer,
  por ejemplo) nunca se redirige: si se tradujera también ese caso, un id que
  alguna vez sirvió como id de entorno de otra sesión podría desviar
  silenciosamente la lectura o la escritura de una sesión que no tiene nada
  que ver.
- **`hooks/` no puede importar `mcp_server/`.** Los dos son capa 5. Por eso
  `accounts.py` bajó a L3 y `budget.py` está en L2: la cifra que se le muestra
  al agente y la que lo frena tienen que ser la misma función, o el día que
  discrepen va a ser a las tres de la mañana.

## Por qué

`app.py` era un único archivo de ~1000 líneas que mezclaba paleta, dibujo,
lógica de dominio, integración con Windows y dos ventanas. Separarlo en capas
con dependencias hacia abajo hace que:

- La lógica testeable (config, alertas, formato, scroll) no dependa de la UI.
- Todo lo específico del sistema operativo viva en un solo lugar (`platform/`).
  Eso es lo que hizo posible el port a macOS: las tres diferencias reales
  (el token en el llavero en vez de un archivo, el `NSStatusItem` que exige el
  main thread, y la barra de menú arriba en vez de la de tareas abajo) entraron
  como funciones nuevas del seam, sin tocar la lógica de dominio ni la UI.
- Un cambio de comportamiento tenga un hogar obvio en vez de sumar líneas a un
  archivo que ya nadie podía revisar.

## `providers/`

Cada proveedor es un módulo con un singleton `PROVIDER` y una línea en
`providers/__init__._REGISTRY`. Nada más en el proyecto nombra un proveedor:
`api.py` despacha vía `profile.adapter`, `config.py` le pregunta al adapter
dónde están las credenciales, y la ventana de perfiles dibuja un radio button
por cada entrada de `all_providers()`.

| Módulo | Qué es |
|---|---|
| `_types.py` | El contrato (`Provider`) y los helpers de normalización. |
| `_claude.py` | Anthropic: `api.anthropic.com/api/oauth/usage`, refresh vía `claude update`, y la sesión en `.credentials.json` o en el llavero (ver abajo). |
| `_codex.py` | OpenAI Codex: `~/.codex/auth.json`, `chatgpt.com/backend-api/codex/usage`, refresh OAuth propio. |

### Cómo se agrega uno nuevo

1. Crear `providers/_<nombre>.py` con una clase que cumpla `Provider` y
   exponer `PROVIDER = MiProvider()`.
2. Registrarlo en `_REGISTRY`.
3. Agregar su variable de entorno a `PROVIDER_HOME_ENV` en `tests/conftest.py`,
   para que el aislamiento de tests siga siendo completo.

Los tests de contrato de `tests/test_providers.py` corren automáticamente
contra el adapter nuevo: shape del payload, tolerancia a respuestas raras,
ventanas declaradas y manejo de credenciales ausentes o corruptas.

### Por qué el adapter no hace el request

`usage_request()` devuelve una *descripción* del GET en vez de ejecutarlo.
`config.py` (L2) importa `providers` para saber dónde viven las credenciales,
así que este paquete tiene que quedar en L1 y sin `requests` — si no, todo
módulo puro que toca un perfil arrastraría la capa de red
(`tests/test_import_hygiene.py`). Como efecto secundario, el cache, el backoff
y el reintento por 401 se escriben una sola vez en `api.py` en vez de una vez
por proveedor.

### Por qué el llavero de macOS vive acá adentro

Claude Code en macOS no escribe `.credentials.json`: guarda el mismo JSON en
el llavero, bajo un nombre de servicio que deriva de la carpeta de
configuración. Esa lectura es del adapter de Claude y no de `api.py`, por dos
razones que se refuerzan.

La primera es de pertenencia: la entrada del llavero es de Claude Code, con el
nombre de Claude Code, y ningún otro proveedor tiene una. Ponerla en la capa
agnóstica sería hacer que un módulo que no debe nombrar proveedores cargue con
la rareza de almacenamiento de uno solo.

La segunda es que arriba el arreglo quedaba a medias. Las credenciales se leen
**dos** veces: una para contestar "¿hay sesión?" y otra para armar el header
`Authorization` del pedido de uso. Un fallback que sólo cubriera la primera
dejaba la bandeja diciendo "sesión iniciada" y sin números para siempre.

Qué entrada del llavero contesta por qué carpeta lo resuelve
`_keychain_services`. El nombre no es global: Claude Code le pega al base
`Claude Code-credentials` un guión y los primeros ocho caracteres del SHA-256
de la ruta de la carpeta, con su propio `~/.claude` como única excepción, que
queda sin sufijo. Las dos mitades importan por igual: sin el sufijo, todo
perfil que no sea el de casa muestra "sin sesión"; con el sufijo aplicado a
todos, el que se rompe es justamente el de casa.

Por eso el adapter arma la lista de candidatos y prueba primero el nombre
sufijado —nombra una sola carpeta, así que preferirlo sólo puede ser más
preciso, y sigue andando si Claude Code algún día sufija también la entrada por
defecto—. El nombre pelado no se le ofrece nunca a otra carpeta: ésa es la
consulta que haría que todos los perfiles del Mac reporten la cuenta principal
con nombres distintos, dato equivocado presentado con confianza, que es peor
que la ausencia de dato. Una carpeta desde la que nadie inició sesión no tiene
entrada y se lee como "sin sesión".

`_is_ambient` sigue existiendo, pero ya no para esto: su único cliente es
`refresh_credentials`, porque `claude update` renueva la sesión de la cuenta en
la que está logueado el CLI local y no recibe carpeta alguna. El archivo,
cuando existe, siempre gana; y un archivo que existe pero no se puede leer es
una falla real, no una invitación a preguntarle al llavero.

### Por qué Codex no agregó ventanas de cuota nuevas

La ventana `primary` de Codex es de 5 horas y la `secondary` de 7 días, o sea
exactamente `session` y `weekly`. Reusar esos ids hace que el tooltip, el
popup, las alertas, el budget y el reporte MCP muestren un perfil de Codex sin
un solo cambio. `weekly_fable` queda en `None`, que es el mismo caso que una
cuenta de Claude sin Fable, ya soportado en todos lados.

## macOS: quién corre el event loop

La diferencia menos obvia del port, y la que no se ve en la tabla de capas.

En Windows, `app.py` arranca dos loops: `pystray` corre el suyo en un thread
daemon y `tkinter` corre el `mainloop()` en el main thread. En macOS eso no se
puede: el icono de la barra de menú es un `NSStatusItem`, o sea un objeto de
AppKit, y AppKit sólo admite el main thread. Peor: en un proceso hay una sola
`NSApplication`, y bajo Aqua **Tk ya es esa aplicación** — `Tk.mainloop()` es,
por debajo, el run loop de Cocoa.

La solución es no arrancar un segundo loop. `pystray` expone `run_detached()`
justo para integrarse con otra librería que ya tenga uno: deja el icono listo y
vuelve, y los clics del menú los despacha el loop que ya está corriendo. El seam
lo declara con `tray_requires_host_event_loop()`, y `app.py` elige el camino
según esa respuesta.

De ahí sale la segunda regla: cualquier cambio posterior al icono (la imagen
nueva de cada polleo, el tooltip) también es una llamada a AppKit. Como los
polleos ocurren en threads, `_apply_to_tray()` los reencamina por la misma
`_ui_queue` que ya usaban los callbacks de hover. Escribir en el `NSStatusItem`
desde otro thread no falla de entrada: corrompe o crashea más tarde, que en una
app que repinta cada 5 minutos significa morirse de madrugada sin motivo
aparente.

Y la tercera, que es la misma regla mirada al revés: *leer* la geometría del
icono también es AppKit. `tray_icon_rect()` pregunta por el frame de la ventana
del `NSStatusItem`, y el tooltip propio la llama treinta veces por segundo. Por
eso `HoverTracker` acepta un `scheduler`: en Windows el rect lo contesta el
shell desde cualquier thread y el tracker se corre su propio daemon, mientras
que acá `app.py` le pasa `root.after` y los polleos suceden en el loop que ya
está corriendo. La máquina de estados es la misma; lo único que cambia de manos
es el reloj.
