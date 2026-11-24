import { describe, expect, it } from "vitest";

import { dayRoute, escapeHtml, parseTripMap } from "./geo";

const point = (lon: unknown, lat: unknown, properties: Record<string, unknown>) =>
  ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties });

const stop = (order: number, day: number, name: string, lon = -9.15, lat = 38.71) =>
  point(lon, lat, { order, day, date: `2027-05-1${day}`, time: "10:00", kind: "sight", name, url: null });

// the guide writer's Lisbon map (output/lisbon-2027-05-10-to-2027-05-14.geojson): the stay and six stops on days 2-4
const LISBON = {
  type: "FeatureCollection",
  features: [
    point(-9.1331, 38.7471, { kind: "stay", name: "Cantinho dos Pacatos - Bunk bed (red)", url: "https://example.com/stay" }),
    stop(1, 2, "Monument to the Discoveries"),
    stop(2, 2, "Ajuda Botanical Gardens"),
    stop(3, 3, "National Museum of Ancient Art"),
    stop(4, 3, "Rosa da Rua Restaurante"),
    stop(5, 3, "MAAT"),
    stop(6, 4, "Belém Tower"),
  ],
};

const collection = (features: unknown[]) => JSON.stringify({ type: "FeatureCollection", features });

describe("parseTripMap", () => {
  it("parses the stay and groups stops by day", () => {
    const map = parseTripMap(JSON.stringify(LISBON));
    expect(map.stay?.name).toBe("Cantinho dos Pacatos - Bunk bed (red)");
    expect(map.stay?.lat).toBe(38.7471); // GeoJSON is [lon, lat]
    expect(map.stay?.lon).toBe(-9.1331);
    expect(map.days.map((d) => d.day)).toEqual([2, 3, 4]);
    expect(map.days[1].stops.map((s) => s.order)).toEqual([3, 4, 5]);
    expect(map.days[1].date).toBe("2027-05-13");
  });

  it("orders stops within a day by order even if the file is shuffled", () => {
    const map = parseTripMap(collection([stop(5, 3, "E"), stop(3, 3, "C"), stop(1, 2, "A"), stop(4, 3, "D")]));
    expect(map.days.map((d) => d.day)).toEqual([2, 3]);
    expect(map.days[1].stops.map((s) => s.name)).toEqual(["C", "D", "E"]);
  });

  it("puts points without day into one null-day group after the numbered days, in file order", () => {
    const map = parseTripMap(collection([point(2, 50, { name: "Other B" }), stop(1, 1, "A"), point(3, 51, {})]));
    expect(map.days.map((d) => d.day)).toEqual([1, null]);
    expect(map.days[1].stops.map((s) => s.name)).toEqual(["Other B", "Unnamed place"]);
    expect(map.days[1].stops[0]).toMatchObject({ order: null, lat: 50, lon: 2 });
    expect(map.stay).toBeNull();
  });

  it("rejects invalid JSON", () => {
    expect(() => parseTripMap("{not json")).toThrow(/not valid JSON/);
  });

  it("rejects GeoJSON with no points", () => {
    const polygon = { type: "Feature", geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, properties: {} };
    expect(() => parseTripMap(collection([polygon]))).toThrow(/no places/);
    expect(() => parseTripMap(JSON.stringify(stop(1, 1, "A")))).toThrow(/no places/); // a bare Feature
    expect(() => parseTripMap("[]")).toThrow(/no places/);
    expect(() => parseTripMap("null")).toThrow(/no places/);
  });

  it("skips points outside latitude -90..90 or longitude -180..180 (e.g. metres in a projected CRS)", () => {
    const map = parseTripMap(collection([point(-1017000, 4680000, { name: "Metres", day: 1, order: 1 }), stop(2, 1, "Good")]));
    expect(map.days[0].stops.map((s) => s.name)).toEqual(["Good"]);
    expect(() => parseTripMap(collection([point(-1017000, 4680000, { name: "Metres" }), point(10, 91, {})]))).toThrow(/no places/);
  });

  it("skips features with non-numeric coordinates", () => {
    const map = parseTripMap(collection([point("a", 1, { name: "Bad", day: 1, order: 1 }), stop(2, 1, "Good")]));
    expect(map.days[0].stops.map((s) => s.name)).toEqual(["Good"]);
  });
});

describe("dayRoute", () => {
  it("joins a day's stops in order as [lat, lon] points", () => {
    const map = parseTripMap(JSON.stringify(LISBON));
    expect(dayRoute(map.days[0])).toEqual([[38.71, -9.15], [38.71, -9.15]]);
  });

  it("draws no route for a day with one stop or for the Other places group", () => {
    const map = parseTripMap(collection([stop(1, 1, "Only"), point(2, 50, { name: "A" }), point(3, 51, { name: "B" })]));
    expect(dayRoute(map.days[0])).toBeNull();
    expect(map.days[1].day).toBeNull();
    expect(dayRoute(map.days[1])).toBeNull();
  });
});

describe("escapeHtml", () => {
  it("escapes & < > \" '", () => {
    expect(escapeHtml('<b a="x">&\'')).toBe("&lt;b a=&quot;x&quot;&gt;&amp;&#39;");
  });
});
