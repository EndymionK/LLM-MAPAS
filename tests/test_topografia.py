from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.windows import Window

from topografia import _contour_levels, create_topographic_figure, selection_window
from dem_api import build_dem_params
from dem_api import download_dem


def test_selection_window_is_clipped_to_raster():
    transform = Affine(1, 0, 0, 0, -1, 10)
    window = selection_window((-3, 1, 5, 12), transform, 10, 10)
    assert window == Window(0, 0, 5, 9)


def test_create_topographic_figure_writes_png(tmp_path: Path):
    values = np.add.outer(np.linspace(100, 300, 32), np.linspace(0, 80, 32))
    output = create_topographic_figure(values, Affine(10, 0, 500000, 0, -10, 4600000), tmp_path / "mapa.png")
    assert output.exists()
    assert output.stat().st_size > 1000


def test_clean_render_preserves_data_ratio(tmp_path: Path):
    values = np.add.outer(np.linspace(100, 300, 20), np.linspace(0, 80, 40))
    output = create_topographic_figure(
        values,
        Affine(10, 0, 500000, 0, -10, 4600000),
        tmp_path / "proporcion.png",
        clean=True,
        style="Curvas oscuras",
    )
    from PIL import Image

    width, height = Image.open(output).size
    assert abs((width / height) - 2.0) < 0.08


def test_build_dem_params_uses_wgs84_bbox():
    params = build_dem_params((-3.2, 40.1, -3.0, 40.3), "test-key")
    assert params["demtype"] == "SRTMGL1"
    assert params["outputFormat"] == "GTiff"
    assert params["south"] == 40.1


def test_contour_count_controls_number_of_levels():
    values = np.linspace(100, 900, 100)
    assert len(_contour_levels(values, 20, 12)) == 12


def test_download_dem_writes_all_stream_chunks(monkeypatch, tmp_path: Path):
    class FakeResponse:
        content_type = "image/tiff"

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"II" + b"first"
            yield b"second" * 100

    monkeypatch.setattr("dem_api.requests.get", lambda *args, **kwargs: FakeResponse())
    output = download_dem((1, 2, 3, 4), "key", tmp_path / "dem.tif")
    assert output.read_bytes() == b"IIfirst" + b"second" * 100