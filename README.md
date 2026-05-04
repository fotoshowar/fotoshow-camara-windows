# FotoShow — Cámara Sony WiFi (Windows)

Interfaz web para conectarse a una cámara Sony vía WiFi, ver las fotos en la cámara, seleccionar cuáles descargar y verlas en galería. Corre en Windows con Python.

---

## Instalación

### 1. Instalar Python
Bajalo de https://python.org → "Download Python 3.x" → instalar marcando **"Add Python to PATH"**

### 2. Clonar el repo
```
git clone https://github.com/fotoshowar/fotoshow-camara-windows.git
cd fotoshow-camara-windows
```

### 3. Instalar dependencias
```
pip install -r requirements.txt
```

### 4. Correr
```
python camara_web.py
```
Se abre el navegador automáticamente en `http://localhost:8081`

> Si pedí permisos de administrador, aceptá — lo necesita para manejar el WiFi con `netsh`.

---

## Uso

### Preparar la cámara
En la cámara Sony:
```
MENU → Network → Send to Smartphone
```
La cámara crea su red WiFi y muestra el nombre (`DIRECT-xxxx:ILCE-6000`) y contraseña en pantalla.

### Conectar desde la interfaz
1. Click **Conectar WiFi cámara**
2. Seleccioná la red `DIRECT-xxxx` de la lista
3. Ingresá la contraseña que muestra la cámara
4. Click **Conectar**

### Descargar fotos
1. Click **Actualizar fotos** — aparecen los thumbnails de todo lo que hay en la cámara
2. Click en las fotos que querés (se marcan en naranja)
3. Click **Descargar seleccionadas**
4. Las fotos quedan en `Mis Documentos\Pictures\FotoShow\`

---

## Notas

- La **contraseña del WiFi de la cámara cambia** cada vez que la cámara se reconecta a otra red. Siempre verificala en la pantalla de la cámara.
- Los archivos **RAW no se descargan** en su formato original — Sony solo expone un JPEG de preview vía WiFi.
- Los videos **.MTS se descargan completos**.
- Si el botón Conectar no funciona, conectate manualmente al WiFi de la cámara desde la bandeja de Windows y después usá solo el botón **Actualizar fotos**.
