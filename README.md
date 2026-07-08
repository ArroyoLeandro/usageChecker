# Claude Usage

App de Windows para ver el uso de Claude Code desde la bandeja del sistema y consultar varios perfiles de Claude desde una sola ventana.

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
