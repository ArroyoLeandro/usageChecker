# Claude Usage

App de Windows y macOS para ver el uso de Claude Code y de OpenAI Codex desde la bandeja del sistema (la barra de menú en Mac), con todas tus cuentas en una sola ventana.

## Características

- **Varios servicios a la vez**: Claude Code y OpenAI Codex en la misma lista, cada uno con su cuota y su renovación. Si tenés los dos instalados, la app los detecta sola en el primer arranque.
- **Perfiles**: agregar, editar (servicio, nombre y ruta), reordenar y eliminar cualquier perfil, incluido el detectado automáticamente.
- **Tooltip propio**: al pasar el mouse por el icono de la bandeja aparece un resumen con los colores del tema, en lugar del tooltip gris del sistema. Funciona igual en Windows y en macOS; si el sistema no puede decir dónde está el icono, la app vuelve sola al tooltip nativo.
- **Popup de uso**: ventana con el uso de cada perfil por ventana de cuota (últimas 5 horas, últimos 7 días, Fable). Codex reporta las dos primeras; no tiene Fable.
- **Configuración por usuario**: tema (oscuro/claro), tamaño de letra y colores personalizados por hex.
- **Alertas**: aviso cuando el uso cruza un umbral, configurable por perfil y por ventana de cuota, con un tiempo mínimo entre avisos.
- **Persistencia**: toda la configuración se guarda en la carpeta de datos del usuario (ver [Dónde se guardan los datos](#dónde-se-guardan-los-datos)).

Para entender cómo está organizado el código, ver [ARCHITECTURE.md](ARCHITECTURE.md).

## Descargar y ejecutar

El ejecutable compilado está versionado, así que no hace falta instalar Python ni compilar nada:

**[⬇ Descargar ClaudeUsage.exe](https://github.com/ArroyoLeandro/usageChecker/raw/main/dist/ClaudeUsage.exe)**

Lo bajás, lo ejecutás, y aparece en la bandeja del sistema. Nada más.

> Windows SmartScreen puede avisar que es un ejecutable desconocido, porque el binario no está firmado. *Más información → Ejecutar de todas formas.*

En **macOS** no hay binario publicado: el `.app` se arma en un minuto desde el código fuente (ver [Compilar](#compilar)), y firmarlo para distribuirlo requiere una cuenta de desarrollador de Apple.

## Requisitos

- Windows, o macOS 11 o posterior.
- [Claude Code](https://claude.ai/download) y/o [OpenAI Codex](https://developers.openai.com/codex/) instalados. Alcanza con uno.
- Tener sesión iniciada en el CLI del perfil que quieras consultar.
- Python 3.10 o superior **sólo** si vas a correrlo desde el código fuente o recompilarlo.
  En Mac conviene el instalador oficial de [python.org](https://www.python.org/downloads/macos/):
  trae su propio Tcl/Tk 8.6, y el Python 3.9 que viene con el sistema no alcanza.

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

La app se abre en la bandeja del sistema. Un perfil sin sesión iniciada en su CLI aparece como "hace falta iniciar sesión": abrí Claude Code o Codex, según el perfil, e iniciá sesión ahí.

## Compilar

```bash
python build.py
```

El mismo script produce lo que corresponda al sistema donde corre: un
`dist/ClaudeUsage.exe` en Windows y un `dist/ClaudeUsage.app` en macOS.

En Mac, arrastrá `dist/ClaudeUsage.app` a `/Applications` y abrilo desde ahí.
El bundle se marca como `LSUIElement`, así que no aparece en el Dock ni tiene
menú propio: vive sólo en la barra de menú. La primera vez macOS puede pedir
permiso para leer el llavero (ver [De dónde saca la sesión](#de-dónde-saca-la-sesión));
dale **Permitir siempre** para que no vuelva a preguntar en cada actualización.

El `.exe` **sí** se versiona, para que siempre haya una copia lista para descargar y ejecutar (ver [Descargar y ejecutar](#descargar-y-ejecutar)). El `.app` de macOS no, y el resto de `dist/` y todo `build/` quedan fuera del repo.

Como cada rebuild agrega ~22 MB al historial de git de forma permanente, conviene commitear el `.exe` cuando publicás un cambio que querés que la gente use, no en cada compilación local.

## Agregar perfiles

Desde la app:

1. Abrí la ventana principal desde el icono de la bandeja.
2. Entrá a la sección de perfiles.
3. Elegí el servicio (Claude Code u OpenAI Codex), poné un nombre y la carpeta del perfil que querés monitorear.

Rutas comunes:

| Servicio | Ruta |
|---|---|
| Claude Code (Windows) | `C:\Users\TU_USUARIO\.claude` |
| Claude Code (macOS) | `/Users/TU_USUARIO/.claude` |
| Claude Code (WSL) | `\\wsl.localhost\Ubuntu\home\TU_USUARIO\.claude` |
| OpenAI Codex (Windows) | `C:\Users\TU_USUARIO\.codex` |
| OpenAI Codex (macOS) | `/Users/TU_USUARIO/.codex` |

La app usa la sesión local de cada carpeta. No copies credenciales al repositorio ni compartas `.credentials.json` (Claude) ni `auth.json` (Codex).

### Qué se ve de cada servicio

Ninguno de los dos expone un número absoluto de tokens restantes: los dos publican **porcentaje consumido y momento de renovación**, que es lo que muestra la app.

| Ventana de cuota | Claude Code | OpenAI Codex |
|---|---|---|
| Últimas 5 horas | sí | sí |
| Últimos 7 días | sí | sí |
| Fable, últimos 7 días | sí | no aplica |

### Agregar otro servicio

La app está armada para que sumar uno más (opencode, Cursor, etc.) sea un archivo nuevo en `claude_usage_tray/providers/` y una línea en el registro, sin tocar la API, la config ni la UI. Los pasos y el contrato están en [ARCHITECTURE.md](ARCHITECTURE.md).

## Configuración y alertas

Desde la ventana principal, pestaña **Configuración**:

- **Tema y tamaño de letra**: se aplican al instante a todas las ventanas.
- **Colores**: tocá el cuadrito de cada color para elegirlo, o escribí el hex. Los que dicen "del tema" usan el color del tema elegido; en cuanto tocás uno pasa a ser propio. El botón `↺` vuelve al valor anterior.
- **Alertas**: activá los avisos y definí los umbrales (por ejemplo `80, 95`). Hay un umbral **General** que se usa en los perfiles sin umbral propio, y podés definir umbrales distintos por perfil y por ventana de cuota. El **tiempo mínimo entre avisos** evita que el mismo aviso se repita: como una ventana de cuota se reinicia cada varias horas, para no volver a recibir el mismo aviso conviene un valor mayor a ese período.

## De dónde saca la sesión

Cada servicio guarda su token donde quiere, y el adapter de cada uno sabe
dónde buscarlo. Codex es el caso simple: `auth.json` dentro de la carpeta del
perfil, en cualquier sistema operativo. Claude Code depende del sistema.

En Windows y Linux, Claude Code deja el token en `.credentials.json` dentro de
la carpeta del perfil, y la app lo lee de ahí.

En **macOS no existe ese archivo**: Claude Code guarda el mismo JSON en el
llavero (Keychain). La app lo lee con `/usr/bin/security`, que es el binario
firmado por Apple al que el llavero ya le da acceso — por eso el permiso se pide
una sola vez y no en cada consulta.

El nombre del servicio no es uno solo: Claude Code lo deriva de la carpeta de
configuración, pegándole al nombre base `Claude Code-credentials` un guión y los
primeros ocho caracteres del SHA-256 de la ruta. La única excepción es su propio
`~/.claude`, cuya entrada queda sin sufijo. Por eso la app arma los dos nombres
posibles y prueba primero el que lleva el sufijo: eso deja que **cada perfil
lea su propia sesión** — un segundo perfil apuntando a `~/.claude-trabajo`
encuentra la suya — sin que el nombre pelado se le ofrezca nunca a otra carpeta,
que es la consulta que haría aparecer el uso de tu cuenta principal con el
nombre de otro perfil.

Una carpeta desde la que nunca iniciaste sesión simplemente no tiene entrada y
se muestra como "sin sesión", salvo que tenga su propio `.credentials.json` (por
ejemplo, una `.claude` de WSL montada). Los perfiles de Codex no pasan por nada
de esto: su token siempre está en un archivo dentro de la carpeta que elegiste.

## Servidor MCP: que un agente consulte el uso

Además de la bandeja, el proyecto expone el mismo dato como un **servidor MCP**,
para que un agente (Claude Code, por ejemplo) pueda preguntar cuánta cuota queda
antes de arrancar un trabajo largo o entre vueltas de un loop programado.

No necesita Windows, ni la app corriendo, ni instalar nada: es stdlib pura sobre
`requests`, que ya está en `requirements.txt`.

### Registrarlo

```bash
claude mcp add claude-usage --scope user \
  -e PYTHONPATH=/ruta/a/usageChecker \
  -- python3 -m claude_usage_tray.mcp_server
```

Con `--scope user` queda disponible en cualquier proyecto. Para usarlo sólo
dentro de este repo alcanza con el `.mcp.json` que ya viene versionado.
Verificalo con `claude mcp list`; se saca con `claude mcp remove claude-usage`.

### El tool

`get_claude_usage(config_dir?, force_refresh?)` devuelve, en un solo llamado:

- las tres ventanas de cuota (5 horas, 7 días, Fable) con el porcentaje usado,
  el restante, y cuánto falta para que se reinicien;
- `highest_used_percent`: el más alto de todos, que es **el que manda** — la
  cuota se agota cuando se llena cualquiera, no sólo la de 5 horas;
- `other_limits`: los cortes por modelo que trae la cuenta (Opus, Sonnet, etc.);
- `summary`: una línea del estilo `5h 25% · 7d 66%`.

### Qué cuenta consulta

Por defecto, **la del Claude que lo lanzó**. Claude Code exporta
`CLAUDE_CONFIG_DIR` y los servidores que arranca lo heredan, así que un Claude
corriendo sobre `~/.claude-personal` recibe el uso de esa cuenta sin
configuración alguna. El orden completo, de más específico a menos:

1. el argumento `config_dir` del tool — para preguntar por otra cuenta;
2. `CLAUDE_USAGE_CONFIG_DIR` en la entrada del servidor — para fijarlo a mano;
3. `CLAUDE_CONFIG_DIR` — el Claude que llama;
4. `~/.claude`.

Cada respuesta incluye qué carpeta se usó y por cuál de esas reglas, así que una
cuenta equivocada se diagnostica leyendo el resultado.

### Informa, no frena

El servidor puede decir que la cuota semanal va 91%; **no puede impedir que el
agente siga**. Nada de lo que devuelve un tool MCP es vinculante: el modelo lo
lee y decide. Para un corte que el modelo no pueda ignorar está el techo de uso,
abajo.

## Techo de uso: frenar al agente automáticamente

El caso de uso: dejar un agente trabajando de noche sin que se coma la cuota de
toda la semana. Para eso hacen falta **dos** números, no uno. Un corte seco al
70% frena al agente en la mitad de una edición, con el trabajo sin guardar — un
resultado peor que el gasto que evitó. Así que un presupuesto es un **techo**
más un **margen de aviso**: al entrar en el margen se le dice al agente que
cierre y guarde, mientras todavía le queda cuota para hacerlo.

```
        65%                    70%
   ──────┼──────────────────────┼──────────►
         │                      │
      aviso:                 techo:
   "cerrá y guardá"      turno rechazado
```

### Configurarlo

Desde cualquier sesión:

```
/usage-budget 70          # techo 70%, aviso desde 65%
/usage-budget 70 10       # techo 70%, aviso desde 60%
/usage-budget status      # dónde estás parado
/usage-budget off         # sin techo
```

En criollo también funciona ("no pases del 70% esta noche"): el agente llama al
tool `set_usage_budget`.

### Alcance: la sesión donde lo pusiste, y sus subagentes

El techo vale para **esa** sesión de Claude y para los subagentes que lance —
no para todo Claude. Las otras terminales que tengas abiertas siguen igual.

Funciona porque Claude Code exporta `CLAUDE_CODE_SESSION_ID` a todo proceso que
arranca, y un subagente hereda el del padre. Escritor (el tool MCP) y lector (el
hook) ven el mismo valor, así que "este agente y todo lo que él levantó" es
exactamente una clave. Cada sesión guarda su techo en su propio archivo dentro
de `budgets/`, así que dos sesiones fijando techo al mismo tiempo no se pisan.

El precio, que conviene tener claro: con tres sesiones abiertas se pueden gastar
tres techos entre todas. El techo acota al agente que apuntaste, no a la cuenta.
Si lo que querés es acotar la cuenta entera, hay que poner el mismo techo en
cada sesión.

### Instalarlo

El techo lo aplica un hook, que se declara en `settings.json` (`~/.claude/`
para todos los proyectos, o `.claude/` para uno solo). Los dos eventos hacen
falta:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "timeout": 15,
                    "command": "env PYTHONPATH=/ruta/a/usageChecker python3 -m claude_usage_tray.hooks" }] }
    ],
    "PreToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "timeout": 15,
                    "command": "env PYTHONPATH=/ruta/a/usageChecker python3 -m claude_usage_tray.hooks" }] }
    ]
  }
}
```

`UserPromptSubmit` corta entre turnos — incluida cada vuelta de un `/loop`.
`PreToolUse` corta *dentro* de un turno largo, que es donde el otro no vuelve a
dispararse hasta que termine.

### Sacar el techo y volver a arrancar

Si **todavía no** te bloqueó, alcanza con:

```
/usage-budget off        # o "subime el techo a 85"
```

Si **ya** te bloqueó, usá el comando dedicado:

```
/usage-override off      # o /usage-override 93 para subirlo
```

El gate matchea la cadena pelada `usage-override`, y el nombre del comando ya
la contiene, así que invocarlo *es* la escotilla. También funciona suelta en
cualquier parte de un mensaje común: `subime el techo a 93 usage-override`.

**No lleva `!` adelante.** Claude Code lee un `!` inicial como su prefijo de
shell, así que `!usage-override ...` se lo comía bash y nunca llegaba a ser
texto de prompt — justo la posición más natural era la única que no podía
funcionar.

La escotilla deja pasar ese turno sin tocar la red. Y `set_usage_budget`
está exento del gate de forma permanente: si el tool que levanta el techo
quedara detrás del techo, no habría vuelta desde adentro de la sesión.

Desde afuera de Claude siempre funciona borrar el archivo:

```bash
rm ~/.config/ClaudeUsage/budgets/<session-id>.json   # %APPDATA%\ClaudeUsage\budgets\ en Windows
```

Esa exención es también la única vía por la que un agente podría levantarse el
techo a sí mismo, así que el mensaje de bloqueo le dice explícitamente que no lo
haga y que sólo vos podés cambiarlo. Es una instrucción, no una barrera: si
querés un techo que ni el agente coopere en levantar, sacá `set_usage_budget`
del servidor MCP y manejá el archivo sólo desde la terminal.

### Falla abierto, siempre

Sin presupuesto configurado, con uso desconocido, con la API caída, con el
archivo corrupto o con una excepción adentro del hook: **te deja pasar**. Un
gate que falla cerrado te deja afuera de la sesión que necesitarías para
desbloquearlo, hasta que resetee la cuota horas después. Gastar de más se
recupera; no poder escribir, no.


## Dónde se guardan los datos

En Windows, todo vive en `%APPDATA%\ClaudeUsage\` (normalmente
`C:\Users\TU_USUARIO\AppData\Roaming\ClaudeUsage\`). En macOS, en
`~/Library/Application Support/ClaudeUsage/`. En ambos casos:

- `config.json` — perfiles (con su servicio) y preferencias (tema, colores, alertas). Es por computadora: cualquier ejecución de la app lee y escribe este mismo archivo.
- `alert-state.json` — estado interno de las alertas (qué se notificó y cuándo). Se puede borrar sin perder configuración.
- `budgets/<session-id>.json` — el techo de cada sesión que fijó uno. Borrar el archivo equivale a `/usage-budget off` en esa sesión. Los que quedan de sesiones muertas se barren solos a los 7 días.
- `usage-cache-<cuenta>.json` — la última cifra de uso que leyó el hook, una por cuenta. Machine-written y descartable: sin esto, el hook haría una llamada a la API antes de cada prompt y de cada tool.
- `claude-usage.log` — registro de diagnóstico. Si algo falla, el error queda acá (la app se compila sin consola, así que este archivo es la forma de ver qué pasó).

## Diferencias entre plataformas

| | Windows | macOS |
|---|---|---|
| Dónde vive el icono | bandeja del sistema | barra de menú |
| Token de sesión | `.credentials.json` | llavero (Keychain) |
| Tooltip al pasar el mouse | propio, con los colores del tema | propio, con los colores del tema |
| Arranque automático | registro de Windows | LaunchAgent en `~/Library/LaunchAgents` |
| Dónde se abre el popup | abajo a la derecha, sobre la barra de tareas | arriba a la derecha, bajo la barra de menú |

El tooltip propio necesita saber dónde está el icono en pantalla. Windows lo
contesta con `Shell_NotifyIconGetRect`; macOS no tiene esa función, pero cada
item de la barra de menú vive en su propia ventana y una ventana sí sabe su
frame, así que la respuesta existe por otro camino. En las dos plataformas la
app pregunta en cada polleo -- el icono se corre cuando aparece o desaparece
otro al lado -- y si en algún momento deja de haber respuesta, vuelve sola al
tooltip nativo, que muestra la misma información.

