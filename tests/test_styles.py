from pathlib import Path

import numpy as np
from affine import Affine
from PIL import Image

from topografia import create_topographic_figure


def test_styles_and_line_density_produce_distinct_images(tmp_path: Path):
    rows, columns = np.indices((80, 120))
    values = 500 + rows * 2 + columns + 80 * np.sin(rows / 8) * np.cos(columns / 11)
    transform = Affine(10, 0, 500000, 0, -10, 4600000)
    styles = ("Relieve natural", "Curvas oscuras", "Curvas claras", "Contraste negro", "Curvas transparentes")
    outputs = []
    for index, style in enumerate(styles):
        output = tmp_path / f"style_{index}.png"
        create_topographic_figure(values, transform, output, clean=True, preview=True, style=style, contour_count=12)
        outputs.append(Image.open(output).convert("RGBA"))
    assert len({image.tobytes() for image in outputs}) == len(styles)
    sparse = tmp_path / "sparse.png"
    dense = tmp_path / "dense.png"
    create_topographic_figure(values, transform, sparse, clean=True, preview=True, style="Curvas oscuras", contour_count=8)
    create_topographic_figure(values, transform, dense, clean=True, preview=True, style="Curvas oscuras", contour_count=80)
    sparse_pixels = np.asarray(Image.open(sparse).convert("RGB"))
    dense_pixels = np.asarray(Image.open(dense).convert("RGB"))
    assert np.any(sparse_pixels != dense_pixels)