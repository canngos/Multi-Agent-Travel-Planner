import "leaflet/dist/leaflet.css";

import L from "leaflet";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";

import { dayRoute, escapeHtml, parseTripMap, type MapStop, type TripMap } from "../lib/geo";
import type { RunState } from "../lib/run";

/** A .geojson map file the user opened in the Guide map tab (kept in App so it survives tab switches). */
export interface ImportedMap { name: string; text: string }

interface Props {
  run: RunState;
  imports: ImportedMap[];
  onImport: (m: ImportedMap) => void;
  /** "run" (this run's plan) or an imported file name; kept in App like the imports */
  selected: string;
  onSelect: (name: string) => void;
}

const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const DAY_COLOURS = ["--line-transport", "--line-stay", "--line-culture", "--line-food", "--line-review"];
const RUN_PLAN = "run";

const dayColourVar = (index: number, day: number | null) => (day === null ? "--ink-3" : DAY_COLOURS[index % DAY_COLOURS.length]);
const dayLabel = (day: number | null) => (day === null ? "Other places" : `Day ${day}`);
const isWebLink = (url: string | null): url is string => !!url && /^https?:\/\//i.test(url);

function popupHtml(s: MapStop, isStay: boolean): string {
  const when = [s.day !== null ? `Day ${s.day}` : null, s.date, s.time].filter(Boolean).join(" · ");
  const kind = isStay ? "Where you stay" : s.kind;
  return [
    `<strong>${escapeHtml(s.name)}</strong>`,
    when && escapeHtml(when),
    kind && `<span class="pin-kind">${escapeHtml(kind)}</span>`,
    isWebLink(s.url) && `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">Open link</a>`,
  ].filter(Boolean).join("<br>");
}

export function GuideMapPanel({ run, imports, onImport, selected, onSelect }: Props) {
  const exportsState = run.state.guide_exports as { map?: unknown } | undefined;
  const runText = typeof exportsState?.map === "string" ? exportsState.map : null;
  const [importError, setImportError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // the chosen source, falling back to the run's plan, then to the first imported file
  const source = imports.find((m) => m.name === selected) ?? (runText !== null ? { name: RUN_PLAN, text: runText } : imports[0]);
  const sourceText = source?.text;
  const parsed = useMemo<{ trip: TripMap | null; error: string }>(() => {
    if (sourceText === undefined) return { trip: null, error: "" };
    try {
      return { trip: parseTripMap(sourceText), error: "" };
    } catch (err) {
      return { trip: null, error: (err as Error).message };
    }
  }, [sourceText]);
  const trip = parsed.trip;

  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markers = useRef(new Map<MapStop, L.Marker>());

  // create the map when there is something to show; remove it when the panel goes away
  const hasMap = trip !== null;
  useEffect(() => {
    if (!hasMap || !container.current) return;
    const map = L.map(container.current);
    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(map);
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, [hasMap]);

  // draw the stay, the stops and one line per day; redrawn from scratch for every plan
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !trip || !container.current) return;
    const css = getComputedStyle(container.current);
    const colour = (name: string) => css.getPropertyValue(name).trim() || "#4a5568";
    const group = L.layerGroup().addTo(map);
    const byStop = new Map<MapStop, L.Marker>();
    const points: L.LatLngExpression[] = [];

    if (trip.stay) {
      const s = trip.stay;
      const icon = L.divIcon({ className: "pin pin-stay", html: "⌂", iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -15] });
      byStop.set(s, L.marker([s.lat, s.lon], { icon, title: s.name, zIndexOffset: 1000 }).bindPopup(popupHtml(s, true)).addTo(group));
      points.push([s.lat, s.lon]);
    }
    trip.days.forEach((d, i) => {
      const c = colour(dayColourVar(i, d.day));
      const route = dayRoute(d);
      if (route) L.polyline(route, { color: c, weight: 3, opacity: 0.8 }).addTo(group);
      for (const s of d.stops) {
        const icon = L.divIcon({ className: "pin", html: `<span style="background:${c}">${s.order ?? "•"}</span>`,
          iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -13] });
        byStop.set(s, L.marker([s.lat, s.lon], { icon, title: s.name }).bindPopup(popupHtml(s, false)).addTo(group));
        points.push([s.lat, s.lon]);
      }
    });
    markers.current = byStop;
    map.invalidateSize();
    map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 15 });
    return () => { group.remove(); };
  }, [trip]);

  const focus = (s: MapStop) => {
    const map = mapRef.current;
    const marker = markers.current.get(s);
    if (!map || !marker) return;
    map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15));
    marker.openPopup();
  };

  const openFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    const text = await f.text();
    try {
      parseTripMap(text);
    } catch (err) {
      setImportError(`${f.name}: ${(err as Error).message}`);
      return;
    }
    setImportError("");
    onImport({ name: f.name, text });
    onSelect(f.name);
  };

  const importButton = (
    <>
      <button type="button" onClick={() => fileInput.current?.click()}>Import map file</button>
      <input ref={fileInput} type="file" accept=".geojson,application/geo+json,application/json" hidden onChange={openFile} />
    </>
  );

  if (!source) {
    return (
      <div className="guide-map-panel">
        <p className="empty-state">
          The map of the planned places appears here when the guide writer has saved the guide. You can also open a
          .geojson map file of another plan.
        </p>
        <div className="map-sources">{importButton}</div>
        {importError && <p className="error-text" role="alert">{importError}</p>}
      </div>
    );
  }

  return (
    <div className="guide-map-panel">
      <div className="map-sources">
        <label>
          Showing{" "}
          <select value={source.name} onChange={(e) => { onSelect(e.target.value); setImportError(""); }}>
            {runText !== null && <option value={RUN_PLAN}>This run's plan</option>}
            {imports.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
        </label>
        {importButton}
        {importError && <span className="error-text" role="alert">{importError}</span>}
      </div>
      {parsed.error && <p className="error-text" role="alert">This map cannot be shown: {parsed.error}</p>}
      {trip && (
        <>
          <div ref={container} className="guide-map" role="region" aria-label="Map of the planned places" />
          <p className="muted map-note">Street map tiles come from OpenStreetMap and need an internet connection; the pins work offline.</p>
          <ol className="map-stops">
            {trip.stay && (
              <li>
                <button type="button" onClick={() => focus(trip.stay!)}>
                  <span className="pin-dot pin-dot-stay" aria-hidden="true">⌂</span> {trip.stay.name}
                </button>
              </li>
            )}
            {trip.days.map((d, i) => (
              <li key={d.day ?? "other"}>
                <h4>
                  <span className="day-swatch" style={{ background: `var(${dayColourVar(i, d.day)})` }} aria-hidden="true" />
                  {dayLabel(d.day)}{d.date ? <span className="muted"> · {d.date}</span> : null}
                </h4>
                <ul>
                  {d.stops.map((s, j) => (
                    <li key={j}>
                      <button type="button" onClick={() => focus(s)}>
                        <span className="pin-dot" style={{ background: `var(${dayColourVar(i, d.day)})` }} aria-hidden="true">{s.order ?? "•"}</span>
                        {" "}{s.name}{s.time ? <span className="muted"> · {s.time}</span> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
