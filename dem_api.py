"""Cliente minimo para el API global de OpenTopography."""

from pathlib import Path
import logging
import os
from time import perf_counter

import requests

API_URL = "https://portal.opentopography.org/API/globaldem"
LOGGER = logging.getLogger("topomapas.network")


def build_dem_params(bounds: tuple[float, float, float, float], api_key: str) -> dict[str, str | float]:
    """Construye los parametros WGS84 exigidos por OpenTopography."""
    west, south, east, north = bounds
    if not api_key.strip():
        raise ValueError("Introduce una clave de OpenTopography.")
    if west >= east or south >= north:
        raise ValueError("El area debe tener un orden oeste-este y sur-norte valido.")
    return {"demtype": "SRTMGL1", "south": south, "north": north, "west": west, "east": east, "outputFormat": "GTiff", "API_Key": api_key.strip()}


def download_dem(bounds: tuple[float, float, float, float], api_key: str, destination: str | Path) -> Path:
    """Descarga el DEM SRTM global de aproximadamente 30 m para una caja."""
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    response = requests.get(API_URL, params=build_dem_params(bounds, api_key), timeout=(10, 180), stream=True)
    response.raise_for_status()
    chunks = response.iter_content(chunk_size=1024 * 1024)
    first_chunk = next(chunks, b"")
    if not first_chunk.startswith((b"II", b"MM")):
        raise ValueError("OpenTopography no devolvio un GeoTIFF. Revisa la clave y el area seleccionada.")
    total = 0
    temporary = output.with_suffix(output.suffix + ".part")
    with temporary.open("wb") as file:
        file.write(first_chunk)
        total += len(first_chunk)
        for chunk in chunks:
            if chunk:
                total += len(chunk)
                if total > 500 * 1024 * 1024:
                    raise ValueError("La descarga supera el limite de 500 MB.")
                file.write(chunk)
    os.replace(temporary, output)
    LOGGER.info("dem_download_complete bounds=%s bytes=%d elapsed_ms=%.1f", bounds, total, (perf_counter() - started) * 1000)
    return output