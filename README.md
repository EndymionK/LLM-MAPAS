# TopoMapas

## Version web Node.js / Vercel

La version desplegable en Vercel esta compuesta por un frontend React/Vite y una funcion serverless Node.js:

 `src/main.jsx`: mapa Leaflet, seleccion de areas, presets, preview raster y exportacion PNG.
- `src/styles.css`: interfaz responsive del mapa y panel de previsualizacion.
- `api/dem.js`: proxy serverless hacia OpenTopography; la API key nunca llega al navegador.
- `vercel.json`: configura el build estatico y la funcion `/api/dem`.
5. El panel de preview se reorganizo alrededor de presets visuales, con auto-preview, cantidad de curvas, exageracion del relieve y descarga PNG.
### Migracion paso a paso

1. La interfaz Tkinter se sustituyo por una aplicacion web React/Vite, porque Tkinter no puede ejecutarse en Vercel.
La imagen se configura mediante presets visuales. `Atlas nocturno` es el principal y el predeterminado: usa fondo carbon, curvas marfil finas con curvas maestras mas gruesas, reticula cartografica tenue y etiquetas de elevacion discretas. Tambien estan disponibles `Relieve natural`, `Lectura altimetrica`, `Curvas de campo`, `Contraste editorial` y `Lineas blancas`. La cantidad de lineas y la exageracion del relieve son controles generales independientes del preset; los colores, fondo, paleta, sombreado y grosor pertenecen exclusivamente a cada preset. Tambien se puede subir una ruta `GPX` o `KML`: la app calcula un margen automatico, selecciona esa extension, dibuja la ruta en el mapa y la superpone en la imagen. El color y grosor de la ruta se ajustan en `Opciones de imagen`. La preview admite rueda para zoom y arrastre izquierdo para desplazarse.
2. `TkinterMapView` se sustituyo por Leaflet con teselas de OpenStreetMap, centrado inicialmente en Colombia.
3. `requests` y `dem_api.py` se sustituyeron por `fetch` en `api/dem.js`, con validacion de limites, limite de superficie y API key en servidor.
4. Rasterio/Matplotlib se sustituyeron por `geotiff.js` y Canvas en el navegador para leer el GeoTIFF y dibujar hipsometria, sombreado y curvas.
5. El panel de preview se reorganizo en presets y modo avanzado, con auto-preview, detalle de curvas, perfiles locales y descarga PNG.

### Desarrollo y despliegue

```powershell
npm install
npm run dev
npm run build
```

En Vercel, importa este repositorio, usa `npm run build` como Build Command y `dist` como Output Directory. Configura la variable de entorno `OPEN_TOPOGRAPHY_API_KEY` en Project Settings para los entornos Development, Preview y Production. No subas `.opentopography_key` ni expongas la clave en el frontend.

La funcion acepta solo selecciones validas dentro de Colombia y de hasta 16 grados cuadrados para mantener controlados el tiempo de respuesta y los limites del proveedor. Vercel no conserva cache local entre invocaciones; el cache persistente SQLite de la antigua app de escritorio no forma parte del despliegue web.

Aplicacion de escritorio a pantalla completa con mapa mundial dinamico. Las teselas se cargan bajo demanda al acercar, alejar o desplazar el mapa. El espacio se divide en 2/3 para el mapa y 1/3 para una preview topografica que se actualiza despues de cada seleccion. Permite dibujar un rectangulo y descargar automaticamente su topografia, sin cargar un DEM manualmente.

- hipsometria con paleta `terrain`;
- sombreado de relieve calculado con `matplotlib.colors.LightSource`;
- curvas de nivel con estilos configurables;
- exportacion limpia, sin ejes ni leyendas.

## Instalacion y uso

En PowerShell, desde esta carpeta:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Para ejecutar la suite normal y los benchmarks de rendimiento:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -m performance --durations=0 -q
```

Los eventos de aplicación, tiempos de descarga/lectura/render, tamaño de raster y errores con traceback se guardan en `logs/topomapas.log`. El archivo usa rotación de 2 MB y no registra la API key. Si la interfaz se bloquea, anota la hora y revisa las líneas cercanas a `preview_queued`, `raster_read`, `render_complete` o `preview_error`.

Los benchmarks comprueban tiempo de preview, memoria pico, resolucion maxima y el coste relativo de curvas de nivel. En esta maquina de referencia la suite completa tarda aproximadamente 3 segundos y la preview grande menos de 2 segundos; los umbrales son deliberadamente amplios para no depender de una CPU especifica.

El mapa funciona como un mapa web: el **boton izquierdo** desplaza, la **rueda** acerca o aleja y el **boton derecho** dibuja el rectangulo de seleccion. Las teselas de OpenStreetMap se descargan dinamicamente en cada nivel de zoom. La preview se genera en segundo plano al terminar el rectangulo. Pulsa **Guardar imagen** para exportar exactamente la imagen topografica, sin leyenda, ejes ni textos.

 La imagen se configura mediante presets visuales: `Relieve natural`, `Lectura altimetrica`, `Curvas de campo`, `Contraste editorial` y `Lineas blancas`. La cantidad de lineas y la exageracion del relieve son controles generales independientes del preset; los colores, fondo, paleta, sombreado y grosor pertenecen exclusivamente a cada preset. El preset `Lineas blancas` usa fondo negro y curvas blancas. La preview admite rueda para zoom y arrastre izquierdo para desplazarse.

## API gratuita

La aplicacion usa la API global de [OpenTopography](https://portal.opentopography.org/apidocs/#/Public/getGlobalDem), que requiere una clave gratuita.

1. Crea una cuenta en [OpenTopography](https://portal.opentopography.org/newUser).
2. Entra en [MyOpenTopo](https://portal.opentopography.org/myopentopo) y solicita una API key desde la seccion de servicios/API.
3. Pegala en el dialogo de la aplicacion cuando la solicite. No se guarda en archivos.

Tambien puedes definirla antes de iniciar:

```powershell
$env:OPEN_TOPOGRAPHY_API_KEY = "TU_CLAVE"
.\.venv\Scripts\python.exe app.py
```

La API tiene limites diarios y de superficie. Para un primer intento selecciona un area pequena, por ejemplo una region de 0.1 a 1 grado por lado. El mapa base usa teselas publicas de [OpenTopoMap](https://opentopomap.org/about).

## Datos de elevacion recomendados

- [USGS 3DEP](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services): fuente gratuita y sin restricciones de uso para Estados Unidos; ofrece productos de 1 m, 10 m y 30 m donde existe cobertura.
- [USGS National Map Downloader](https://apps.nationalmap.gov/downloader/): permite buscar y descargar datos por extension o poligono.
- [OpenTopography Developers](https://opentopography.org/developers): APIs y catalogo global, incluyendo SRTM, ALOS y USGS 3DEP. La API requiere clave y tiene limites de uso; nunca la incrustes en una aplicacion distribuida.
- [NASA SRTMGL1](https://www.earthdata.nasa.gov/data/catalog/lpcloud-srtmgl1-003): alternativa global de aproximadamente 30 m. Comprueba cobertura, vacios y metadatos del producto.

## Criterios tecnicos usados

Rasterio lee el raster como un array NumPy y conserva el `transform` para convertir la seleccion a una ventana georreferenciada. El recorte se limita al tamaño del dataset para evitar lecturas fuera de rango. Matplotlib dibuja el raster, el relieve y curvas con niveles explicitos, siguiendo su documentacion oficial.

Para analisis de pendientes o distancias, reproyecta primero a un CRS local en metros. No uses grados de latitud/longitud para calcular el sombreado como si fueran metros. Revisa siempre unidades verticales, datum, resolucion, `nodata` y si el producto es DEM de terreno o DSM con edificios/vegetacion.

## Referencias consultadas

- [Rasterio: plotting](https://rasterio.readthedocs.io/en/latest/topics/plotting.html)
- [Rasterio: masking by shapefile](https://rasterio.readthedocs.io/en/latest/topics/masking-by-shapefile.html)
- [Rasterio: reprojection](https://rasterio.readthedocs.io/en/latest/topics/reproject.html)
- [Rasterio: mask API](https://rasterio.readthedocs.io/en/latest/api/rasterio.mask.html)
- [Matplotlib: contourf](https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.contourf.html)
- [Matplotlib: LightSource](https://matplotlib.org/stable/api/_as_gen/matplotlib.colors.LightSource.html)