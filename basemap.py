"""Carga una vista mundial de OpenTopoMap usando teselas publicas."""

from io import BytesIO

import numpy as np
import requests
from PIL import Image

WEB_MERCATOR_LIMIT = 20037508.342789244


def world_tiles(zoom: int = 2) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Devuelve el mosaico mundial y su extent en Web Mercator."""
    size = 256
    count = 2**zoom
    mosaic = Image.new("RGB", (count * size, count * size), "#d9e2df")
    for x in range(count):
        for y in range(count):
            url = f"https://{['a', 'b', 'c'][x % 3]}.tile.opentopomap.org/{zoom}/{x}/{y}.png"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            mosaic.paste(Image.open(BytesIO(response.content)).convert("RGB"), (x * size, y * size))
    return np.asarray(mosaic), (-WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT, -WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT)