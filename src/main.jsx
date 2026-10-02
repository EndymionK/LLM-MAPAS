import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import L from "leaflet";
import { fromArrayBuffer } from "geotiff";
import "leaflet/dist/leaflet.css";
import "./styles.css";

const COLOMBIA = [4.5709, -74.2973];
const PRESETS = {
  "Atlas nocturno": { palette: "gray", contours: true, fill: false, shade: 0, color: "#d9d8d0", width: 0.72, count: 12, exaggeration: 1, background: [16, 18, 18], grid: true, gridColor: "#6f7470", gridOpacity: 0.32, labels: true, majorEvery: 5 },
  "Relieve natural": { palette: "terrain", contours: true, fill: true, shade: 0.28, color: "#26342f", width: 0.8, count: 14, exaggeration: 1 },
  "Lectura altimetrica": { palette: "viridis", contours: true, fill: true, shade: 0.12, color: "#14221f", width: 0.65, count: 16, exaggeration: 1.15 },
  "Curvas de campo": { palette: "gray", contours: true, fill: false, shade: 0, color: "#f1ead7", width: 1, count: 18, exaggeration: 1 },
  "Contraste editorial": { palette: "gist_earth", contours: true, fill: true, shade: 0.42, color: "#111716", width: 0.55, count: 12, exaggeration: 1.4 },
  "Lineas blancas": { palette: "gray", contours: true, fill: false, shade: 0, color: "#ffffff", width: 0.9, count: 16, exaggeration: 1, background: [5, 5, 5] },
};
const initialStyle = { ...PRESETS["Atlas nocturno"], preset: "Atlas nocturno", transparent: false, interval: 20, azimuth: 315, altitude: 45, routeColor: "#ef4444", routeWidth: 4 };

function parseRouteFile(text, extension) {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.querySelector("parsererror")) throw new Error("El archivo de ruta no tiene un XML valido.");
  const elements = [...document.getElementsByTagName("*")];
  const points = extension === "gpx"
    ? elements.filter((element) => element.localName === "trkpt" || element.localName === "rtept").map((point) => [Number(point.getAttribute("lat")), Number(point.getAttribute("lon"))])
    : elements.filter((element) => element.localName === "coordinates").flatMap((node) => node.textContent.trim().split(/\s+/).map((item) => item.split(",").slice(0, 2).map(Number).reverse()));
  const valid = points.filter(([latitude, longitude]) => Number.isFinite(latitude) && Number.isFinite(longitude));
  if (valid.length < 2) throw new Error("La ruta debe contener al menos dos coordenadas.");
  return valid;
}

function routeBounds(points) {
  let west = Infinity;
  let east = -Infinity;
  let south = Infinity;
  let north = -Infinity;
  for (const [latitude, longitude] of points) {
    west = Math.min(west, longitude);
    east = Math.max(east, longitude);
    south = Math.min(south, latitude);
    north = Math.max(north, latitude);
  }
  const longitudeMargin = Math.max((east - west) * 0.14, 0.04);
  const latitudeMargin = Math.max((north - south) * 0.14, 0.04);
  return { west: Math.max(-79.1, west - longitudeMargin), south: Math.max(-4.3, south - latitudeMargin), east: Math.min(-66.8, east + longitudeMargin), north: Math.min(13.6, north + latitudeMargin) };
}

function App() {
  const mapRef = useRef(null);
  const selectionRef = useRef(null);
  const routeLayerRef = useRef(null);
  const [map, setMap] = useState(null);
  const [bounds, setBounds] = useState(null);
  const [style, setStyle] = useState(initialStyle);
  const autoRefresh = true;
  const [route, setRoute] = useState(null);
  const [status, setStatus] = useState("Dibuja un rectangulo sobre Colombia para comenzar");
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [previewZoom, setPreviewZoom] = useState(1);

  useEffect(() => {
    const instance = L.map(mapRef.current, { zoomControl: false, minZoom: 3, maxZoom: 16 }).setView(COLOMBIA, 6);
    L.control.zoom({ position: "bottomright" }).addTo(instance);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap contributors", maxZoom: 19 }).addTo(instance);
    let start = null;
    let rightSelecting = false;
    let activePointerId = null;
    const pointToLatLng = (event) => {
      const rect = instance.getContainer().getBoundingClientRect();
      return instance.containerPointToLatLng(L.point(event.clientX - rect.left, event.clientY - rect.top));
    };
    const updateSelection = (event) => {
      if (start && rightSelecting && (activePointerId === null || event.pointerId === activePointerId) && selectionRef.current) {
        selectionRef.current.setBounds([start, pointToLatLng(event)]);
      }
    };
    const finishSelection = (event) => {
      if (!start || !rightSelecting || (activePointerId !== null && event.pointerId != null && event.pointerId !== activePointerId)) return;
      const selected = L.latLngBounds(start, pointToLatLng(event));
      start = null;
      rightSelecting = false;
      activePointerId = null;
      instance.dragging.enable();
      window.removeEventListener("pointermove", updateSelection);
      window.removeEventListener("pointerup", finishSelection, true);
      window.removeEventListener("pointercancel", finishSelection, true);
      window.removeEventListener("mouseup", finishSelection, true);
      if (selected.getNorth() - selected.getSouth() < 0.03 || selected.getEast() - selected.getWest() < 0.03) return setStatus("Amplia un poco el area seleccionada");
      const next = { west: selected.getWest(), south: selected.getSouth(), east: selected.getEast(), north: selected.getNorth() };
      setBounds(next);
      setStatus("Area lista. Preparada para generar la preview.");
      if (autoRefresh) render(next, style);
    };
    const startSelection = (event) => {
      if (event.button !== 2) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      instance.dragging.disable();
      rightSelecting = true;
      activePointerId = event.pointerId;
      start = pointToLatLng(event);
      if (selectionRef.current) selectionRef.current.remove();
      selectionRef.current = L.rectangle([start, start], { color: "#f4c95d", weight: 2, fillOpacity: 0.12 }).addTo(instance);
      window.addEventListener("pointermove", updateSelection);
      window.addEventListener("pointerup", finishSelection, true);
      window.addEventListener("pointercancel", finishSelection, true);
      window.addEventListener("mouseup", finishSelection, true);
    };
    instance.getContainer().addEventListener("pointerdown", startSelection, true);
    instance.getContainer().addEventListener("contextmenu", (event) => event.preventDefault(), true);
    setMap(instance);
    return () => {
      window.removeEventListener("pointermove", updateSelection);
      window.removeEventListener("pointerup", finishSelection, true);
      window.removeEventListener("pointercancel", finishSelection, true);
      window.removeEventListener("mouseup", finishSelection, true);
      instance.getContainer().removeEventListener("pointerdown", startSelection, true);
      instance.remove();
    };
  }, []);

  useEffect(() => { if (map) map.invalidateSize(); }, [map]);

  async function render(nextBounds = bounds, nextStyle = style, nextRoute = route) {
    if (!nextBounds) return setStatus("Selecciona primero un area en el mapa");
    setLoading(true); setStatus("Descargando DEM y construyendo preview...");
    try {
      const response = await fetch("/api/dem", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nextBounds) });
      if (!response.ok) {
        const payload = await response.text();
        let message = payload || "Error de descarga";
        try { message = JSON.parse(payload).error || message; } catch { /* response may be plain text */ }
        throw new Error(message);
      }
      const raster = await (await fromArrayBuffer(await response.arrayBuffer())).getImage();
      const rasters = await raster.readRasters({ interleave: true });
      const rendered = renderRaster(rasters, raster.getWidth(), raster.getHeight(), nextStyle, nextBounds, nextRoute);
      setImage({ url: rendered, width: raster.getWidth(), height: raster.getHeight() });
      setPreviewZoom(1);
      setStatus("Preview lista. Ajusta el estilo y exporta cuando quieras.");
    } catch (error) { setStatus(error.message); }
    finally { setLoading(false); }
  }

  function updateGeneral(key, value) {
    const next = { ...style, [key]: value };
    setStyle(next);
    if (autoRefresh && bounds) render(bounds, next);
  }
  function updateImageStyle(key, value) {
    const next = { ...style, [key]: value };
    setStyle(next);
    if (routeLayerRef.current && (key === "routeColor" || key === "routeWidth")) {
      routeLayerRef.current.setStyle({ color: next.routeColor, weight: next.routeWidth });
    }
    if (autoRefresh && bounds) render(bounds, next, route);
  }
  function applyPreset(name) { const next = { ...style, ...PRESETS[name], preset: name }; setStyle(next); if (bounds) render(bounds, next, route); }
  function changePreviewZoom(delta) {
    setPreviewZoom((current) => Math.max(0.25, Math.min(4, Number((current + delta).toFixed(2)))));
  }
  function handlePreviewWheel(event) {
    event.preventDefault();
    changePreviewZoom(event.deltaY < 0 ? 0.15 : -0.15);
  }
  function handleRouteUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const extension = file.name.toLowerCase().endsWith(".kml") ? "kml" : "gpx";
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const points = parseRouteFile(String(reader.result), extension);
        const nextBounds = routeBounds(points);
        const nextRoute = { points, name: file.name };
        setRoute(nextRoute);
        setBounds(nextBounds);
        if (selectionRef.current) selectionRef.current.remove();
        if (routeLayerRef.current) routeLayerRef.current.remove();
        if (map) {
          selectionRef.current = L.rectangle([[nextBounds.south, nextBounds.west], [nextBounds.north, nextBounds.east]], { color: "#f4c95d", weight: 2, fillOpacity: 0.08 }).addTo(map);
          routeLayerRef.current = L.polyline(points, { color: style.routeColor, weight: style.routeWidth, opacity: 0.9 }).addTo(map);
          map.fitBounds([[nextBounds.south, nextBounds.west], [nextBounds.north, nextBounds.east]], { padding: [24, 24] });
        }
        setStatus(`Ruta cargada: ${file.name}. Generando area con margen.`);
        render(nextBounds, style, nextRoute);
      } catch (error) { setStatus(error.message); }
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  return <main className="app-shell">
    <header className="topbar"><div><p className="eyebrow">TOPOGRAFIA / COLOMBIA</p><h1>TopoMapas</h1></div><div className="top-status"><span className={loading ? "pulse" : "dot"}></span>{status}</div></header>
    <section className="workspace">
      <div className="map-pane"><div ref={mapRef} className="map" style={{ touchAction: "none", userSelect: "none", WebkitUserSelect: "none" }} onContextMenu={(event) => event.preventDefault()} onDragStart={(event) => event.preventDefault()}></div><div className="map-note"><strong>Area de estudio</strong><span>Arrastra con el boton derecho para seleccionar</span></div></div>
      <aside className="preview-pane">
        <div className="preview-heading"><div><p className="eyebrow">SALIDA VISUAL</p><h2>Previsualizacion</h2></div><span className="badge">{style.preset}</span></div>
        <div className="canvas-wrap" onWheel={handlePreviewWheel}>{image ? <><img src={image.url} alt="Previsualizacion topografica" style={{ width: `${previewZoom * 100}%`, height: `${previewZoom * 100}%` }} /><div className="zoom-controls" aria-label="Controles de zoom"><button type="button" onClick={() => changePreviewZoom(0.25)} aria-label="Acercar">+</button><button type="button" onClick={() => changePreviewZoom(-0.25)} aria-label="Alejar">−</button><button type="button" onClick={() => setPreviewZoom(1)} aria-label="Restablecer zoom">1:1</button></div><span className="zoom-level">{Math.round(previewZoom * 100)}%</span></> : <div className="empty-state"><span className="contour-mark">⌁</span><strong>Tu relieve aparecera aqui</strong><span>Selecciona una zona en el mapa para cargar el modelo de elevacion.</span></div>}</div>
        <div className="controls">
          <div className="preset-toolbar"><span className="section-label">Estilos</span><label className="route-upload">Subir GPX/KML<input type="file" accept=".gpx,.kml,application/gpx+xml,application/vnd.google-earth.kml+xml" onChange={handleRouteUpload} /></label></div>
          <div className="quick-panel"><div className="preset-grid">{Object.keys(PRESETS).map((name) => <button key={name} className={style.preset === name ? "preset selected" : "preset"} onClick={() => applyPreset(name)}><span className={`swatch swatch-${PRESETS[name].palette}`} style={name === "Lineas blancas" ? { background: "repeating-linear-gradient(135deg, #050505 0 5px, #ffffff 5px 6px)" } : name === "Atlas nocturno" ? { background: "repeating-linear-gradient(135deg, #101212 0 6px, #d9d8d0 6px 7px)" } : undefined}></span><span>{name}</span></button>)}</div><label className="range-label"><span>Cantidad de lineas <output>{style.count}</output></span><input type="range" min="4" max="50" value={style.count} onChange={(event) => updateGeneral("count", Number(event.target.value))} /></label><label className="range-label"><span>Exageracion del relieve <output>{style.exaggeration.toFixed(1)}x</output></span><input type="range" min="0.5" max="3" step="0.1" value={style.exaggeration} onChange={(event) => updateGeneral("exaggeration", Number(event.target.value))} /></label>{route && <div className="route-summary"><strong>{route.name}</strong><span>{route.points.length} puntos · margen automatico</span></div>}</div>
          <Advanced style={style} updateStyle={updateImageStyle} hasRoute={Boolean(route)} />
          <div className="control-actions"><button onClick={() => render()} disabled={loading || !bounds} className="primary">{loading ? "Generando..." : "Regenerar preview"}</button>{image && <a className="download" href={image.url} download="topomapas-colombia.png">Descargar PNG</a>}</div>
        </div>
      </aside>
    </section>
  </main>;
}

function Advanced({ style, updateStyle, hasRoute }) {
  return <details className="advanced-options"><summary>Opciones de imagen</summary><div className="advanced-grid"><label>Paleta<select value={style.palette} onChange={(event) => updateStyle("palette", event.target.value)}><option>terrain</option><option>viridis</option><option>gist_earth</option><option>gray</option></select></label><label>Intervalo (m)<input type="number" min="1" max="1000" value={style.interval} onChange={(event) => updateStyle("interval", Number(event.target.value))} /></label><label>Grosor curvas<input type="number" min="0.1" max="5" step="0.1" value={style.width} onChange={(event) => updateStyle("width", Number(event.target.value))} /></label><label>Opacidad sombra<input type="range" min="0" max="1" step="0.05" value={style.shade} onChange={(event) => updateStyle("shade", Number(event.target.value))} /></label><label>Azimut<input type="number" min="0" max="360" value={style.azimuth} onChange={(event) => updateStyle("azimuth", Number(event.target.value))} /></label><label>Altitud<input type="number" min="0" max="90" value={style.altitude} onChange={(event) => updateStyle("altitude", Number(event.target.value))} /></label><label className={!hasRoute ? "disabled-control" : ""}>Color de ruta<input type="color" value={style.routeColor} disabled={!hasRoute} onChange={(event) => updateStyle("routeColor", event.target.value)} /></label><label className={!hasRoute ? "disabled-control" : ""}>Grosor de ruta<input type="number" min="1" max="12" step="0.5" value={style.routeWidth} disabled={!hasRoute} onChange={(event) => updateStyle("routeWidth", Number(event.target.value))} /></label><label className="checks"><span><input type="checkbox" checked={style.fill} onChange={(event) => updateStyle("fill", event.target.checked)} /> Relleno</span><span><input type="checkbox" checked={style.contours} onChange={(event) => updateStyle("contours", event.target.checked)} /> Curvas</span><span><input type="checkbox" checked={style.transparent} onChange={(event) => updateStyle("transparent", event.target.checked)} /> Transparente</span></label></div></details>;
}

function palette(name, value) {
  const stops = { terrain: [[0, 51, 102, 51], [0.35, 176, 190, 115], [0.7, 185, 148, 91], [1, 245, 245, 240]], viridis: [[0, 68, 1, 84], [0.5, 33, 145, 140], [1, 253, 231, 37]], gist_earth: [[0, 24, 48, 36], [0.5, 125, 112, 74], [1, 239, 238, 177]], gray: [[0, 28, 28, 28], [1, 235, 235, 235]] }[name] || [];
  for (let index = 1; index < stops.length; index += 1) if (value <= stops[index][0]) { const a = stops[index - 1]; const b = stops[index]; const t = (value - a[0]) / (b[0] - a[0]); return [1, 2, 3].map((channel) => Math.round(a[channel] + (b[channel] - a[channel]) * t)); }
  return stops.at(-1).slice(1);
}

function selectedAreaKm2(bounds) {
  if (!bounds) return 100;
  const latitudeSpanKm = Math.abs(bounds.north - bounds.south) * 110.574;
  const longitudeSpanKm = Math.abs(bounds.east - bounds.west) * 111.32 * Math.cos(((bounds.north + bounds.south) / 2) * Math.PI / 180);
  return Math.max(0.1, latitudeSpanKm * longitudeSpanKm);
}

function drawContourLevel(context, values, width, height, level, major, color) {
  const path = new Path2D();
  const interpolate = (x1, y1, value1, x2, y2, value2) => {
    const denominator = value2 - value1;
    const ratio = denominator === 0 ? 0.5 : Math.max(0, Math.min(1, (level - value1) / denominator));
    return [x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio];
  };
  for (let y = 0; y < height - 1; y += 1) for (let x = 0; x < width - 1; x += 1) {
    const topLeft = Number(values[y * width + x]);
    const topRight = Number(values[y * width + x + 1]);
    const bottomRight = Number(values[(y + 1) * width + x + 1]);
    const bottomLeft = Number(values[(y + 1) * width + x]);
    if (![topLeft, topRight, bottomRight, bottomLeft].every(Number.isFinite)) continue;
    const corners = [
      [topLeft < level, interpolate(x, y, topLeft, x + 1, y, topRight)],
      [topRight < level, interpolate(x + 1, y, topRight, x + 1, y + 1, bottomRight)],
      [bottomRight < level, interpolate(x + 1, y + 1, bottomRight, x, y + 1, bottomLeft)],
      [bottomLeft < level, interpolate(x, y + 1, bottomLeft, x, y, topLeft)],
    ];
    const crossings = corners.filter(([inside], index) => inside !== corners[(index + 1) % 4][0]).map(([, point]) => point);
    for (let index = 0; index + 1 < crossings.length; index += 2) {
      path.moveTo(...crossings[index]);
      path.lineTo(...crossings[index + 1]);
    }
  }
  context.strokeStyle = color;
  context.globalAlpha = major ? 0.94 : 0.76;
  context.lineWidth = major ? Math.max(1.1, 1.45 * 0.72) : 0.72;
  context.stroke(path);
}

function renderRaster(values, width, height, style, bounds, route) {
  const renderScale = 2;
  const canvas = document.createElement("canvas"); canvas.width = width * renderScale; canvas.height = height * renderScale; const context = canvas.getContext("2d"); const pixels = context.createImageData(width * renderScale, height * renderScale); let min = Infinity; let max = -Infinity;
  for (const rawValue of values) {
    const value = Number(rawValue);
    if (Number.isFinite(value)) {
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) throw new Error("El DEM no contiene elevaciones validas.");
  const range = max - min || 1;
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) { const index = y * width + x; const value = Number(values[index]); const t = Number.isFinite(value) ? (value - min) / range : 0; let [r, g, b] = style.fill && !style.transparent ? palette(style.palette, t) : (style.background || (style.transparent ? [255, 255, 255] : [24, 34, 30])); const dx = Number(values[index + 1] ?? value) - Number(values[index - 1] ?? value); const dy = Number(values[index + width] ?? value) - Number(values[index - width] ?? value); const shade = style.shade * Math.max(-1, Math.min(1, (dx - dy) / Math.max(1, range * 0.02))) * 80; r = Math.max(0, Math.min(255, r + shade)); g = Math.max(0, Math.min(255, g + shade)); b = Math.max(0, Math.min(255, b + shade)); for (let offsetY = 0; offsetY < renderScale; offsetY += 1) for (let offsetX = 0; offsetX < renderScale; offsetX += 1) { const outputIndex = ((y * renderScale + offsetY) * width * renderScale + x * renderScale + offsetX) * 4; pixels.data[outputIndex] = r; pixels.data[outputIndex + 1] = g; pixels.data[outputIndex + 2] = b; pixels.data[outputIndex + 3] = style.transparent ? Math.round(255 * Math.max(0.15, style.shade || 0.8)) : 255; } }
  context.putImageData(pixels, 0, 0);
  context.save();
  context.scale(renderScale, renderScale);
  if (style.grid) {
    context.save();
    context.strokeStyle = style.gridColor || "#6f7470";
    context.globalAlpha = style.gridOpacity ?? 0.25;
    context.lineWidth = 0.65;
    for (const fraction of [0.2, 0.4, 0.6, 0.8]) {
      const x = Math.round(width * fraction) + 0.5;
      const y = Math.round(height * fraction) + 0.5;
      context.beginPath();
      context.moveTo(x, 0); context.lineTo(x, height);
      context.moveTo(0, y); context.lineTo(width, y);
      context.stroke();
    }
    context.restore();
  }
  if (style.contours) {
    const pixelLimit = Math.max(6, Math.min(28, Math.floor(Math.min(width, height) / 18)));
    const reliefFactor = Math.min(1, Math.max(0.42, range / 900));
    const areaFactor = Math.min(1.5, Math.max(0.35, Math.sqrt(selectedAreaKm2(bounds) / 150)));
    const contourCount = Math.max(4, Math.min(28, pixelLimit, Math.round(style.count * areaFactor * reliefFactor)));
    context.lineJoin = "round";
    context.lineCap = "round";
    for (let index = 1; index <= contourCount; index += 1) {
      const level = min + (range * index) / (contourCount + 1);
      drawContourLevel(context, values, width, height, level, Boolean(style.majorEvery && index % style.majorEvery === 0), style.color);
    }
    context.globalAlpha = 1;
    if (style.labels) {
      context.save();
      context.font = "500 11px 'DM Mono', monospace";
      context.fillStyle = style.color;
      context.globalAlpha = 0.9;
      context.textAlign = "center";
      context.textBaseline = "middle";
      const labelCount = Math.min(6, Math.max(2, Math.floor(contourCount / (style.majorEvery || 5))));
      for (let labelIndex = 1; labelIndex <= labelCount; labelIndex += 1) {
        const x = width * (0.08 + (labelIndex % 3) * 0.36);
        const y = height * (0.16 + Math.floor(labelIndex / 3) * 0.34);
        const elevation = Math.round(min + (range * labelIndex) / (labelCount + 1));
        context.fillText(String(elevation), x, y);
      }
      context.restore();
    }
  }
  if (route?.points?.length > 1) {
    const xScale = width / (bounds.east - bounds.west);
    const yScale = height / (bounds.north - bounds.south);
    context.beginPath();
    route.points.forEach(([latitude, longitude], index) => {
      const x = (longitude - bounds.west) * xScale;
      const y = (bounds.north - latitude) * yScale;
      if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
    });
    context.strokeStyle = style.routeColor || "#ef4444";
    context.lineWidth = Math.max(1, Number(style.routeWidth) || 4);
    context.lineJoin = "round";
    context.lineCap = "round";
    context.globalAlpha = 1;
    context.stroke();
  }
  context.restore();
  return canvas.toDataURL("image/png");
}

createRoot(document.getElementById("root")).render(<App />);
