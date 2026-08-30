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
| 1 | `quotas.py` | Nombres de las ventanas de cuota (`session`, `weekly`, `weekly_fable`) y sus etiquetas. Fuente única para que el tooltip, el popup y las alertas llamen a cada cuota igual. |
| 1 | `formatting.py` | Convierte datos crudos en texto para mostrar (porcentajes, fechas, tooltip). Sin dependencias de UI. |
| 2 | `config.py` | Modelo de perfiles: alta, edición, reordenamiento, borrado, identidad estable por `id`, semilla de primer arranque. |
| 2 | `settings.py` | Preferencias del usuario (tema, tamaño de letra, colores hex, alertas) y su serialización. |
| 2 | `alerts.py` | Máquina de estados de las alertas: edge-triggered por (perfil × ventana × umbral), re-armado al reiniciarse una cuota, y cooldown. Es pura (sin reloj ni I/O) para poder testearla. |
| 2 | `icons.py` | Dibuja el icono de la bandeja y de la ventana con los colores del tema actual. |
| 2 | `logging_setup.py` | Configura el log a `%APPDATA%\ClaudeUsage\claude-usage.log` y captura excepciones no manejadas (clave con `--windowed`, que descarta la consola). |
| 3 | `api.py` | Habla con la API de uso de Anthropic y maneja backoff / rate limiting. |
| 4 | `ui/` | Todas las ventanas. Ver abajo. |
| 5 | `app.py` | El orquestador: arma el menú de la bandeja, la cola de UI, el loop de polleo y conecta todo. No contiene lógica de negocio ni construcción de widgets. |

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
