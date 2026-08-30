# Claude Usage

App de Windows y macOS para ver el uso de Claude Code desde la bandeja del sistema (la barra de menú en Mac) y consultar varios perfiles de Claude desde una sola ventana.

## Características

- **Perfiles**: agregar, editar (nombre y ruta), reordenar y eliminar cualquier perfil, incluido el detectado automáticamente.
- **Tooltip propio** (Windows): al pasar el mouse por el icono de la bandeja aparece un resumen con los colores del tema, en lugar del tooltip gris del sistema. En macOS se usa el tooltip nativo, con la misma información.
- **Popup de uso**: ventana con el uso de cada perfil por ventana de cuota (últimas 5 horas, últimos 7 días, Fable).
- **Configuración por usuario**: tema (oscuro/claro), tamaño de letra y colores personalizados por hex.
- **Alertas**: aviso cuando el uso cruza un umbral, configurable por perfil y por ventana de cuota, con un tiempo mínimo entre avisos.
- **Persistencia**: toda la configuración se guarda en la carpeta de datos del usuario (ver [Dónde se guardan los datos](#dónde-se-guardan-los-datos)).

Para entender cómo está organizado el código, ver [ARCHITECTURE.md](ARCHITECTURE.md).

## Requisitos

- Windows, o macOS 11 o posterior.
- Python 3.10 o superior para ejecutar el proyecto desde código fuente.
  En Mac conviene el instalador oficial de [python.org](https://www.python.org/downloads/macos/):
  trae su propio Tcl/Tk 8.6, y el Python 3.9 que viene con el sistema no alcanza.
- [Claude Code](https://claude.ai/download) instalado.
- Tener sesión iniciada en Claude Code en el perfil que quieras consultar.

## Clonar e instalar

```bash
git clone https://github.com/ArroyoLeandro/usageChecker.git
cd usageChecker
python -m venv .venv
.venv\Scripts\activate      # en macOS: source .venv/bin/activate
pip install -r requirements.txt
```

En macOS `pip` trae además `pyobjc`, que es lo que pystray usa para poner el
icono en la barra de menú. Viene como dependencia de `pystray`, no hay que
pedirlo aparte.

## Ejecutar en desarrollo

```bash
python launcher.py
```

La app se abre en la bandeja del sistema. Si Claude Code no tiene una sesión iniciada, la app no podrá leer el uso hasta que abras Claude e inicies sesión.

## Compilar

```bash
python build.py
```

El mismo script produce lo que corresponda al sistema donde corre: un `.exe`
en Windows y un `ClaudeUsage.app` en macOS. Todo va a parar a `dist/`, que no
se versiona: cada persona lo reconstruye localmente.

En Mac, arrastrá `dist/ClaudeUsage.app` a `/Applications` y abrilo desde ahí.
El bundle se marca como `LSUIElement`, así que no aparece en el Dock ni tiene
menú propio: vive sólo en la barra de menú. La primera vez macOS puede pedir
permiso para leer el llavero (ver abajo); dale **Permitir siempre** para que no
vuelva a preguntar en cada actualización.

## Agregar perfiles de Claude

Desde la app:

1. Abrí la ventana principal desde el icono de la bandeja.
2. Entrá a la sección de perfiles.
3. Agregá un nombre y la carpeta de Claude del perfil que querés monitorear.

Rutas comunes:

- Windows: `C:\Users\TU_USUARIO\.claude`
- macOS: `/Users/TU_USUARIO/.claude`
- WSL: `\\wsl.localhost\Ubuntu\home\TU_USUARIO\.claude`

La app usa la sesión local de cada carpeta de Claude. No copies credenciales al repositorio ni compartas el archivo `.credentials.json`.

## Configuración y alertas

Desde la ventana principal, pestaña **Configuración**:

- **Tema y tamaño de letra**: se aplican al instante a todas las ventanas.
- **Colores**: tocá el cuadrito de cada color para elegirlo, o escribí el hex. Los que dicen "del tema" usan el color del tema elegido; en cuanto tocás uno pasa a ser propio. El botón `↺` vuelve al valor anterior.
- **Alertas**: activá los avisos y definí los umbrales (por ejemplo `80, 95`). Hay un umbral **General** que se usa en los perfiles sin umbral propio, y podés definir umbrales distintos por perfil y por ventana de cuota. El **tiempo mínimo entre avisos** evita que el mismo aviso se repita: como una ventana de cuota se reinicia cada varias horas, para no volver a recibir el mismo aviso conviene un valor mayor a ese período.

## De dónde saca la sesión

En Windows y Linux, Claude Code deja el token en `.credentials.json` dentro de
la carpeta del perfil, y la app lo lee de ahí.

En **macOS no existe ese archivo**: Claude Code guarda el mismo JSON en el
llavero (Keychain), bajo el servicio `Claude Code-credentials`. La app lo lee
con `/usr/bin/security`, que es el binario firmado por Apple al que el llavero
ya le da acceso — por eso el permiso se pide una sola vez y no en cada consulta.

Ese detalle tiene una consecuencia: la entrada del llavero es **una sola** y no
distingue carpetas de configuración, así que sólo puede responder por el perfil
que apunta a tu `~/.claude`. Un segundo perfil en Mac que apunte a otra carpeta
va a mostrar "sin sesión" salvo que esa carpeta tenga su propio
`.credentials.json` (por ejemplo, una `.claude` de WSL montada). Es a propósito:
la alternativa sería mostrar el uso de tu cuenta principal con el nombre de otro
perfil.

## Dónde se guardan los datos

En Windows, todo vive en `%APPDATA%\ClaudeUsage\` (normalmente
`C:\Users\TU_USUARIO\AppData\Roaming\ClaudeUsage\`). En macOS, en
`~/Library/Application Support/ClaudeUsage/`. En ambos casos:

- `config.json` — perfiles y preferencias (tema, colores, alertas). Es por computadora: cualquier ejecución de la app lee y escribe este mismo archivo.
- `alert-state.json` — estado interno de las alertas (qué se notificó y cuándo). Se puede borrar sin perder configuración.
- `claude-usage.log` — registro de diagnóstico. Si algo falla, el error queda acá (la app se compila sin consola, así que este archivo es la forma de ver qué pasó).

## Diferencias entre plataformas

| | Windows | macOS |
|---|---|---|
| Dónde vive el icono | bandeja del sistema | barra de menú |
| Token de sesión | `.credentials.json` | llavero (Keychain) |
| Tooltip al pasar el mouse | propio, con los colores del tema | el nativo de macOS |
| Arranque automático | registro de Windows | LaunchAgent en `~/Library/LaunchAgents` |
| Dónde se abre el popup | abajo a la derecha, sobre la barra de tareas | arriba a la derecha, bajo la barra de menú |

El tooltip propio es el único recorte real: depende de poder preguntarle al
sistema por el rectángulo del icono, y la barra de menú de macOS no expone nada
equivalente. La app lo detecta y se queda con el tooltip nativo, que muestra la
misma información.
