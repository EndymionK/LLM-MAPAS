"""Mapa dinamico con preview topografica automatica."""

from __future__ import annotations

import os
import shutil
import tempfile
import tkinter as tk
import logging
import time
import json
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk
from pyproj import Geod
from tkintermapview import TkinterMapView

from dem_api import download_dem
from topografia import crop_and_render

GEOD = Geod(ellps="WGS84")


def configure_logging() -> logging.Logger:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger("topomapas")
    if not logger.handlers:
        handler = RotatingFileHandler(log_dir / "topomapas.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = configure_logging()

MAP_TILE_CACHE_DIR = Path("cache") / "tiles"
MAP_TILE_CACHE_DB = MAP_TILE_CACHE_DIR / "map_tiles.db"
COLOMBIA_CENTER = (4.5709, -74.2973)
COLOMBIA_BOUNDS = (-79.1, -4.3, -66.8, 13.6)
COLOMBIA_WARMUP_ZOOMS = (4, 5, 6)
STYLE_PRESETS: dict[str, tuple[bool, bool, bool, float, float, str, float, float, float, float, int]] = {
    "Relieve natural": (True, True, True, 0.55, 20.0, "#27313a", 0.24, 315.0, 45.0, 1.0, 30),
    "Curvas oscuras": (True, False, True, 0.55, 20.0, "#dce7c8", 0.0, 315.0, 45.0, 1.0, 30),
    "Curvas claras": (True, False, True, 0.55, 20.0, "#4b4b48", 0.24, 315.0, 45.0, 1.0, 30),
    "Contraste negro": (True, False, True, 0.75, 20.0, "#e8e2d5", 0.0, 315.0, 45.0, 1.0, 30),
    "Curvas transparentes": (True, False, True, 0.55, 20.0, "#e8e2d5", 0.0, 315.0, 45.0, 1.0, 30),
}


def _tile_cache_limits_by_zoom(zoom: int) -> tuple[int, int]:
    """Devuelve radio maximo y cuantos niveles por delante precargar segun zoom."""
    if zoom <= 3:
        return 4, 2
    if zoom <= 6:
        return 3, 1
    return 2, 1


class FastMapView(TkinterMapView):
    """Reduce la precarga agresiva de teselas durante movimientos rapidos."""

    def pre_cache(self) -> None:
        last_position = None
        radius = 1
        while self.running:
            if last_position != self.pre_cache_position:
                last_position = self.pre_cache_position
                radius = 1
            if last_position is None:
                time.sleep(0.1)
                continue
            zoom = round(self.zoom)
            max_radius, zoom_ahead = _tile_cache_limits_by_zoom(zoom)
            if radius <= max_radius:
                center_x, center_y = last_position
                for x in range(center_x - radius, center_x + radius + 1):
                    for y in (center_y - radius, center_y + radius):
                        if f"{zoom}{x}{y}" not in self.tile_image_cache:
                            self.request_image(zoom, x, y)
                for y in range(center_y - radius + 1, center_y + radius):
                    for x in (center_x - radius, center_x + radius):
                        if f"{zoom}{x}{y}" not in self.tile_image_cache:
                            self.request_image(zoom, x, y)
                # Precarga teselas hijas para que el siguiente zoom aparezca mas rapido.
                if zoom_ahead > 0:
                    max_zoom = int(getattr(self, "max_zoom", 19))
                    detail_zoom = min(zoom + 1, max_zoom)
                    if detail_zoom > zoom:
                        detail_x_start = (center_x - radius) * 2
                        detail_x_end = (center_x + radius) * 2 + 1
                        detail_y_start = (center_y - radius) * 2
                        detail_y_end = (center_y + radius) * 2 + 1
                        for x in range(detail_x_start, detail_x_end + 1):
                            for y in (detail_y_start, detail_y_end):
                                if f"{detail_zoom}{x}{y}" not in self.tile_image_cache:
                                    self.request_image(detail_zoom, x, y)
                        for y in range(detail_y_start + 1, detail_y_end):
                            for x in (detail_x_start, detail_x_end):
                                if f"{detail_zoom}{x}{y}" not in self.tile_image_cache:
                                    self.request_image(detail_zoom, x, y)
                radius += 1
            else:
                time.sleep(0.1)


class TopographyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TopoMapas | mapa mundial dinamico")
        self.root.state("zoomed")
        self.root.minsize(1000, 650)
        self.selection: tuple[float, float, float, float] | None = None
        self.selection_polygon = None
        self.selection_start: tuple[float, float] | None = None
        self.preview_path: Path | None = None
        self.preview_dem_path: Path | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.api_key: str | None = os.getenv("OPEN_TOPOGRAPHY_API_KEY")
        local_key = Path(".opentopography_key")
        if not self.api_key and local_key.exists():
            self.api_key = local_key.read_text(encoding="utf-8").strip() or None
        self.render_token = 0
        self.render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="topomapas-render")
        self.render_future = None
        self.refresh_after_id = None
        self.preview_cache: OrderedDict[tuple, Path] = OrderedDict()
        self.show_contours = tk.BooleanVar(value=True)
        self.line_width = tk.DoubleVar(value=0.55)
        self.transparent = tk.BooleanVar(value=False)
        self.colormap = tk.StringVar(value="terrain")
        self.visual_style = tk.StringVar(value="Relieve natural")
        self.contour_interval = tk.DoubleVar(value=20.0)
        self.contour_count = tk.IntVar(value=30)
        self.fill_surface = tk.BooleanVar(value=True)
        self.contour_color = tk.StringVar(value="#27313a")
        self.hillshade_opacity = tk.DoubleVar(value=0.24)
        self.azimuth = tk.DoubleVar(value=315.0)
        self.altitude = tk.DoubleVar(value=45.0)
        self.vertical_exaggeration = tk.DoubleVar(value=1.0)
        self.auto_refresh = tk.BooleanVar(value=True)
        self.control_mode = tk.StringVar(value="Guiado")
        self.quick_profile = tk.StringVar(value="Relieve natural")
        self.detail_level = tk.IntVar(value=55)
        self.applying_style = False
        self.profile_path = Path("config") / "custom_preview_profile.json"
        self.custom_controls: list[tk.Widget] = []
        LOGGER.info("app_init pid=%s", os.getpid())
        self._build_ui()
        self._build_map()

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Header.TFrame", background="#17212b")
        style.configure("Title.TLabel", background="#17212b", foreground="#f2f6f8", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", background="#17212b", foreground="#a8bbc8")
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7))
        style.configure("Primary.TButton", background="#2dd4bf", foreground="#102026", font=("Segoe UI", 10, "bold"), padding=(14, 7))
        style.map("Primary.TButton", background=[("active", "#5eead4"), ("disabled", "#46605f")])
        self.root.configure(bg="#17212b")
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(16, 12, 16, 10))
        header.pack(fill=tk.X)
        top = ttk.Frame(header, style="Header.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="TOPO MAPAS", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(top, text="Explora y genera relieve", style="Muted.TLabel").pack(side=tk.LEFT, padx=16)
        bottom = ttk.Frame(header, style="Header.TFrame")
        bottom.pack(fill=tk.X, pady=(9, 0))
        ttk.Label(bottom, text="Mapa: izquierdo desplaza, rueda hace zoom, derecho selecciona", style="Muted.TLabel").pack(side=tk.LEFT)
        self.status = ttk.Label(header, text="Cargando mapa mundial...", style="Muted.TLabel", anchor=tk.W)
        self.status.pack(fill=tk.X, pady=(8, 0))

    def _build_map(self) -> None:
        content = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashwidth=5, bg="#17212b", bd=0)
        self.content = content
        content.pack(fill=tk.BOTH, expand=True)
        map_frame = tk.Frame(content, bg="#dbe5e8")
        preview_frame = tk.Frame(content, bg="#101820")
        content.add(map_frame, stretch="always", minsize=500)
        content.add(preview_frame, stretch="always", minsize=300)
        MAP_TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.map_widget = FastMapView(map_frame, corner_radius=0, bg_color="#dbe5e8", max_zoom=19, database_path=str(MAP_TILE_CACHE_DB))
        self.map_widget.pack(fill=tk.BOTH, expand=True)
        self.map_widget.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", max_zoom=19)
        self.map_widget.set_position(*COLOMBIA_CENTER)
        self.map_widget.set_zoom(2)
        LOGGER.info("tile_cache_enabled db=%s", MAP_TILE_CACHE_DB)
        self.root.after(1200, self._warmup_colombia_tiles)
        self.map_widget.fading_possible = False
        self.map_widget.canvas.unbind("<Button-3>")
        self.map_widget.canvas.bind("<ButtonPress-3>", self._right_press)
        self.map_widget.canvas.bind("<B3-Motion>", self._right_drag)
        self.map_widget.canvas.bind("<ButtonRelease-3>", self._right_release)
        ttk.Label(preview_frame, text="PREVISUALIZACION", foreground="#f2f6f8", background="#101820", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        self.preview_status = ttk.Label(preview_frame, text="Dibuja un area para generar la imagen", foreground="#a8bbc8", background="#101820", wraplength=320)
        self.preview_status.pack(anchor="w", padx=18, pady=(0, 12))
        self.preview_canvas = tk.Canvas(preview_frame, bg="#101820", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        self.preview_canvas.bind("<MouseWheel>", self._preview_zoom)
        self.preview_canvas.bind("<ButtonPress-1>", self._preview_pan_start)
        self.preview_canvas.bind("<B1-Motion>", self._preview_pan)
        self.preview_canvas.create_text(220, 220, text="La topografia aparecera aqui", fill="#a8bbc8", tags="placeholder")
        self.preview_scale = 1.0
        self.preview_origin = (0, 0)
        self.preview_drag_start = None
        footer = tk.Frame(preview_frame, bg="#101820")
        footer.pack(fill=tk.X, padx=18, pady=(0, 18))
        quick = ttk.LabelFrame(footer, text="Flujo rapido", padding=(10, 8))
        quick.pack(fill=tk.X, pady=(0, 8))
        row_top = tk.Frame(quick, bg="#101820")
        row_top.pack(fill=tk.X)
        ttk.Label(row_top, text="Preset", style="Muted.TLabel").pack(side=tk.LEFT)
        quick_style_box = ttk.Combobox(row_top, textvariable=self.quick_profile, values=tuple(STYLE_PRESETS.keys()), width=18, state="readonly")
        quick_style_box.pack(side=tk.LEFT, padx=(8, 8))
        quick_style_box.bind("<<ComboboxSelected>>", self._apply_quick_profile)
        ttk.Radiobutton(row_top, text="Guiado", value="Guiado", variable=self.control_mode, command=self._set_custom_controls_state).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Radiobutton(row_top, text="Avanzado", value="Avanzado", variable=self.control_mode, command=self._set_custom_controls_state).pack(side=tk.LEFT)
        ttk.Checkbutton(row_top, text="Auto preview", variable=self.auto_refresh).pack(side=tk.RIGHT)

        row_detail = tk.Frame(quick, bg="#101820")
        row_detail.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(row_detail, text="Detalle", style="Muted.TLabel").pack(side=tk.LEFT)
        detail_scale = ttk.Scale(row_detail, from_=0, to=100, variable=self.detail_level, command=self._detail_level_changed)
        detail_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        ttk.Label(row_detail, textvariable=self.detail_level, style="Muted.TLabel", width=4, anchor=tk.E).pack(side=tk.LEFT)
        ttk.Button(row_detail, text="Guardar perfil", style="Action.TButton", command=self._save_custom_profile).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(row_detail, text="Cargar perfil", style="Action.TButton", command=self._load_custom_profile).pack(side=tk.RIGHT)

        tabs = ttk.Notebook(footer)
        tabs.pack(fill=tk.X, pady=(0, 10))
        basic_tab = tk.Frame(tabs, bg="#101820")
        advanced_tab = tk.Frame(tabs, bg="#101820")
        tabs.add(basic_tab, text="Basico")
        tabs.add(advanced_tab, text="Avanzado")

        basic_row1 = tk.Frame(basic_tab, bg="#101820")
        basic_row1.pack(fill=tk.X, pady=(6, 4), padx=6)
        ttk.Checkbutton(basic_row1, text="Curvas", variable=self.show_contours, command=self._on_option_changed).pack(side=tk.LEFT)
        ttk.Checkbutton(basic_row1, text="Relleno", variable=self.fill_surface, command=self._on_option_changed).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(basic_row1, text="Transparente", variable=self.transparent, command=self._on_option_changed).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(basic_row1, text="Paleta", style="Muted.TLabel").pack(side=tk.LEFT, padx=(20, 6))
        palette_box = ttk.Combobox(basic_row1, textvariable=self.colormap, values=("terrain", "viridis", "gist_earth", "gray"), width=10, state="readonly")
        palette_box.pack(side=tk.LEFT)
        palette_box.bind("<<ComboboxSelected>>", self._on_option_changed)

        basic_row2 = tk.Frame(basic_tab, bg="#101820")
        basic_row2.pack(fill=tk.X, pady=(2, 6), padx=6)
        ttk.Label(basic_row2, text="Cantidad de lineas", style="Muted.TLabel").pack(side=tk.LEFT)
        contour_count_control = ttk.Spinbox(basic_row2, from_=2, to=200, increment=1, width=6, textvariable=self.contour_count, command=self._on_option_changed)
        contour_count_control.pack(side=tk.LEFT, padx=(8, 16))
        contour_count_control.bind("<Return>", lambda _event: self._on_option_changed())
        ttk.Label(basic_row2, text="Grosor", style="Muted.TLabel").pack(side=tk.LEFT)
        line_width_spin = ttk.Spinbox(basic_row2, from_=0.1, to=5, increment=0.1, width=6, textvariable=self.line_width, command=self._on_option_changed)
        line_width_spin.pack(side=tk.LEFT, padx=(8, 16))
        line_width_spin.bind("<Return>", lambda _event: self._on_option_changed())
        ttk.Label(basic_row2, text="Intervalo", style="Muted.TLabel").pack(side=tk.LEFT)
        contour_interval_spin = ttk.Spinbox(basic_row2, from_=1, to=1000, increment=1, width=7, textvariable=self.contour_interval, command=self._on_option_changed)
        contour_interval_spin.pack(side=tk.LEFT, padx=(8, 0))
        contour_interval_spin.bind("<Return>", lambda _event: self._on_option_changed())

        advanced_row1 = tk.Frame(advanced_tab, bg="#101820")
        advanced_row1.pack(fill=tk.X, pady=(6, 4), padx=6)
        ttk.Label(advanced_row1, text="Color linea", style="Muted.TLabel").pack(side=tk.LEFT)
        contour_color_box = ttk.Combobox(advanced_row1, textvariable=self.contour_color, values=("#27313a", "#ffffff", "#000000", "#dce7c8", "#e8e2d5"), width=10, state="readonly")
        contour_color_box.pack(side=tk.LEFT, padx=(8, 16))
        contour_color_box.bind("<<ComboboxSelected>>", self._on_option_changed)
        ttk.Label(advanced_row1, text="Sombra", style="Muted.TLabel").pack(side=tk.LEFT)
        shade_spin = ttk.Spinbox(advanced_row1, from_=0, to=1, increment=0.05, width=6, textvariable=self.hillshade_opacity, command=self._on_option_changed)
        shade_spin.pack(side=tk.LEFT, padx=(8, 16))
        shade_spin.bind("<Return>", lambda _event: self._on_option_changed())
        ttk.Label(advanced_row1, text="Luz", style="Muted.TLabel").pack(side=tk.LEFT)
        azimuth_spin = ttk.Spinbox(advanced_row1, from_=0, to=360, increment=15, width=6, textvariable=self.azimuth, command=self._on_option_changed)
        azimuth_spin.pack(side=tk.LEFT, padx=(8, 16))
        azimuth_spin.bind("<Return>", lambda _event: self._on_option_changed())
        ttk.Label(advanced_row1, text="Altura", style="Muted.TLabel").pack(side=tk.LEFT)
        altitude_spin = ttk.Spinbox(advanced_row1, from_=0, to=90, increment=5, width=6, textvariable=self.altitude, command=self._on_option_changed)
        altitude_spin.pack(side=tk.LEFT, padx=(8, 0))
        altitude_spin.bind("<Return>", lambda _event: self._on_option_changed())

        advanced_row2 = tk.Frame(advanced_tab, bg="#101820")
        advanced_row2.pack(fill=tk.X, pady=(2, 6), padx=6)
        ttk.Label(advanced_row2, text="Exageracion vertical", style="Muted.TLabel").pack(side=tk.LEFT)
        exaggeration_spin = ttk.Spinbox(advanced_row2, from_=0.1, to=10, increment=0.1, width=6, textvariable=self.vertical_exaggeration, command=self._on_option_changed)
        exaggeration_spin.pack(side=tk.LEFT, padx=(8, 12))
        exaggeration_spin.bind("<Return>", lambda _event: self._on_option_changed())
        ttk.Label(advanced_row2, text="Estilo activo", style="Muted.TLabel").pack(side=tk.LEFT, padx=(12, 6))
        style_box = ttk.Combobox(advanced_row2, textvariable=self.visual_style, values=tuple(STYLE_PRESETS.keys()) + ("Personalizado",), width=16, state="readonly")
        style_box.pack(side=tk.LEFT)
        style_box.bind("<<ComboboxSelected>>", self._style_changed)

        self.custom_controls.extend([contour_color_box, shade_spin, azimuth_spin, altitude_spin, exaggeration_spin])
        actions = tk.Frame(footer, bg="#101820")
        actions.pack(fill=tk.X)
        self.clear_button = ttk.Button(actions, text="Limpiar", style="Action.TButton", command=self.clear_selection)
        self.clear_button.pack(side=tk.RIGHT)
        self.save_button = ttk.Button(actions, text="Guardar imagen", style="Primary.TButton", command=self.export_map, state=tk.DISABLED)
        self.save_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.refresh_button = ttk.Button(actions, text="Regenerar preview", style="Action.TButton", command=self._refresh_preview, state=tk.DISABLED)
        self.refresh_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.root.after(100, self._set_panel_ratio)
        self._apply_quick_profile()
        self._set_custom_controls_state()
        self.root.after(500, lambda: self.status.configure(text="Mapa de Colombia priorizado: navega con izquierdo y selecciona con derecho"))

    def _warmup_colombia_tiles(self) -> None:
        if not hasattr(self, "map_widget"):
            return
        total_requested = 0
        west, south, east, north = COLOMBIA_BOUNDS
        for zoom in COLOMBIA_WARMUP_ZOOMS:
            top_left = self.map_widget.decimal_to_osm(west, north, zoom)
            bottom_right = self.map_widget.decimal_to_osm(east, south, zoom)
            x_start, x_end = sorted((int(top_left[0]), int(bottom_right[0])))
            y_start, y_end = sorted((int(top_left[1]), int(bottom_right[1])))
            x_start = max(0, x_start)
            y_start = max(0, y_start)
            max_tile = (2**zoom) - 1
            x_end = min(max_tile, x_end)
            y_end = min(max_tile, y_end)
            requested_zoom = 0
            for x in range(x_start, x_end + 1):
                for y in range(y_start, y_end + 1):
                    key = f"{zoom}{x}{y}"
                    if key in self.map_widget.tile_image_cache:
                        continue
                    self.map_widget.request_image(zoom, x, y)
                    requested_zoom += 1
            total_requested += requested_zoom
            LOGGER.info("tile_warmup_colombia zoom=%d requested=%d x_range=%d-%d y_range=%d-%d", zoom, requested_zoom, x_start, x_end, y_start, y_end)
        LOGGER.info("tile_warmup_colombia_done total_requested=%d", total_requested)

    def _set_panel_ratio(self) -> None:
        width = self.content.winfo_width()
        if width > 0:
            self.content.sash_place(0, int(width * 2 / 3), 0)

    def _add_option_control(self, control: tk.Widget, parent: tk.Widget) -> None:
        control.pack(side=tk.RIGHT)
        self.custom_controls.append(control)

    def _apply_quick_profile(self, _event=None) -> None:
        profile = self.quick_profile.get()
        values = STYLE_PRESETS.get(profile)
        if values is None:
            return
        self.applying_style = True
        try:
            transparent, fill_surface, show_contours, line_width, interval, contour_color, shade_opacity, azimuth, altitude, exaggeration, contour_count = values
            self.transparent.set(transparent)
            self.fill_surface.set(fill_surface)
            self.show_contours.set(show_contours)
            self.line_width.set(line_width)
            self.contour_interval.set(interval)
            self.contour_color.set(contour_color)
            self.hillshade_opacity.set(shade_opacity)
            self.azimuth.set(azimuth)
            self.altitude.set(altitude)
            self.vertical_exaggeration.set(exaggeration)
            self.contour_count.set(contour_count)
            self.visual_style.set(profile)
        finally:
            self.applying_style = False
        LOGGER.info("style_profile_applied profile=%s", profile)
        self._on_option_changed()

    def _detail_level_changed(self, _value=None) -> None:
        level = int(self.detail_level.get())
        self.applying_style = True
        try:
            self.contour_count.set(max(2, min(200, int(8 + level * 1.6))))
            self.contour_interval.set(max(5.0, min(180.0, 80.0 - (level * 0.7))))
            self.line_width.set(round(max(0.1, min(5.0, 0.3 + (level / 100.0) * 1.2)), 2))
            self.hillshade_opacity.set(round(max(0.0, min(1.0, 0.1 + (level / 100.0) * 0.45)), 2))
        finally:
            self.applying_style = False
        if self.control_mode.get() == "Avanzado":
            self.visual_style.set("Personalizado")
        LOGGER.info("detail_level_changed level=%d", level)
        self._on_option_changed()

    def _on_option_changed(self, _event=None) -> None:
        if not self.applying_style and self.control_mode.get() == "Avanzado":
            self.visual_style.set("Personalizado")
        if self.auto_refresh.get():
            self._refresh_preview()
        elif self.selection is not None:
            self.preview_status.configure(text="Cambios listos. Pulsa 'Regenerar preview' para aplicar.")

    def _save_custom_profile(self) -> None:
        data = {
            "transparent": self.transparent.get(),
            "fill_surface": self.fill_surface.get(),
            "show_contours": self.show_contours.get(),
            "line_width": self.line_width.get(),
            "contour_interval": self.contour_interval.get(),
            "contour_count": self.contour_count.get(),
            "contour_color": self.contour_color.get(),
            "hillshade_opacity": self.hillshade_opacity.get(),
            "azimuth": self.azimuth.get(),
            "altitude": self.altitude.get(),
            "vertical_exaggeration": self.vertical_exaggeration.get(),
            "colormap": self.colormap.get(),
            "detail_level": self.detail_level.get(),
        }
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.visual_style.set("Personalizado")
        self.control_mode.set("Avanzado")
        self._set_custom_controls_state()
        self.preview_status.configure(text=f"Perfil personalizado guardado en {self.profile_path}")
        LOGGER.info("custom_profile_saved path=%s", self.profile_path)

    def _load_custom_profile(self) -> None:
        if not self.profile_path.exists():
            self.preview_status.configure(text="No hay perfil guardado aun. Ajusta parametros y usa 'Guardar perfil'.")
            return
        data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        self.applying_style = True
        try:
            self.transparent.set(bool(data.get("transparent", self.transparent.get())))
            self.fill_surface.set(bool(data.get("fill_surface", self.fill_surface.get())))
            self.show_contours.set(bool(data.get("show_contours", self.show_contours.get())))
            self.line_width.set(float(data.get("line_width", self.line_width.get())))
            self.contour_interval.set(float(data.get("contour_interval", self.contour_interval.get())))
            self.contour_count.set(int(data.get("contour_count", self.contour_count.get())))
            self.contour_color.set(str(data.get("contour_color", self.contour_color.get())))
            self.hillshade_opacity.set(float(data.get("hillshade_opacity", self.hillshade_opacity.get())))
            self.azimuth.set(float(data.get("azimuth", self.azimuth.get())))
            self.altitude.set(float(data.get("altitude", self.altitude.get())))
            self.vertical_exaggeration.set(float(data.get("vertical_exaggeration", self.vertical_exaggeration.get())))
            self.colormap.set(str(data.get("colormap", self.colormap.get())))
            self.detail_level.set(int(data.get("detail_level", self.detail_level.get())))
            self.visual_style.set("Personalizado")
            self.control_mode.set("Avanzado")
        finally:
            self.applying_style = False
        self._set_custom_controls_state()
        self._on_option_changed()
        LOGGER.info("custom_profile_loaded path=%s", self.profile_path)

    def _style_changed(self, _event=None) -> None:
        if self.visual_style.get() in STYLE_PRESETS:
            self.quick_profile.set(self.visual_style.get())
            self._apply_quick_profile()
            return
        LOGGER.info("style_change style=%s", self.visual_style.get())
        if self.visual_style.get() == "Personalizado":
            self.control_mode.set("Avanzado")
        self._set_custom_controls_state()
        self._on_option_changed()

    def _set_custom_controls_state(self) -> None:
        state = tk.NORMAL if self.control_mode.get() == "Avanzado" else tk.DISABLED
        for control in self.custom_controls:
            try:
                control.configure(state=state)
            except tk.TclError:
                pass

    def _map_coords(self, event) -> tuple[float, float]:
        return self.map_widget.convert_canvas_coords_to_decimal_coords(event.x, event.y)

    def _right_press(self, event) -> str:
        self.selection_start = self._map_coords(event)
        self._delete_polygon()
        self.preview_path = None
        self.preview_dem_path = None
        self.save_button.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.preview_status.configure(text="Suelta para generar la nueva preview")
        return "break"

    def _right_drag(self, event) -> str:
        if self.selection_start is not None:
            start_lat, start_lon = self.selection_start
            end_lat, end_lon = self._map_coords(event)
            self._draw_polygon(start_lat, start_lon, end_lat, end_lon)
        return "break"

    def _right_release(self, event) -> str:
        if self.selection_start is not None:
            start_lat, start_lon = self.selection_start
            end_lat, end_lon = self._map_coords(event)
            self._draw_polygon(start_lat, start_lon, end_lat, end_lon)
            self.selection_start = None
            area_m2, _ = GEOD.polygon_area_perimeter(
                [self.selection[0], self.selection[2], self.selection[2], self.selection[0]],
                [self.selection[1], self.selection[1], self.selection[3], self.selection[3]],
            )
            LOGGER.info("selection_complete bounds=%s area_km2=%.2f", self.selection, abs(area_m2) / 1_000_000)
            self._start_preview()
        return "break"

    def _draw_polygon(self, start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> None:
        west, east = sorted((start_lon, end_lon))
        south, north = sorted((start_lat, end_lat))
        if self.selection_polygon is not None:
            self.selection_polygon.delete()
        self.selection_polygon = self.map_widget.set_polygon(
            [(north, west), (north, east), (south, east), (south, west)],
            fill_color="#2dd4bf", outline_color="#ffffff", border_width=3, name="area seleccionada"
        )
        self.selection = (west, south, east, north)
        area_m2, _ = GEOD.polygon_area_perimeter([west, east, east, west], [south, south, north, north])
        self.status.configure(text=f"Area: {abs(area_m2) / 1_000_000:,.1f} km2 | {west:.3f}, {south:.3f} a {east:.3f}, {north:.3f}")

    def _start_preview(self) -> None:
        if self.selection is None:
            return
        if not self.api_key:
            self.api_key = simpledialog.askstring("Clave OpenTopography", "Pega tu API key de OpenTopography:", show="*")
        if not self.api_key:
            self.preview_status.configure(text="Necesitas una API key para crear la preview")
            return
        self.render_token += 1
        token = self.render_token
        self.preview_path = None
        self.preview_dem_path = None
        self.save_button.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        options = self._render_options()
        cache_key = (self.selection, options)
        cached = self.preview_cache.get(cache_key)
        LOGGER.info("preview_queued token=%d cache_hit=%s", token, bool(cached and cached.exists()))
        if cached is not None and cached.exists():
            self._show_preview(cached, token)
            return
        self.preview_status.configure(text="Descargando y generando preview...")
        if self.render_future is not None:
            self.render_future.cancel()
        self.render_future = self.render_executor.submit(self._render_preview, self.selection, self.api_key, token, options)
        try:
            Path(".opentopography_key").write_text(self.api_key.strip(), encoding="utf-8")
        except OSError:
            pass

    def _render_options(self) -> tuple[bool, float, bool, str, str, bool, float, str, float, float, float, float, int]:
        return (self.show_contours.get(), self.line_width.get(), self.transparent.get(), self.colormap.get(), self.visual_style.get(), self.fill_surface.get(), self.contour_interval.get(), self.contour_color.get(), self.hillshade_opacity.get(), self.azimuth.get(), self.altitude.get(), self.vertical_exaggeration.get(), self.contour_count.get())

    def _render_preview(self, bounds: tuple[float, float, float, float], api_key: str, token: int, options: tuple[bool, float, bool, str, str, bool, float, str, float, float, float, float, int]) -> None:
        try:
            work_dir = Path(tempfile.gettempdir()) / "topomapas"
            work_dir.mkdir(exist_ok=True)
            dem_path = work_dir / f"preview_{token}.tif"
            preview_path = work_dir / f"preview_{token}.png"
            download_dem(bounds, api_key, dem_path)
            if token != self.render_token:
                return
            self.root.after(0, lambda: self._remember_dem(dem_path, token))
            show_contours, line_width, transparent, colormap, style, fill_surface, interval, contour_color, shade_opacity, azimuth, altitude, exaggeration, contour_count = options
            crop_and_render(dem_path, bounds, preview_path, interval, clean=True, show_contours=show_contours, line_width=line_width, transparent=transparent, colormap=colormap, style=style, preview=True, fill_surface=fill_surface, contour_color=contour_color, hillshade_opacity=shade_opacity, azimuth=azimuth, altitude=altitude, vertical_exaggeration=exaggeration, contour_count=contour_count)
            self.root.after(0, lambda: self._show_preview(preview_path, token))
        except Exception as error:
            LOGGER.exception("preview_error token=%d", token)
            message = str(error)
            self.root.after(0, lambda message=message: self.preview_status.configure(text=f"No se pudo generar: {message}"))

    def _remember_dem(self, path: Path, token: int) -> None:
        if token == self.render_token:
            self.preview_dem_path = path

    def _show_preview(self, path: Path, token: int) -> None:
        if token != self.render_token or not path.exists():
            return
        self.preview_image = Image.open(path).convert("RGBA")
        self.preview_scale = 1.0
        self.preview_origin = (0, 0)
        self._draw_preview()
        self.preview_path = path
        cache_key = (self.selection, self._render_options())
        self.preview_cache[cache_key] = path
        self.preview_cache.move_to_end(cache_key)
        while len(self.preview_cache) > 8:
            self.preview_cache.popitem(last=False)
        self.save_button.configure(state=tk.NORMAL)
        self.refresh_button.configure(state=tk.NORMAL)
        self.preview_status.configure(text="Preview actualizada. Puedes seleccionar otra area.")

    def _draw_preview(self) -> None:
        if not hasattr(self, "preview_image"):
            return
        image = self.preview_image.copy()
        size = (max(1, int(image.width * self.preview_scale)), max(1, int(image.height * self.preview_scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        width = self.preview_canvas.winfo_width()
        height = self.preview_canvas.winfo_height()
        x = width // 2 + self.preview_origin[0]
        y = height // 2 + self.preview_origin[1]
        self.preview_canvas.create_image(x, y, image=self.preview_photo, anchor="center")

    def _preview_zoom(self, event) -> str:
        self.preview_scale = max(0.2, min(5.0, self.preview_scale * (1.15 if event.delta > 0 else 1 / 1.15)))
        self._draw_preview()
        return "break"

    def _preview_pan_start(self, event) -> None:
        self.preview_drag_start = (event.x, event.y)

    def _preview_pan(self, event) -> None:
        if self.preview_drag_start is None:
            return
        last_x, last_y = self.preview_drag_start
        self.preview_origin = (self.preview_origin[0] + event.x - last_x, self.preview_origin[1] + event.y - last_y)
        self.preview_drag_start = (event.x, event.y)
        self._draw_preview()

    def _refresh_preview(self) -> None:
        if self.refresh_after_id is not None:
            self.root.after_cancel(self.refresh_after_id)
        self.refresh_after_id = self.root.after(250, self._refresh_preview_now)

    def _refresh_preview_now(self) -> None:
        self.refresh_after_id = None
        if self.selection is None:
            return
        if self.preview_dem_path is None or not self.preview_dem_path.exists():
            self._start_preview()
            return
        self.render_token += 1
        token = self.render_token
        bounds = self.selection
        options = self._render_options()
        cached = self.preview_cache.get((bounds, options))
        if cached is not None and cached.exists():
            self._show_preview(cached, token)
            return
        self.preview_status.configure(text="Actualizando preview...")
        self.save_button.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        if self.render_future is not None:
            self.render_future.cancel()
        self.render_future = self.render_executor.submit(self._render_existing_preview, self.preview_dem_path, bounds, token, options)

    def _render_existing_preview(self, dem_path: Path, bounds: tuple[float, float, float, float], token: int, options: tuple[bool, float, bool, str, str, bool, float, str, float, float, float, float, int]) -> None:
        try:
            if token != self.render_token:
                return
            preview_path = dem_path.with_name(f"{dem_path.stem}_{token}.png")
            show_contours, line_width, transparent, colormap, style, fill_surface, interval, contour_color, shade_opacity, azimuth, altitude, exaggeration, contour_count = options
            crop_and_render(dem_path, bounds, preview_path, interval, clean=True, show_contours=show_contours, line_width=line_width, transparent=transparent, colormap=colormap, style=style, preview=True, fill_surface=fill_surface, contour_color=contour_color, hillshade_opacity=shade_opacity, azimuth=azimuth, altitude=altitude, vertical_exaggeration=exaggeration, contour_count=contour_count)
            self.root.after(0, lambda: self._show_preview(preview_path, token))
        except Exception as error:
            LOGGER.exception("preview_refresh_error token=%d", token)
            message = str(error)
            self.root.after(0, lambda message=message: self.preview_status.configure(text=f"No se pudo actualizar: {message}"))

    def _delete_polygon(self) -> None:
        if self.selection_polygon is not None:
            self.selection_polygon.delete()
            self.selection_polygon = None
        self.selection = None

    def clear_selection(self) -> None:
        self.render_token += 1
        self._delete_polygon()
        self.preview_path = None
        self.preview_dem_path = None
        self.save_button.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(220, 220, text="La topografia aparecera aqui", fill="#a8bbc8")
        self.preview_status.configure(text="Dibuja un area para generar la imagen")
        self.status.configure(text="Mapa listo: navega con izquierdo y selecciona con derecho")

    def export_map(self) -> None:
        if self.preview_path is None or not self.preview_path.exists():
            messagebox.showwarning("Preview no disponible", "Selecciona un area y espera a que termine la preview.")
            return
        destination = filedialog.asksaveasfilename(title="Guardar imagen topografica", defaultextension=".png", filetypes=[("PNG", "*.png")])
        if destination:
            shutil.copyfile(self.preview_path, destination)
            self.status.configure(text=f"Imagen guardada en {destination}")


def main() -> None:
    root = tk.Tk()
    app = TopographyApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.render_executor.shutdown(wait=False, cancel_futures=True), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
