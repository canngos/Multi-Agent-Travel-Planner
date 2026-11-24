// The guide writer's map file (GeoJSON from export_map_stops) as the stay plus the stops grouped by day, for the
// Guide map tab. Also reads map files from other tools: points without a day go into one "Other places" group.

export interface MapStop {
  order: number | null;
  day: number | null;
  date: string | null;
  time: string | null;
  kind: string | null;
  name: string;
  url: string | null;
  lat: number;
  lon: number;
}

export interface MapDay { day: number | null; date: string | null; stops: MapStop[] } // day null = "Other places"
export interface TripMap { stay: MapStop | null; days: MapDay[] }

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);

/** Parses GeoJSON text into a TripMap. Throws an Error with a message fit to show the user. */
export function parseTripMap(text: string): TripMap {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("This file is not valid JSON.");
  }
  const features = (data as { type?: unknown; features?: unknown } | null)?.type === "FeatureCollection"
    ? (data as { features?: unknown }).features : null;

  let stay: MapStop | null = null;
  const stops: MapStop[] = [];
  for (const f of Array.isArray(features) ? features : []) {
    const geometry = (f as { geometry?: { type?: unknown; coordinates?: unknown } } | null)?.geometry;
    if (geometry?.type !== "Point" || !Array.isArray(geometry.coordinates)) continue;
    const [lon, lat] = geometry.coordinates.map(num);
    // out of range = not degrees (e.g. metres from a projected CRS); such points would pile up at the map's edge
    if (lon === null || lat === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
    const p = ((f as { properties?: unknown }).properties ?? {}) as Record<string, unknown>;
    const s: MapStop = { order: num(p.order), day: num(p.day), date: str(p.date), time: str(p.time), kind: str(p.kind),
      name: str(p.name) ?? "Unnamed place", url: str(p.url), lat, lon };
    if (s.kind === "stay") stay ??= s;
    else stops.push(s);
  }
  if (!stay && !stops.length) throw new Error("This file has no places to show on a map.");

  const byDay = new Map<number | null, MapStop[]>();
  for (const s of stops) byDay.set(s.day, [...(byDay.get(s.day) ?? []), s]);
  const days = [...byDay.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : a - b))
    .map(([day, list]) => {
      // stable sort: stops without an order keep their file order
      const sorted = day === null ? list : [...list].sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity));
      return { day, date: sorted.find((s) => s.date)?.date ?? null, stops: sorted };
    });
  return { stay, days };
}

/** The line joining a day's stops in visiting order, as [lat, lon] points; null for a single stop and for the
 *  "Other places" group (points from other tools have no order, so a line through them would mean nothing). */
export function dayRoute(day: MapDay): [number, number][] | null {
  if (day.day === null || day.stops.length < 2) return null;
  return day.stops.map((s) => [s.lat, s.lon]);
}

const HTML_ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escapes text for use inside HTML (Leaflet popups take HTML strings). */
export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}
