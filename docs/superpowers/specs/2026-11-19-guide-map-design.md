# Guide map tab: design (short)

Date: 2026-11-19. Status: approved by the user in chat.

## Why

The guide writer already saves the plan's places as GeoJSON (`export_map_stops`), but the UI only offers it as a
download. The user wants to see the trip on a real map inside the UI, and, because a user may make more than one
travel plan, to open the map files of other plans there too.

## Goals

- A **Guide map** tab (after "Guide") that draws the current run's `run.state.guide_exports.map` on a street map.
- An **Import** button in that tab to open other `.geojson` map files (for example from `output/`) and switch between
  them and the current run's plan.

## Non-goals

- No backend change; no new state keys.
- Imported maps are not saved anywhere: they last until the page is reloaded.
- No editing of the plan from the map.
- No overlaying of two plans on one map (different trips are usually different cities).

## Approaches considered

1. **Leaflet + OpenStreetMap tiles** (picked by the user): real streets; one npm dependency; tiles need internet.
2. Self-drawn SVG (pins and lines placed by coordinates): offline, no dependency, but no streets, so of little use
   for a travel map.
3. MapLibre GL: vector tiles, heavier, needs a tile/style provider; no gain over 1 for seven pins.

## Design

- **Data helper** `travel-planner-ui/src/lib/geo.ts`: parses GeoJSON text into `{ stay, days: [{ day, date, stops }] }`
  (stops carry order, time, kind, name, url, lat, lon). Tolerates missing properties; rejects text that is not a
  FeatureCollection with at least one Point. Unit-tested with vitest.
- **Component** `travel-planner-ui/src/components/GuideMap.tsx`, plain `leaflet` (+ `@types/leaflet`) in a `useEffect`
  (no `react-leaflet`).
  - OSM tiles with the required attribution.
  - Stay: a house-shaped pin. Stops: numbered circles (visiting order), one colour per day, as `divIcon`s (avoids
    Leaflet's bundler marker-image problem).
  - One polyline per day joining that day's stops in order.
  - Popup per pin: day, date, time, kind, name, link.
  - The map fits all points.
  - Under the map: stops grouped by day; clicking one centres the map on it and opens its popup.
  - A small note that the street background needs internet (pins and lines draw offline).
- **Plan switcher and import**: a row above the map with a select ("This run's plan" plus each imported file by its
  file name) and an **Import map file** button (`accept=".geojson,application/geo+json,application/json"`). Importing
  adds the file and switches to it; a bad file shows an inline error and changes nothing. Re-importing a file with the
  same name replaces that entry.
- **Tab**: shows the "ready" dot when the current run has a map. With no current-run map and nothing imported, the tab
  shows an empty-state message plus the Import button, so it is usable before or without a run.
- **Panels.tsx**: the download button's tooltip drops "Google My Maps" (it does not import GeoJSON).

## Calls made by Claude (user may override)

- Tiles stay light in the dark theme.
- Imported maps live only in memory for the session.
- The switcher lists the current run's plan first, then imports in the order they were opened.

## Left to the builder

Colours per day (pick from the existing line colours in `styles.css`), exact marker sizes, CSS layout.
