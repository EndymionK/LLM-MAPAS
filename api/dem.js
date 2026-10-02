const API_URL = "https://portal.opentopography.org/API/globaldem";
const COLOMBIA = { west: -79.1, south: -4.3, east: -66.8, north: 13.6 };

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function validBounds(body) {
  const bounds = ["west", "south", "east", "north"].reduce((result, key) => {
    result[key] = number(body?.[key]);
    return result;
  }, {});
  if (Object.values(bounds).some((value) => value === null)) return null;
  if (bounds.west >= bounds.east || bounds.south >= bounds.north) return null;
  if (bounds.east < COLOMBIA.west || bounds.west > COLOMBIA.east || bounds.north < COLOMBIA.south || bounds.south > COLOMBIA.north) return null;
  const area = (bounds.east - bounds.west) * (bounds.north - bounds.south);
  return area > 0 && area <= 16 ? bounds : null;
}

export default async function handler(request, response) {
  if (request.method !== "POST") {
    return response.status(405).json({ error: "Metodo no permitido" });
  }
  const bounds = validBounds(request.body);
  const apiKey = process.env.OPEN_TOPOGRAPHY_API_KEY;
  if (!bounds) return response.status(400).json({ error: "Selecciona un area valida dentro de Colombia." });
  if (!apiKey) return response.status(500).json({ error: "Falta configurar OPEN_TOPOGRAPHY_API_KEY en Vercel." });

  const params = new URLSearchParams({
    demtype: "SRTMGL1",
    south: String(bounds.south),
    north: String(bounds.north),
    west: String(bounds.west),
    east: String(bounds.east),
    outputFormat: "GTiff",
    API_Key: apiKey,
  });
  try {
    const upstream = await fetch(`${API_URL}?${params}`);
    if (!upstream.ok) {
      const message = await upstream.text();
      return response.status(upstream.status).json({ error: message.slice(0, 500) });
    }
    const contentType = upstream.headers.get("content-type") || "image/tiff";
    response.setHeader("Content-Type", contentType);
    response.setHeader("Cache-Control", "private, max-age=300");
    return response.status(200).send(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    return response.status(502).json({ error: `No se pudo descargar el DEM: ${error.message}` });
  }
}
