# Claude Usage

App de Windows para ver el uso de Claude Code desde la bandeja del sistema y consultar varios perfiles de Claude desde una sola ventana.

## Características

- **Perfiles**: agregar, editar (nombre y ruta), reordenar y eliminar cualquier perfil, incluido el detectado automáticamente.
- **Tooltip propio**: al pasar el mouse por el icono de la bandeja aparece un resumen con los colores del tema, en lugar del tooltip gris del sistema.
- **Popup de uso**: ventana con el uso de cada perfil por ventana de cuota (últimas 5 horas, últimos 7 días, Fable).
- **Configuración por usuario**: tema (oscuro/claro), tamaño de letra y colores personalizados por hex.
- **Alertas**: aviso cuando el uso cruza un umbral, configurable por perfil y por ventana de cuota, con un tiempo mínimo entre avisos.
- **Persistencia**: toda la configuración se guarda en `%APPDATA%\ClaudeUsage\` (ver [Dónde se guardan los datos](#dónde-se-guardan-los-datos)).

Para entender cómo está organizado el código, ver [ARCHITECTURE.md](ARCHITECTURE.md).

## Requisitos

- Windows.
- Python 3 instalado para ejecutar el proyecto desde código fuente.
- [Claude Code](https://claude.ai/download) instalado.
- Tener sesión iniciada en Claude Code en el perfil que quieras consultar.

## Clonar e instalar

```bash
git clone https://github.com/ArroyoLeandro/usageChecker.git
cd usageChecker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecutar en desarrollo

```bash
python launcher.py
```

La app se abre en la bandeja del sistema. Si Claude Code no tiene una sesión iniciada, la app no podrá leer el uso hasta que abras Claude e inicies sesión.

## Compilar el `.exe`

```bash
python build.py
```

El ejecutable se genera en `dist/`. Ese directorio no se versiona: cada persona debe reconstruirlo localmente.

## Agregar perfiles de Claude

Desde la app:

1. Abrí la ventana principal desde el icono de la bandeja.
2. Entrá a la sección de perfiles.
3. Agregá un nombre y la carpeta de Claude del perfil que querés monitorear.

Rutas comunes:

- Windows: `C:\Users\TU_USUARIO\.claude`
- WSL: `\\wsl.localhost\Ubuntu\home\TU_USUARIO\.claude`

La app usa la sesión local de cada carpeta de Claude. No copies credenciales al repositorio ni compartas el archivo `.credentials.json`.

## Configuración y alertas

Desde la ventana principal, pestaña **Configuración**:

- **Tema y tamaño de letra**: se aplican al instante a todas las ventanas.
- **Colores**: tocá el cuadrito de cada color para elegirlo, o escribí el hex. Los que dicen "del tema" usan el color del tema elegido; en cuanto tocás uno pasa a ser propio. El botón `↺` vuelve al valor anterior.
- **Alertas**: activá los avisos y definí los umbrales (por ejemplo `80, 95`). Hay un umbral **General** que se usa en los perfiles sin umbral propio, y podés definir umbrales distintos por perfil y por ventana de cuota. El **tiempo mínimo entre avisos** evita que el mismo aviso se repita: como una ventana de cuota se reinicia cada varias horas, para no volver a recibir el mismo aviso conviene un valor mayor a ese período.

## Dónde se guardan los datos

Todo vive en `%APPDATA%\ClaudeUsage\` (normalmente `C:\Users\TU_USUARIO\AppData\Roaming\ClaudeUsage\`):

- `config.json` — perfiles y preferencias (tema, colores, alertas). Es por computadora: cualquier ejecución de la app lee y escribe este mismo archivo.
- `alert-state.json` — estado interno de las alertas (qué se notificó y cuándo). Se puede borrar sin perder configuración.
- `claude-usage.log` — registro de diagnóstico. Si algo falla, el error queda acá (el `.exe` se compila sin consola, así que este archivo es la forma de ver qué pasó).
