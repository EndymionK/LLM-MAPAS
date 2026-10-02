import time
import tracemalloc

import numpy as np
import pytest
from affine import Affine
from PIL import Image

from topografia import create_topographic_figure

pytestmark = pytest.mark.performance


def test_preview_is_bounded_and_finishes_without_excess_memory(tmp_path):
    values = np.add.outer(np.linspace(100, 1200, 1600), np.linspace(0, 500, 1600)).astype("float32")
    output = tmp_path / "preview.png"
    tracemalloc.start()
    started = time.perf_counter()
    create_topographic_figure(
        values,
        Affine(10, 0, 500000, 0, -10, 4600000),
        output,
        clean=True,
        preview=True,
        style="Curvas oscuras",
    )
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    width, height = Image.open(output).size
    assert width <= 1000 and height <= 1000
    assert elapsed < 8.0
    assert peak < 180 * 1024 * 1024


def test_preview_paths_stay_within_render_budget(tmp_path):
    values = np.add.outer(np.linspace(100, 1200, 700), np.linspace(0, 500, 700)).astype("float32")
    transform = Affine(10, 0, 500000, 0, -10, 4600000)
    plain = time.perf_counter()
    create_topographic_figure(values, transform, tmp_path / "plain.png", clean=True, preview=True, show_contours=False, style="Curvas oscuras")
    plain_time = time.perf_counter() - plain
    contoured = time.perf_counter()
    create_topographic_figure(values, transform, tmp_path / "contours.png", clean=True, preview=True, show_contours=True, style="Curvas oscuras")
    contour_time = time.perf_counter() - contoured
    assert plain_time < 4.0
    assert contour_time < 8.0