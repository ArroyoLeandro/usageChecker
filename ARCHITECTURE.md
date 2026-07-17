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
- Todo lo específico de Windows viva en un solo lugar (`platform/`), lo que deja
  el camino listo para un port a Linux sin tocar el resto.
- Un cambio de comportamiento tenga un hogar obvio en vez de sumar líneas a un
  archivo que ya nadie podía revisar.
