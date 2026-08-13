# Instalación paso a paso

Guía para dejar la herramienta andando desde cero. No necesitas saber programar:
son cuatro instalaciones y un comando de prueba. Toma entre 10 y 20 minutos,
casi todo esperando descargas.

*(English version at the bottom.)*

## Qué vas a instalar

| Qué | Para qué | Tamaño |
|---|---|---|
| **Blender** | Hace el trabajo 3D. Corre solo, sin abrir ventanas | 400 MB |
| **Python 3.12 o superior** | Ejecuta la herramienta | 30 MB |
| **uv** | Instala las librerías de Python por ti | 30 MB |
| **Este repositorio** | La herramienta en sí | 3 MB |

---

## macOS

### 1. Blender

Bájalo de [blender.org/download](https://www.blender.org/download/) y arrástralo
a la carpeta Aplicaciones. Ábrelo una vez para que macOS lo autorice y ciérralo.

Si prefieres Homebrew:

```bash
brew install --cask blender
```

Queda en `/Applications/Blender.app`, que es donde la herramienta lo busca sola.
No hay nada que configurar.

### 2. Python

macOS trae Python, pero suele ser una versión antigua. Revisa qué tienes:

```bash
python3 --version
```

Si dice 3.12 o superior, listo. Si dice menos, instálalo:

```bash
brew install python@3.13
```

### 3. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Cierra la Terminal y ábrela de nuevo para que tome el comando.

### 4. La herramienta

```bash
git clone https://github.com/MechatronicStore/mechatronicstore-logo-3d.git
cd mechatronicstore-logo-3d
uv sync
```

### 5. Probar que funciona

```bash
python3 logo3d.py --model examples/coaster_simple.stl --logo m --preview
```

Tiene que aparecer:

```
[logo3d] logo size not given, using 45.0 mm
[logo3d] mode=geom logo=logo-m.svg size=45.0mm
[geom] support check: logo fully lands on material (100%)

OK  examples/coaster_simple_logo.glb  (233 KB)
    previews: examples/coaster_simple_logo_previews/
```

Abre la carpeta de previews y mira `iso.png`: tiene que verse la m morada sobre
un posavasos. Si la ves, ya está todo instalado.

---

## Windows

### 1. Blender

Bájalo de [blender.org/download](https://www.blender.org/download/) e instálalo
normal (siguiente, siguiente, terminar).

### 2. Python

Bájalo de [python.org/downloads](https://www.python.org/downloads/). En la
primera pantalla del instalador marca **"Add python.exe to PATH"** antes de
seguir. Ese casillero es el que evita el 90% de los problemas después.

### 3. uv

Abre PowerShell y pega:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Cierra PowerShell y ábrelo de nuevo.

### 4. La herramienta

```powershell
git clone https://github.com/MechatronicStore/mechatronicstore-logo-3d.git
cd mechatronicstore-logo-3d
uv sync
```

Si no tienes git, baja el ZIP verde desde la página del repositorio y
descomprímelo donde quieras.

### 5. Decirle dónde está Blender

En Windows hay que indicarlo una vez. Revisa en qué carpeta quedó (cambia la
versión según lo que instalaste) y ejecuta:

```powershell
$env:BLENDER_PATH = "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
```

Eso vale para la ventana actual de PowerShell. Para dejarlo fijo:

```powershell
[Environment]::SetEnvironmentVariable("BLENDER_PATH", "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe", "User")
```

### 6. Probar que funciona

```powershell
python logo3d.py --model examples\coaster_simple.stl --logo m --preview
```

---

## Linux

```bash
# Blender: el de tu distribución sirve si es 4.x o más nuevo
sudo apt install blender          # Debian, Ubuntu
sudo dnf install blender          # Fedora
flatpak install flathub org.blender.Blender    # cualquiera, versión más nueva

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# la herramienta
git clone https://github.com/MechatronicStore/mechatronicstore-logo-3d.git
cd mechatronicstore-logo-3d
uv sync
python3 logo3d.py --model examples/coaster_simple.stl --logo m --preview
```

Si instalaste Blender por Flatpak, la herramienta no lo va a encontrar sola.
Indícale la ruta:

```bash
export BLENDER_PATH="$(which flatpak) run org.blender.Blender"
```

Si eso te da problemas, instala Blender del paquete oficial de blender.org y
apunta `BLENDER_PATH` al ejecutable.

---

## Tu primera pieza de verdad

1. Baja cualquier modelo de MakerWorld (el `.3mf` sirve tal cual, no hay que
   convertir nada).
2. Mira qué trae dentro:

   ```bash
   python3 logo3d.py --model "Tu Modelo.3mf" --list-pieces
   ```

3. Elige la pieza donde quieres el logo y genera el archivo:

   ```bash
   python3 logo3d.py --model "Tu Modelo.3mf" --piece 2 --logo m --preview
   ```

4. Mira los previews. Si el logo quedó chico, grande o corrido, ajusta con
   `--logo-size-mm 25` o `--offset-y-mm 10` y vuelve a correrlo. Es instantáneo
   y no toca el archivo original.
5. Abre Bambu Studio **primero**, sin ningún archivo, y **arrastra** el `.glb` a
   la placa. Con doble clic al archivo no funciona.
6. Asigna un filamento a cada color y lamina.

---

## Cuando algo falla

**"Blender not found"**
No está instalado, o está en una ruta que la herramienta no revisa. Solución:
`export BLENDER_PATH="/ruta/al/ejecutable/blender"` (en Windows, el comando de
la sección 5 de arriba).

**macOS dice que Blender es de un desarrollador no identificado**
Abre Blender una vez haciendo clic derecho sobre la aplicación y eligiendo
Abrir. Después de autorizarlo esa vez, la herramienta lo puede usar siempre.

**"holds 5 pieces, pick one with --piece N"**
No es un error: el `.3mf` trae varias piezas y hay que decir en cuál va el logo.
La lista con las medidas de cada una sale en el mismo mensaje.

**"WARNING support check: only 0% of the logo sits on the top surface"**
La pieza es hueca o tipo marco, y el centro de la cara de arriba cae al vacío.
Muévelo con `--offset-x-mm` / `--offset-y-mm` o hazlo más chico con
`--logo-size-mm`.

**El logo sale bien en los previews pero Bambu no muestra colores**
Abriste el `.glb` con doble clic. Cierra todo, abre Bambu Studio primero y
arrastra el archivo a la placa.

**"unsupported input '.obj'"**
Solo entran `.stl` y `.3mf`. Exporta tu modelo a uno de esos dos formatos.

**En Windows: "python no se reconoce como un comando"**
No marcaste "Add python.exe to PATH" al instalar. Reinstala Python con ese
casillero marcado.

---

# English

The tool needs four things: **Blender 4.x or newer**, **Python 3.12 or newer**,
**uv**, and this repository.

```bash
# macOS
brew install --cask blender
curl -LsSf https://astral.sh/uv/install.sh | sh

# every platform, once Blender and uv are in place
git clone https://github.com/MechatronicStore/mechatronicstore-logo-3d.git
cd mechatronicstore-logo-3d
uv sync
python3 logo3d.py --model examples/coaster_simple.stl --logo m --preview
```

Blender is found automatically on PATH and at the macOS default location. On
Windows, and for Flatpak installs, point `BLENDER_PATH` at the executable:

```powershell
[Environment]::SetEnvironmentVariable("BLENDER_PATH", "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe", "User")
```

Then open the previews folder and check `iso.png`: you should see the purple m
on a coaster. If you do, the install is done.

Troubleshooting, in short:

* **Blender not found**: set `BLENDER_PATH`.
* **"holds N pieces"**: the `.3mf` has several parts, choose one with `--piece N`.
* **"only 0% of the logo sits on the top surface"**: the part is hollow, move the
  logo with `--offset-x-mm` / `--offset-y-mm` or shrink it.
* **No colors in Bambu Studio**: you double-clicked the file. Launch Bambu first,
  then drag the `.glb` onto the plate.
