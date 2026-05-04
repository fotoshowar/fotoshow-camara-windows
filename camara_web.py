#!/usr/bin/env python3
"""
FotoShow - Cámara Sony WiFi (Windows)
Instalar: pip install flask requests
Correr:   python camara_web.py
Abre el navegador automáticamente en http://localhost:8081
Para conectar la cámara: MENU → Network → Send to Smartphone
"""
from flask import Flask, jsonify, Response, send_file, abort, request
from xml.dom import minidom
import subprocess, os, threading, queue, mimetypes, tempfile
import requests as req
import webbrowser

app = Flask(__name__)

CAMERA_IP   = "10.0.0.1"
CAMERA_PORT = "64321"
FOTOS_DIR   = os.path.join(os.path.expanduser("~"), "Pictures", "FotoShow")

download_log   = queue.Queue()
download_state = {"running": False}
wifi_config    = {"ssid": "", "password": ""}

# ── WiFi (Windows via netsh) ──────────────────────────────────────────────────

def wifi_scan():
    r = subprocess.run(
        ["netsh", "wlan", "show", "networks", "mode=bssid"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    redes = []; seen = set(); ssid = None; signal = 0
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.upper().startswith("SSID") and ":" in line and "BSSID" not in line.upper():
            ssid = line.split(":", 1)[1].strip()
        elif "signal" in line.lower() or "señal" in line.lower():
            try:
                signal = int(line.split(":", 1)[1].strip().replace("%", ""))
            except:
                signal = 0
            if ssid and ssid not in seen:
                seen.add(ssid)
                redes.append({"ssid": ssid, "signal": signal})
            ssid = None; signal = 0
    redes.sort(key=lambda x: x["signal"], reverse=True)
    return redes

def wifi_connect(ssid, password):
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM><security>
        <authEncryption>
            <authentication>WPA2PSK</authentication>
            <encryption>AES</encryption>
            <useOneX>false</useOneX>
        </authEncryption>
        <sharedKey>
            <keyType>passPhrase</keyType>
            <protected>false</protected>
            <keyMaterial>{password}</keyMaterial>
        </sharedKey>
    </security></MSM>
</WLANProfile>"""
    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False, mode="w", encoding="utf-8")
    tmp.write(profile); tmp.close()
    try:
        subprocess.run(["netsh", "wlan", "delete", "profile", f"name={ssid}"], capture_output=True)
        subprocess.run(["netsh", "wlan", "add", "profile", f"filename={tmp.name}"], capture_output=True)
        r = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return r.returncode == 0, r.stdout + r.stderr
    finally:
        os.unlink(tmp.name)

def wifi_status(ssid):
    if not ssid:
        return False
    r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return ssid.lower() in r.stdout.lower()

# ── UPnP / cámara ─────────────────────────────────────────────────────────────

def upnp_browse(object_id="PhotoRoot", port=None):
    p = port or CAMERA_PORT
    try:
        r = req.post(
            f"http://{CAMERA_IP}:{p}/upnp/control/ContentDirectory",
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
                "Content-Type": 'text/xml; charset="utf-8"',
            },
            data=(
                '<?xml version="1.0"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                "<s:Body>"
                '<u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
                f"<ObjectID>{object_id}</ObjectID>"
                "<BrowseFlag>BrowseDirectChildren</BrowseFlag>"
                "<Filter>*</Filter>"
                "<StartingIndex>0</StartingIndex>"
                "<RequestedCount>9999</RequestedCount>"
                "<SortCriteria></SortCriteria>"
                "</u:Browse>"
                "</s:Body>"
                "</s:Envelope>"
            ),
            timeout=8,
        )
    except Exception as e:
        return None, str(e)

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    dom = minidom.parseString(r.text)
    results = []
    for el in dom.getElementsByTagName("Result"):
        inner = minidom.parseString(el.firstChild.nodeValue)
        for c in inner.getElementsByTagName("container"):
            title = c.getElementsByTagName("dc:title")[0].firstChild.nodeValue
            results.append({"type": "dir", "id": c.attributes["id"].value, "title": title})
        for item in inner.getElementsByTagName("item"):
            filename = item.getElementsByTagName("dc:title")[0].firstChild.nodeValue
            resources = item.getElementsByTagName("res")
            best_url = None; best_size = 0; thumb_url = None
            for res in resources:
                proto = res.attributes.get("protocolInfo")
                pv = proto.value if proto else ""
                sa = res.attributes.get("size")
                size = int(sa.value) if sa else 0
                url = res.firstChild.nodeValue if res.firstChild else None
                if "_TN" in pv or "_SM" in pv:
                    if not thumb_url: thumb_url = url
                if "_LRG" in pv: thumb_url = url
                if size > best_size and url:
                    best_url = url; best_size = size
            if not best_url and resources:
                best_url = resources[-1].firstChild.nodeValue if resources[-1].firstChild else None
            results.append({"type": "photo", "filename": filename,
                            "url": best_url, "thumb": thumb_url or best_url, "size": best_size})
    return results, None

def detect_port():
    for port in [CAMERA_PORT, "60151", "60152", "60153", "60154", "60155"]:
        try:
            r = req.get(f"http://{CAMERA_IP}:{port}/DmsDescPush.xml", timeout=3)
            if r.status_code == 200:
                return port
        except:
            pass
    return None

# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FotoShow — Cámara Sony</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0 }
  :root {
    --bg:#0f0f0f; --surface:#1a1a1a; --border:#2a2a2a;
    --text:#e8e8e8; --muted:#888; --accent:#f5a623;
    --green:#4caf50; --red:#f44336; --blue:#2196f3;
  }
  body { background:var(--bg); color:var(--text); font-family:system-ui,sans-serif; min-height:100vh }
  header {
    background:var(--surface); border-bottom:1px solid var(--border);
    padding:14px 24px; display:flex; align-items:center; gap:20px; flex-wrap:wrap;
  }
  header h1 { font-size:18px; font-weight:600; color:var(--accent); flex:1 }
  .status-bar { display:flex; gap:16px; align-items:center; flex-wrap:wrap }
  .badge { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--muted) }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--red) }
  .dot.on { background:var(--green) }
  .dot.pulse { background:var(--blue); animation:pulse 1s infinite }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .tabs { display:flex; border-bottom:1px solid var(--border); padding:0 24px }
  .tab {
    padding:12px 20px; cursor:pointer; font-size:14px; font-weight:600;
    color:var(--muted); border-bottom:2px solid transparent; margin-bottom:-1px;
  }
  .tab.active { color:var(--accent); border-bottom-color:var(--accent) }
  .panel { display:none; padding:20px 24px }
  .panel.active { display:block }
  .actions { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px }
  button {
    padding:10px 22px; border:none; border-radius:8px; cursor:pointer;
    font-size:14px; font-weight:600; transition:opacity .15s
  }
  button:hover { opacity:.85 }
  button:disabled { opacity:.4; cursor:not-allowed }
  .btn-connect { background:var(--blue); color:#fff }
  .btn-dl      { background:var(--accent); color:#000 }
  .btn-sec     { background:var(--border); color:var(--text) }
  #log-box {
    background:#000; border:1px solid var(--border); border-radius:8px;
    padding:14px; font-family:monospace; font-size:13px;
    height:160px; overflow-y:auto; color:#9effa0; margin-bottom:16px; display:none;
  }
  #log-box.visible { display:block }
  #log-box p { line-height:1.6 }
  .photo-grid {
    display:grid; grid-template-columns:repeat(auto-fill, minmax(160px, 1fr)); gap:10px;
  }
  .photo-card {
    background:var(--surface); border-radius:8px; overflow:hidden;
    position:relative; cursor:pointer; transition:transform .15s; border:2px solid transparent;
  }
  .photo-card:hover { transform:scale(1.02) }
  .photo-card.selected { border-color:var(--accent) }
  .photo-card img { width:100%; height:140px; object-fit:cover; display:block }
  .photo-card .filename {
    font-size:11px; color:var(--muted); padding:6px 8px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .photo-card .check {
    position:absolute; top:6px; right:6px; width:22px; height:22px; border-radius:50%;
    background:rgba(0,0,0,.6); border:2px solid #fff;
    display:flex; align-items:center; justify-content:center; font-size:12px; color:#fff;
  }
  .photo-card.selected .check { background:var(--accent); border-color:var(--accent); color:#000 }
  .video-card {
    background:var(--surface); border-radius:8px; overflow:hidden;
    display:flex; flex-direction:column; align-items:center; justify-content:center; height:165px;
    cursor:pointer;
  }
  .video-card .icon { font-size:36px; margin-bottom:6px }
  .video-card .filename { font-size:11px; color:var(--muted); padding:0 8px; text-align:center }
  .sel-bar {
    display:flex; align-items:center; justify-content:space-between; margin-bottom:14px;
    flex-wrap:wrap; gap:10px;
  }
  .sel-count { font-size:13px; color:var(--muted) }
  #lightbox {
    display:none; position:fixed; inset:0; background:rgba(0,0,0,.92);
    z-index:100; align-items:center; justify-content:center; flex-direction:column;
  }
  #lightbox.visible { display:flex }
  #lightbox img { max-width:92vw; max-height:82vh; object-fit:contain; border-radius:4px }
  #lightbox-bar { margin-top:14px; display:flex; gap:14px; align-items:center }
  #lightbox-name { color:var(--muted); font-size:13px }
  #lightbox-close {
    position:absolute; top:18px; right:22px;
    background:none; border:none; color:var(--text); font-size:28px; cursor:pointer;
  }
  #lightbox-dl {
    padding:8px 18px; background:var(--accent); color:#000;
    border:none; border-radius:6px; font-weight:600; cursor:pointer;
    text-decoration:none; font-size:13px;
  }
  .empty { color:var(--muted); font-size:14px; padding:40px 0; text-align:center }
  .day-title {
    font-size:15px; font-weight:600; color:var(--muted);
    margin:20px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--border);
  }
  .day-title:first-child { margin-top:0 }
  #modal-bg {
    display:none; position:fixed; inset:0; background:rgba(0,0,0,.7);
    z-index:200; align-items:center; justify-content:center;
  }
  #modal-bg.visible { display:flex }
  #modal {
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:24px; width:360px; max-width:94vw;
  }
  #modal h2 { font-size:16px; margin-bottom:18px; color:var(--accent) }
  .redes-lista {
    max-height:200px; overflow-y:auto; margin-bottom:14px;
    border:1px solid var(--border); border-radius:8px;
  }
  .red-item {
    padding:10px 14px; cursor:pointer; display:flex; justify-content:space-between;
    align-items:center; font-size:14px; border-bottom:1px solid var(--border);
  }
  .red-item:last-child { border-bottom:none }
  .red-item:hover { background:rgba(255,255,255,.05) }
  .red-item.seleccionada { background:rgba(245,166,35,.1); color:var(--accent) }
  .red-signal { font-size:12px; color:var(--muted) }
  .campo { margin-bottom:14px }
  .campo label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px }
  .campo input {
    width:100%; padding:9px 12px; background:var(--bg); border:1px solid var(--border);
    border-radius:6px; color:var(--text); font-size:14px;
  }
  .campo input:focus { outline:none; border-color:var(--accent) }
  .modal-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:6px }
</style>
</head>
<body>
<header>
  <h1>FotoShow — Cámara Sony</h1>
  <div class="status-bar">
    <div class="badge"><div class="dot" id="dot-wifi"></div><span id="lbl-wifi">WiFi</span></div>
    <div class="badge"><div class="dot" id="dot-cam"></div><span id="lbl-cam">Cámara</span></div>
    <div class="badge"><div class="dot" id="dot-dl"></div><span id="lbl-dl">En espera</span></div>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab('camara')">Fotos en cámara</div>
  <div class="tab" onclick="switchTab('descargadas')">Descargadas</div>
</div>

<div class="panel active" id="tab-camara">
  <div class="actions">
    <button class="btn-connect" onclick="abrirModalWifi()">Conectar WiFi cámara</button>
    <button class="btn-sec" onclick="cargarCamara()">Actualizar fotos</button>
  </div>
  <div id="log-box"></div>
  <div class="sel-bar">
    <span class="sel-count" id="sel-count">0 seleccionadas</span>
    <div style="display:flex;gap:8px">
      <button class="btn-sec" onclick="seleccionarTodas()">Todas</button>
      <button class="btn-sec" onclick="deseleccionarTodas()">Ninguna</button>
      <button class="btn-dl" id="btn-dl-sel" onclick="descargarSeleccion()" disabled>Descargar seleccionadas</button>
    </div>
  </div>
  <div class="photo-grid" id="grid-camara">
    <p class="empty" style="grid-column:1/-1">Conectá la cámara y presioná Actualizar fotos.</p>
  </div>
</div>

<div class="panel" id="tab-descargadas">
  <div class="actions">
    <button class="btn-sec" onclick="cargarDescargadas()">Actualizar galería</button>
  </div>
  <div id="gallery"></div>
</div>

<div id="modal-bg">
  <div id="modal">
    <h2>Conectar WiFi de cámara</h2>
    <div class="redes-lista" id="redes-lista">
      <div class="red-item" style="color:var(--muted);cursor:default">Escaneando redes...</div>
    </div>
    <div class="campo">
      <label>Red seleccionada</label>
      <input type="text" id="input-ssid" placeholder="Nombre de la red (SSID)">
    </div>
    <div class="campo">
      <label>Contraseña</label>
      <input type="password" id="input-pass" placeholder="Contraseña WiFi">
    </div>
    <div class="modal-actions">
      <button class="btn-sec" onclick="cerrarModal()">Cancelar</button>
      <button class="btn-connect" onclick="conectarWifi()">Conectar</button>
    </div>
  </div>
</div>

<div id="lightbox">
  <button id="lightbox-close" onclick="cerrarLightbox()">✕</button>
  <img id="lightbox-img" src="" alt="">
  <div id="lightbox-bar">
    <span id="lightbox-name"></span>
    <a id="lightbox-dl" href="#" download>Descargar</a>
  </div>
</div>

<script>
const log = document.getElementById('log-box')
let fotosEnCamara = []
let seleccionadas = new Set()

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', ['camara','descargadas'][i]===name))
  document.querySelectorAll('.panel').forEach((p,i) => p.classList.toggle('active', ['tab-camara','tab-descargadas'][i]==='tab-'+name))
  if (name==='descargadas') cargarDescargadas()
}

async function checkStatus() {
  try {
    const d = await fetch('/api/status').then(r=>r.json())
    document.getElementById('dot-wifi').className = 'dot '+(d.wifi?'on':'')
    document.getElementById('lbl-wifi').textContent = d.wifi ? 'WiFi cámara' : 'WiFi desconectado'
    document.getElementById('dot-cam').className = 'dot '+(d.camera?'on':'')
    document.getElementById('lbl-cam').textContent = d.camera ? 'Cámara online' : 'Cámara offline'
    document.getElementById('dot-dl').className = 'dot '+(d.downloading?'pulse':'')
    document.getElementById('lbl-dl').textContent = d.downloading ? 'Descargando...' : 'En espera'
  } catch(e){}
}

async function abrirModalWifi() {
  document.getElementById('modal-bg').classList.add('visible')
  const cfg = await fetch('/api/wifi/config').then(r=>r.json())
  document.getElementById('input-ssid').value = cfg.ssid || ''
  document.getElementById('input-pass').value = cfg.password || ''
  escanearRedes()
}
function cerrarModal() { document.getElementById('modal-bg').classList.remove('visible') }

async function escanearRedes() {
  document.getElementById('redes-lista').innerHTML = '<div class="red-item" style="color:var(--muted);cursor:default">Escaneando...</div>'
  const redes = await fetch('/api/wifi/scan').then(r=>r.json())
  const lista = document.getElementById('redes-lista')
  if (!redes.length) { lista.innerHTML = '<div class="red-item" style="color:var(--muted);cursor:default">No se encontraron redes</div>'; return }
  lista.innerHTML = ''
  const ssidActual = document.getElementById('input-ssid').value
  redes.forEach(red => {
    const div = document.createElement('div')
    div.className = 'red-item'+(red.ssid===ssidActual?' seleccionada':'')
    div.innerHTML = `<span>${red.ssid}</span><span class="red-signal">${barras(red.signal)} ${red.signal}%</span>`
    div.onclick = () => {
      document.getElementById('input-ssid').value = red.ssid
      document.querySelectorAll('.red-item').forEach(d=>d.classList.remove('seleccionada'))
      div.classList.add('seleccionada')
      document.getElementById('input-pass').focus()
    }
    lista.appendChild(div)
  })
}
function barras(s) { return s>75?'▂▄▆█':s>50?'▂▄▆_':s>25?'▂▄__':'▂___' }

async function conectarWifi() {
  const ssid = document.getElementById('input-ssid').value.trim()
  const pass = document.getElementById('input-pass').value
  if (!ssid) return
  cerrarModal(); mostrarLog()
  agregarLog(`Conectando a "${ssid}"...`)
  const d = await fetch('/api/connect', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ssid, password:pass})}).then(r=>r.json())
  agregarLog(d.ok ? '✓ Conectado. Cargando fotos...' : '✗ Error: '+d.output)
  if (d.ok) { checkStatus(); setTimeout(cargarCamara, 2000) }
}

async function cargarCamara() {
  const grid = document.getElementById('grid-camara')
  grid.innerHTML = '<p class="empty" style="grid-column:1/-1">Conectando con la cámara...</p>'
  seleccionadas.clear(); actualizarContador()
  const r = await fetch('/api/camara/fotos')
  const d = await r.json()
  fotosEnCamara = d.fotos || []
  if (d.error) { grid.innerHTML = `<p class="empty" style="grid-column:1/-1">Error: ${d.error}</p>`; return }
  if (!fotosEnCamara.length) { grid.innerHTML = '<p class="empty" style="grid-column:1/-1">No hay fotos en la cámara.</p>'; return }
  grid.innerHTML = ''
  fotosEnCamara.forEach((foto, idx) => {
    const ext = foto.filename.split('.').pop().toLowerCase()
    const card = document.createElement('div')
    if (['mts','mp4','mov','avi'].includes(ext)) {
      card.className = 'video-card photo-card'
      card.innerHTML = `<div class="check">✓</div><div class="icon">🎬</div><div class="filename">${foto.filename}</div>`
    } else {
      card.className = 'photo-card'
      card.innerHTML = `<div class="check">✓</div><img src="/api/camara/thumb?url=${encodeURIComponent(foto.thumb)}" loading="lazy"><div class="filename">${foto.filename}</div>`
    }
    card.dataset.idx = idx
    card.addEventListener('click', () => toggleSeleccion(idx, card))
    grid.appendChild(card)
  })
}

function toggleSeleccion(idx, card) {
  if (seleccionadas.has(idx)) { seleccionadas.delete(idx); card.classList.remove('selected') }
  else { seleccionadas.add(idx); card.classList.add('selected') }
  actualizarContador()
}
function seleccionarTodas() {
  document.querySelectorAll('#grid-camara .photo-card').forEach(card => { seleccionadas.add(parseInt(card.dataset.idx)); card.classList.add('selected') })
  actualizarContador()
}
function deseleccionarTodas() {
  seleccionadas.clear()
  document.querySelectorAll('#grid-camara .photo-card').forEach(c => c.classList.remove('selected'))
  actualizarContador()
}
function actualizarContador() {
  const n = seleccionadas.size
  document.getElementById('sel-count').textContent = n ? `${n} seleccionada${n>1?'s':''}` : '0 seleccionadas'
  document.getElementById('btn-dl-sel').disabled = n === 0
}

async function descargarSeleccion() {
  if (!seleccionadas.size) return
  const urls = [...seleccionadas].map(i => fotosEnCamara[i].url)
  const names = [...seleccionadas].map(i => fotosEnCamara[i].filename)
  mostrarLog(); agregarLog(`Descargando ${urls.length} foto(s)...`)
  document.getElementById('btn-dl-sel').disabled = true
  const r = await fetch('/api/camara/descargar', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({urls, names})}).then(r=>r.json())
  if (r.ok) escucharStream()
  else agregarLog('Error: '+r.error)
}
function escucharStream() {
  const es = new EventSource('/api/stream')
  es.onmessage = e => {
    if (e.data==='[FIN]') { agregarLog('— Descarga completada —'); es.close(); checkStatus() }
    else agregarLog(e.data)
  }
  es.onerror = () => es.close()
}

async function cargarDescargadas() {
  const datos = await fetch('/api/fotos').then(r=>r.json())
  const gallery = document.getElementById('gallery')
  gallery.innerHTML = ''
  const dias = Object.keys(datos).sort().reverse()
  if (!dias.length) { gallery.innerHTML = '<p class="empty">No hay fotos descargadas todavía.</p>'; return }
  for (const dia of dias) {
    const title = document.createElement('div'); title.className = 'day-title'
    title.textContent = `${dia} — ${datos[dia].length} archivo(s)`; gallery.appendChild(title)
    const grid = document.createElement('div'); grid.className = 'photo-grid'
    for (const archivo of datos[dia]) {
      const ext = archivo.split('.').pop().toLowerCase()
      const path = dia+'/'+archivo
      if (['jpg','jpeg','png'].includes(ext)) {
        const card = document.createElement('div'); card.className = 'photo-card'
        card.innerHTML = `<img src="/foto/${path}" loading="lazy"><div class="filename">${archivo}</div>`
        card.onclick = () => abrirLightbox('/foto/'+path, archivo); grid.appendChild(card)
      } else {
        const card = document.createElement('div'); card.className = 'video-card'
        card.innerHTML = `<div class="icon">🎬</div><div class="filename">${archivo}</div>`
        card.onclick = () => window.open('/foto/'+path); grid.appendChild(card)
      }
    }
    gallery.appendChild(grid)
  }
}

function abrirLightbox(src, nombre) {
  document.getElementById('lightbox-img').src = src
  document.getElementById('lightbox-name').textContent = nombre
  document.getElementById('lightbox-dl').href = src
  document.getElementById('lightbox-dl').download = nombre
  document.getElementById('lightbox').classList.add('visible')
}
function cerrarLightbox() { document.getElementById('lightbox').classList.remove('visible'); document.getElementById('lightbox-img').src = '' }
document.getElementById('lightbox').addEventListener('click', e => { if(e.target===document.getElementById('lightbox')) cerrarLightbox() })

function mostrarLog() { log.classList.add('visible') }
function agregarLog(t) { const p=document.createElement('p'); p.textContent=t; log.appendChild(p); log.scrollTop=log.scrollHeight }

checkStatus()
setInterval(checkStatus, 4000)
</script>
</body>
</html>"""

# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/")
def index(): return HTML

@app.route("/api/status")
def status():
    connected = wifi_status(wifi_config["ssid"])
    try: req.get(f"http://{CAMERA_IP}:{CAMERA_PORT}/DmsDescPush.xml", timeout=2); camera = True
    except: camera = False
    return jsonify({"wifi": connected, "camera": camera, "downloading": download_state["running"]})

@app.route("/api/wifi/config")
def wifi_config_get(): return jsonify({"ssid": wifi_config["ssid"], "password": wifi_config["password"]})

@app.route("/api/wifi/scan")
def api_wifi_scan(): return jsonify(wifi_scan())

@app.route("/api/connect", methods=["POST"])
def connect():
    data = request.get_json(silent=True) or {}
    ssid = data.get("ssid", ""); password = data.get("password", "")
    wifi_config["ssid"] = ssid; wifi_config["password"] = password
    ok, output = wifi_connect(ssid, password)
    return jsonify({"ok": ok, "output": output})

@app.route("/api/camara/fotos")
def camara_fotos():
    port = detect_port()
    if not port: return jsonify({"fotos": [], "error": "No se puede conectar a la cámara"})
    fotos = []
    def walk(oid, p):
        items, _ = upnp_browse(oid, p)
        if not items: return
        for item in items:
            if item["type"] == "dir": walk(item["id"], p)
            else: fotos.append(item)
    walk("PhotoRoot", port)
    return jsonify({"fotos": fotos})

@app.route("/api/camara/thumb")
def camara_thumb():
    url = request.args.get("url", "")
    if not url or CAMERA_IP not in url: abort(400)
    try:
        r = req.get(url, timeout=10, stream=True)
        return Response(r.content, mimetype=r.headers.get("content-type", "image/jpeg"))
    except: abort(502)

@app.route("/api/camara/descargar", methods=["POST"])
def camara_descargar():
    if download_state["running"]: return jsonify({"ok": False, "error": "Ya está descargando"})
    data = request.json
    urls = data.get("urls", []); names = data.get("names", [])
    def run():
        download_state["running"] = True
        from datetime import date
        today = date.today()
        day_dir = os.path.join(FOTOS_DIR, f"{today.year}-{today.month}-{today.day}")
        os.makedirs(day_dir, exist_ok=True)
        for url, name in zip(urls, names):
            filepath = os.path.join(day_dir, name)
            if os.path.isfile(filepath): download_log.put(f"Saltea: {name}"); continue
            download_log.put(f"Descargando: {name}")
            try:
                with req.get(url, stream=True, timeout=60) as r:
                    if r.status_code == 200:
                        with open(filepath, "wb") as f:
                            for chunk in r.iter_content(chunk_size=16384): f.write(chunk)
                        download_log.put(f"✓ {name}")
                    else: download_log.put(f"✗ Error {r.status_code}: {name}")
            except Exception as e: download_log.put(f"✗ {name}: {e}")
        download_log.put("[FIN]"); download_state["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/stream")
def stream():
    def generate():
        while True:
            msg = download_log.get(); yield f"data: {msg}\n\n"
            if msg == "[FIN]": break
    return Response(generate(), mimetype="text/event-stream")

@app.route("/api/fotos")
def fotos():
    result = {}
    if not os.path.isdir(FOTOS_DIR): return jsonify(result)
    for day in sorted(os.listdir(FOTOS_DIR), reverse=True):
        path = os.path.join(FOTOS_DIR, day)
        if os.path.isdir(path):
            files = sorted(f for f in os.listdir(path) if not f.startswith("."))
            if files: result[day] = files
    return jsonify(result)

@app.route("/foto/<path:filepath>")
def serve_foto(filepath):
    full = os.path.realpath(os.path.join(FOTOS_DIR, filepath))
    if not full.startswith(os.path.realpath(FOTOS_DIR)): abort(403)
    if not os.path.isfile(full): abort(404)
    return send_file(full, mimetype=mimetypes.guess_type(full)[0] or "application/octet-stream")

if __name__ == "__main__":
    os.makedirs(FOTOS_DIR, exist_ok=True)
    print(f"Las fotos se guardan en: {FOTOS_DIR}")
    print("Abriendo navegador en http://localhost:8081 ...")
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8081")).start()
    app.run(host="0.0.0.0", port=8081, debug=False)
