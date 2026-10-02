"""Procesamiento de DEM y exportacion de mapas topograficos."""

from __future__ import annotations

from pathlib import Path
import logging
from time import perf_counter

import matplotlib
import numpy as np
import rasterio
from matplotlib.colors import LightSource
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.windows import Window, from_bounds

matplotlib.use("Agg")
import matplotlib.pyplot as plt

LOGGER = logging.getLogger("topomapas.render")


def raster_bounds(dataset: rasterio.DatasetReader) -> tuple[float, float, float, float]:
    """Devuelve los limites del raster como (izquierda, abajo, derecha, arriba)."""
    bounds = dataset.bounds
    return bounds.left, bounds.bottom, bounds.right, bounds.top


def selection_window(
    bounds: tuple[float, float, float, float],
    transform: rasterio.Affine,
    width: int,
    height: int,
) -> Window:
    """Convierte una caja dibujada en coordenadas del raster a una ventana valida."""
    x0, y0, x1, y1 = bounds
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    requested = from_bounds(left, bottom, right, top, transform=transform)
    full = Window(0, 0, width, height)
    clipped = requested.intersection(full)
    clipped = clipped.round_offsets().round_lengths()
    if clipped.width < 2 or clipped.height < 2:
        raise ValueError("El area seleccionada es demasiado pequena.")
    return clipped


def _safe_levels(values: np.ndarray, interval: float) -> np.ndarray:
    minimum = float(np.nanmin(values))
    maximum = float(np.nanmax(values))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("El area seleccionada no contiene elevaciones validas.")
    if maximum <= minimum:
        return np.array([minimum, minimum + 1.0])
    start = np.floor(minimum / interval) * interval
    stop = np.ceil(maximum / interval) * interval
    levels = np.arange(start, stop + interval, interval)
    return levels if len(levels) >= 2 else np.array([minimum, maximum])


def _contour_levels(values: np.ndarray, interval: float, count: int | None) -> np.ndarray:
    if count is not None and count > 1:
        minimum = float(np.nanmin(values))
        maximum = float(np.nanmax(values))
        if maximum > minimum:
            return np.linspace(minimum, maximum, min(int(count), 200))
    return _safe_levels(values, interval)


def create_topographic_figure(
    elevation: np.ndarray,
    transform: rasterio.Affine,
    output_path: str | Path,
    title: str = "Mapa topografico",
    contour_interval: float = 20.0,
    clean: bool = False,
    show_contours: bool = True,
    line_width: float = 0.55,
    transparent: bool = False,
    colormap: str = "terrain",
    style: str = "Relieve natural",
    preview: bool = False,
    fill_surface: bool = True,
    contour_color: str | None = None,
    hillshade_opacity: float = 0.24,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    vertical_exaggeration: float = 1.0,
    contour_count: int | None = None,
) -> Path:
    """Genera PNG con hipsometria, sombreado y curvas de nivel."""
    values = np.ma.masked_invalid(np.asarray(elevation, dtype=float))
    started = perf_counter()
    if values.ndim != 2 or values.count() == 0:
        raise ValueError("El DEM no contiene una matriz 2D valida.")
    cell_x = abs(transform.a) or 1.0
    cell_y = abs(transform.e) or 1.0
    left, bottom, right, top = (
        transform.c,
        transform.f + transform.e * elevation.shape[0],
        transform.c + transform.a * elevation.shape[1],
        transform.f,
    )
    left, right = sorted((left, right))
    bottom, top = sorted((bottom, top))
    style_config = {
        "Relieve natural": {"background": None, "line": "#27313a", "shade": True, "surface": True},
        "Curvas oscuras": {"background": "#192318", "line": "#dce7c8", "shade": False, "surface": False},
        "Curvas claras": {"background": "#f1f1ed", "line": "#4b4b48", "shade": True, "surface": False},
        "Contraste negro": {"background": "#171717", "line": "#e8e2d5", "shade": False, "surface": False},
        "Curvas transparentes": {"background": None, "line": "#e8e2d5", "shade": False, "surface": False},
    }.get(style, {"background": None, "line": "#27313a", "shade": True, "surface": True})
    if style == "Curvas transparentes":
        transparent = True
    hillshade = None
    if style_config["shade"] and not transparent:
        fill = values.filled(float(values.mean()))
        hillshade = LightSource(azdeg=azimuth, altdeg=altitude).hillshade(
            fill, vert_exag=vertical_exaggeration, dx=cell_x, dy=cell_y
        )

    figure, axis = plt.subplots(figsize=(8, 6), dpi=120 if preview else 220, constrained_layout=not clean)
    if style_config["background"] is not None:
        figure.patch.set_facecolor(style_config["background"])
        axis.set_facecolor(style_config["background"])
    image = None
    if (style_config["surface"] and fill_surface) and not transparent:
        image = axis.imshow(values, extent=(left, right, bottom, top), origin="upper", cmap=colormap, interpolation="nearest")
    if hillshade is not None:
        axis.imshow(hillshade, extent=(left, right, bottom, top), origin="upper", cmap="gray", alpha=hillshade_opacity, interpolation="nearest")
    if show_contours:
        levels = _contour_levels(values.compressed(), max(float(contour_interval), 0.01), contour_count)
        axis.contour(
            values,
            levels=levels,
            extent=(left, right, bottom, top),
            origin="upper",
            colors=contour_color or style_config["line"],
            linewidths=line_width,
            alpha=0.72,
        )
    axis.set_aspect("equal", adjustable="box")
    if clean:
        axis.set_axis_off()
        figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
    else:
        if image is not None:
            figure.colorbar(image, ax=axis, label="Elevacion")
        axis.set_title(title)
        axis.set_xlabel("Coordenada X")
        axis.set_ylabel("Coordenada Y")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        output,
        dpi=120 if preview else 220,
        bbox_inches="tight",
        pad_inches=0,
        transparent=transparent,
    )
    plt.close(figure)
    LOGGER.info("render_complete path=%s shape=%s contours=%s elapsed_ms=%.1f", output, values.shape, contour_count if show_contours else 0, (perf_counter() - started) * 1000)
    return output


def crop_and_render(
    source_path: str | Path,
    bounds: tuple[float, float, float, float],
    output_path: str | Path,
    contour_interval: float = 20.0,
    clean: bool = False,
    show_contours: bool = True,
    line_width: float = 0.55,
    transparent: bool = False,
    colormap: str = "terrain",
    style: str = "Relieve natural",
    preview: bool = False,
    fill_surface: bool = True,
    contour_color: str | None = None,
    hillshade_opacity: float = 0.24,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    vertical_exaggeration: float = 1.0,
    contour_count: int | None = None,
) -> Path:
    """Recorta un DEM GeoTIFF en la caja indicada y exporta el mapa."""
    started = perf_counter()
    with rasterio.open(source_path) as dataset:
        window = selection_window(bounds, dataset.transform, dataset.width, dataset.height)
        transform = dataset.window_transform(window)
        read_height = min(int(window.height), 900) if preview else int(window.height)
        read_width = min(int(window.width), 900) if preview else int(window.width)
        if preview and (read_height < window.height or read_width < window.width):
            elevation = dataset.read(
                1,
                window=window,
                out_shape=(read_height, read_width),
                resampling=Resampling.bilinear,
                masked=True,
            )
            transform = transform * Affine.scale(window.width / read_width, window.height / read_height)
        else:
            elevation = dataset.read(1, window=window, masked=True)
        title = f"Mapa topografico - {Path(source_path).name}"
    LOGGER.info("raster_read path=%s window=%s shape=%s preview=%s elapsed_ms=%.1f", source_path, window, elevation.shape, preview, (perf_counter() - started) * 1000)
    output = create_topographic_figure(
        elevation,
        transform,
        output_path,
        title=title,
        contour_interval=contour_interval,
        clean=clean,
        show_contours=show_contours,
        line_width=line_width,
        transparent=transparent,
        colormap=colormap,
        style=style,
        preview=preview,
        fill_surface=fill_surface,
        contour_color=contour_color,
        hillshade_opacity=hillshade_opacity,
        azimuth=azimuth,
        altitude=altitude,
        vertical_exaggeration=vertical_exaggeration,
        contour_count=contour_count,
    )
    LOGGER.info("crop_render_complete source=%s output=%s elapsed_ms=%.1f", source_path, output, (perf_counter() - started) * 1000)
    return output