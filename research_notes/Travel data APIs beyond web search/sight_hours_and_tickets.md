# Opening hours and ticket/admission prices for European sights: APIs, open data and MCP servers (vs. Tavily web search)

Research date: 2026-11-17. Focus cities: Lisbon (PT), Tallinn (EE), Helsinki (FI). Planner stack already uses OpenTripMap, Wikivoyage listings, Wikipedia, OSM Overpass.
Method: web search + official docs, plus **live queries run during this research** (Overpass API, Wikidata API/SPARQL, Helsinki Servicemap/Hauki/Linked Events, MyHelsinki, PyPI). Live-query results are marked "(live query, 2026-11-17)".

## 1. OpenStreetMap tags (opening_hours, fee, charge) for sights in Lisbon, Tallinn, Helsinki; parsers

### Takeaway
For **museums** OSM is surprisingly good: `opening_hours` is filled on 66% (Lisbon), 82% (Tallinn) and 63% (Helsinki) of `tourism=museum` objects, and `fee` on 63–71%, including most flagship museums. For attractions, viewpoints and `historic=*` it is poor (0–15% hours). `charge` (the actual price) is nearly empty except in Lisbon museums (27%). Verdict: beats web search for **museum hours** (structured, free, parseable) but not for prices or non-museum sights.

### Cited Findings
- Method note: public Overpass endpoints (overpass-api.de, private.coffee, mail.ru, osm.jp) timed out during this session after one successful count (Tallinn: 67 museums, matches the figure below). Fill rates were therefore computed on the **QLever OSM-planet SPARQL endpoint** (osm2rdf), counting objects inside the admin boundary via `ogc:sfContains` — Lisbon municipality relation 5400890 (admin_level 7), Tallinn 2164745 (level 8), Helsinki 34914 (level 8). (live query, 2026-11-17) — [QLever OSM planet](https://qlever.cs.uni-freiburg.de/osm-planet); [Overpass API](https://overpass-api.de/)
- Fill rates (count with tag / total) (live query, 2026-11-17) — [QLever OSM planet](https://qlever.cs.uni-freiburg.de/osm-planet):

| City | Selector | Total | opening_hours | fee | charge |
|---|---|---|---|---|---|
| Lisbon | tourism=museum | 104 | 69 (66%) | 69 (66%) | 28 (27%) |
| Lisbon | tourism=attraction | 224 | 33 (15%) | 7 (3%) | 2 (1%) |
| Lisbon | tourism=viewpoint | 102 | 1 (1%) | 6 (6%) | 1 (1%) |
| Lisbon | historic=* | 1054 | 71 (7%) | 14 (1%) | 2 (0%) |
| Lisbon | historic=* with name+wikidata | 294 | 16 (5%) | 7 (2%) | 2 (1%) |
| Tallinn | tourism=museum | 67 | 55 (82%) | 42 (63%) | 1 (1%) |
| Tallinn | tourism=attraction | 36 | 4 (11%) | 2 (6%) | 0 |
| Tallinn | tourism=viewpoint | 41 | 1 (2%) | 1 (2%) | 0 |
| Tallinn | historic=* | 239 | 3 (1%) | 1 (0%) | 0 |
| Tallinn | historic=* with name+wikidata | 97 | 3 (3%) | 1 (1%) | 0 |
| Helsinki | tourism=museum | 49 | 31 (63%) | 35 (71%) | 1 (2%) |
| Helsinki | tourism=attraction | 190 | 15 (8%) | 71 (37%) | 2 (1%) |
| Helsinki | tourism=viewpoint | 141 | 0 | 0 | 0 |
| Helsinki | historic=* | 743 | 3 (0%) | 1 (0%) | 1 (0%) |
| Helsinki | historic=* with name+wikidata | 130 | 2 (2%) | 1 (1%) | 1 (1%) |

- Flagship examples (live query, 2026-11-17) — [QLever OSM planet](https://qlever.cs.uni-freiburg.de/osm-planet):
  - Lisbon: Mosteiro dos Jerónimos `Oct-Apr 10:00-17:30; May-Sep 10:00-18:30; Mo off; Jan 1 off; May 1 off; Dec 25 off`, fee=yes, no charge; Museu Calouste Gulbenkian `We-Mo 10:00-18:00`; MAAT `Mo-Su 12:00-20:00; Tu off; ...`; Museu Nacional do Azulejo `opening_hours=closed` (fee=yes); Núcleo Museológico do Castelo de São Jorge: no hours/fee. Lisbon `charge` examples: "10 EUR", "15 EUR", "8.00 EUR", "3 EUR".
  - Tallinn: Kumu `Th 10:00-20:00; Tu-We,Fr-Su 10:00-18:00`; Kadrioru Kunstimuuseum (Kadriorg Art Museum) `Oct-Apr We-Su 10:00-17:00; May-Sep Tu-Su 10:00-17:00`; Lennusadam (Seaplane Harbour) seasonal hours; Estonian Open Air Museum seasonal hours; Fotografiska hours; all fee=yes, no charge. Kadrioru loss (palace building): fee=yes, no hours. Only one charge value in Tallinn ("5 EUR").
  - Helsinki: Helsingin kaupunginmuseo `Mo-Fr 11:00-19:00; Sa-Su 11:00-17:00`, fee=no (matches the city's Hauki data, see §5); Kansallismuseo `opening_hours=Closed until 2027` (non-standard free text). Ateneum and Kiasma did not appear among `tourism=museum`-tagged named objects in the boundary (likely tagged differently, e.g. `tourism=gallery`, or mapped as building outlines). Single charge example: "5 EUR/adult;12 EUR/family;3 EUR/student".
- Data-quality caveat seen in the sample: non-standard values ("closed", "Closed until 2027", "\"by appointment\"") that a strict parser will reject — [QLever OSM planet](https://qlever.cs.uni-freiburg.de/osm-planet)
- Python parsers for the OSM `opening_hours` syntax exist on PyPI: `opening_hours_py` v2.1.6 ("A parser for the opening_hours fields from OpenStreetMap", Python binding of the Rust `opening-hours-rs` crate by remi-dupre) and `osm_opening_hours` v0.1.1 (k-nut, small/old). `humanized-opening-hours` is not on PyPI under that name (live PyPI JSON API query, 2026-11-17) — [PyPI opening_hours_py](https://pypi.org/project/opening-hours-py/); [GitHub opening-hours-rs](https://github.com/remi-dupre/opening-hours-rs/tree/master/opening-hours-py); [PyPI osm_opening_hours](https://pypi.org/project/osm-opening-hours/)
- Geoapify Place Details exposes OSM-derived `opening_hours` as a raw string field — [Geoapify Place Details API docs](https://apidocs.geoapify.com/docs/place-details/)

### Inferences
- OSM `opening_hours` is free, ODbL-licensed, already reachable through the planner's existing Overpass client, and machine-parseable with `opening_hours_py` (can answer "is it open on Tuesday 10:00?"). It is the cheapest first source for hours, but `fee` is only yes/no and `charge` (actual price) is almost never filled, so OSM does **not** solve ticket prices.
- OSM hours have no "last verified" guarantee (optional `check_date` tag); treat them as "probable" and tell users to verify.

### Gaps
- Did not measure staleness/accuracy of OSM hours against official museum websites.
- taginfo per-city instances were not consulted; fill rates come from QLever (osm2rdf) counts, whose OSM snapshot date was not checked (may lag live OSM by days/weeks).
- Ateneum/Kiasma tagging in OSM not investigated; `tourism=gallery` was not counted.

## 2. Wikidata properties for opening hours / admission fee

### Takeaway
Wikidata has the right properties (P3025 "open days" with qualifiers P8626 "opening time"/P8627 "closing time"; P2555 "fee") but they are essentially **empty** for the target cities: none of 10 checked flagship sights has hours or a fee, and only 2/346 Estonian and 4/1076 Finnish museum items have P3025, with 0 having P2555. Verdict: does not beat web search; useful only for IDs, official website (P856) and visitor counts.

### Cited Findings
- Property labels/datatypes (live Wikidata API, 2026-11-17): P3025 = "open days" (item); P8626 = "opening time"; P8627 = "closing time"; P2555 = "fee" (quantity). Note: **P8989 is "category for the view of the item"**, not an opening-hours property (the brief's guess is wrong) — [Wikidata P3025](https://www.wikidata.org/wiki/Property:P3025); [Wikidata P2555](https://www.wikidata.org/wiki/Property:P2555); [Wikidata P8989](https://www.wikidata.org/wiki/Property:P8989)
- Global usage (live SPARQL count of `wdt:` statements, 2026-11-17): P3025 = 59,803 statements; P2555 = 5,405; P3026 ("closed on") = 1,135; P3027/P3028 = 14/11. I.e. fee data is rare worldwide — [Wikidata Query Service](https://query.wikidata.org/)
- Flagship sights checked for P3025, P2555, P8626, P8627 (live wbgetentities, 2026-11-17): Belém Tower (Q215003), Jerónimos Monastery (Q272781), Calouste Gulbenkian Museum (Q211262), Kadriorg Palace (Q7722735), Suomenlinna (Q1292442), Helsinki Cathedral (Q738015), Ateneum (Q754507), Seurasaari Open-Air Museum (Q84305534), National Museum of Finland (Q1418136) — **none** has hours or fee statements. Most do have P856 official website; Belém Tower, Jerónimos, Ateneum, National Museum of Finland have P1174 visitor numbers (e.g. Jerónimos 1,096,283) — [Wikidata Q215003](https://www.wikidata.org/wiki/Q215003); [Q272781](https://www.wikidata.org/wiki/Q272781); [Q754507](https://www.wikidata.org/wiki/Q754507)
- São Jorge Castle was not resolved by the English search API in this run (search returned a JSON error on the non-ASCII label); not checked.
- Museums (P31/P279* Q33506) by country with hours/fee (live SPARQL, 2026-11-17): Estonia 346 museums, 2 with P3025, 0 with P2555; Finland 1,076 museums, 4 with P3025, 0 with P2555. Portugal query timed out — [Wikidata Query Service](https://query.wikidata.org/)

### Inferences
- Wikidata is still worth a call for the **P856 official website** URL, which an agent can then fetch to read hours/prices directly from the museum's own site — more reliable than a generic Tavily query.
- The P3025 statements that exist globally are mostly day-of-week markers without times; even where present the model is awkward for seasonal schedules.

### Gaps
- Portugal-wide museum counts not obtained (query timeout). São Jorge Castle and Kumu items not checked (search mismatch).

## 3. Commercial APIs with free tiers (Google Places New, Geoapify, HERE, TomTom, Foursquare, TripAdvisor)

### Takeaway
Google Places (New) has by far the best hours coverage, but hours sit in the **Enterprise** SKU (1,000 free calls/month per SKU, billing account with card required) and never include admission prices. Geoapify is the only no-card option with hours, but it re-serves OSM (no gain over Overpass). TomTom Freemium (2,500 req/day, no card) offers opening hours via Place by ID. None of these returns ticket prices.

### Cited Findings
**Google Places API (New)**
- Since 1 March 2025 the $200 monthly credit was replaced with per-SKU free monthly caps: Essentials 10,000, Pro 5,000, Enterprise 1,000; caps do not pool across SKUs — [Google Maps Platform pricing](https://developers.google.com/maps/billing-and-pricing/pricing); [Google March 2025 changes](https://developers.google.com/maps/billing-and-pricing/march-2025); [Woosmap blog](https://www.woosmap.com/blog/google-places-api-pricing)
- Official price list: Place Details Essentials $5.00/1k (10k free), Pro $17.00/1k (5k free), Enterprise $20.00/1k (1k free); Text Search Pro $32/1k (5k free), Text Search Enterprise $35/1k (1k free); Place Details Essentials (IDs only) unlimited free — [Google pricing](https://developers.google.com/maps/billing-and-pricing/pricing)
- Requesting `currentOpeningHours`, `regularOpeningHours`, `regularSecondaryOpeningHours`, `priceLevel`, `priceRange`, `rating`, `websiteUri`, phone numbers etc. triggers the **Place Details Enterprise** SKU — [Place Details (New)](https://developers.google.com/maps/documentation/places/web-service/place-details); [Place Data Fields (New)](https://developers.google.com/maps/documentation/places/web-service/data-fields)
- No field for admission/ticket price exists in Places (priceLevel/priceRange are restaurant-style price bands) — [Place Data Fields (New)](https://developers.google.com/maps/documentation/places/web-service/data-fields)
- A Cloud billing account (credit card) is needed to enable Maps APIs; there is no hard spending cap by default, only budget alerts and per-API quotas — [Woosmap: is Google Maps API free](https://www.woosmap.com/blog/is-google-maps-api-free) (secondary; Google's pricing page does not restate the card requirement)

**Geoapify Places / Place Details**
- Free plan: 3,000 credits/day, 5 req/s, no credit card, never auto-charged; Places API costs 1 credit per 20 places returned — [Geoapify pricing](https://www.geoapify.com/pricing/); [Geoapify Places API](https://www.geoapify.com/places-api/)
- Place Details returns `opening_hours` (string) — data is OpenStreetMap-based — [Geoapify Place Details docs](https://apidocs.geoapify.com/docs/place-details/)

**HERE Geocoding & Search**
- Base Plan: 0–30,000 transactions/month free per service; Base Plan requires a credit card at sign-up but charges only above the free tier — [HERE pricing](https://developer.here.com/pricing); [HERE billing KB](https://docs.here.com/here-kb/docs/how-is-billing-for-a-developer-here-platform-account)
- The pricing page still advertises a "Limited plan – Free, no credit card", but a HERE KB article says the Limited Plan has been retired and cannot be assigned — conflicting; assume card required — [HERE pricing](https://developer.here.com/pricing); contradicted by [HERE KB](https://docs.here.com/here-kb/docs/can-i-switch-from-here-base-plan-to-here-limited-plan)
- (HERE Discover/Lookup responses carry an `openingHours` object where data exists — not re-verified in this run; see Gaps.)

**TomTom Search / POI Details**
- Freemium: 2,500 free non-tile requests per day, no credit card; over-limit returns HTTP 403 — [TomTom pricing](https://developer.tomtom.com/pricing)
- Place by ID supports `openingHours=nextSevenDays` (hours for the next week in POI local time); Nearby/POI search can also return opening hours — [TomTom Place by ID](https://developer.tomtom.com/search-api/search-api-documentation/place-id); [TomTom POI Search](https://developer.tomtom.com/search-api/documentation/search-service/points-of-interest-search)
- Separate POI Details API returns rating, price range, photos, reviews; Orbis version in Public Preview — [TomTom POI Details](https://docs.tomtom.com/poi-details-api/documentation/poi-details-api/poi-details)

**Foursquare Places API**
- Pro endpoints (Search, Details with default fields): official page says up to 10,000 free calls; a June 2026 blog and a table fragment indicate 500 free calls — conflicting — [Foursquare pricing](https://foursquare.com/pricing/); contradicted by [openplacesapi.com blog](https://openplacesapi.com/blog/fsq-os-places-free-place-search)
- `hours` is a **Premium** field (Search/Details become Premium when premium fields are requested; Premium from $18.75 CPM, reportedly no free allowance); the free open dataset (FSQ OS Places) lacks hours — [Foursquare pricing](https://foursquare.com/pricing/); [openplacesapi.com](https://openplacesapi.com/blog/fsq-os-places-free-place-search)

**Tripadvisor Content API**
- First 5,000 calls/month free; credit card required at sign-up; must follow display requirements (Tripadvisor logo/bubble ratings attribution) — [Tripadvisor Content API FAQ](https://tripadvisor-content-api.readme.io/reference/faq)
- Whether Location Details returns hours for attractions was not verified in this research (see Gaps); no admission-price field was found. A community MCP server wraps it: `pab1it0/tripadvisor-mcp` (location search, reviews, photos) — [Glama: tripadvisor-mcp](https://glama.ai/mcp/servers/pab1it0/tripadvisor-mcp)

### Inferences
- For hours, only **Google Places Enterprise** clearly beats OSM in coverage (Google is the de facto source museums update). 1,000 free Place Details Enterprise calls/month is enough for a student demo if results are cached per session, but caching beyond what Google ToS allows is restricted (Places content generally may not be stored beyond place_id; display with Google attribution). Requires a card on the billing account.
- TomTom is the best "no card" commercial option for hours; coverage of museum hours in Lisbon/Tallinn/Helsinki unverified.
- Geoapify adds nothing over Overpass for this use case except hosted convenience.
- No commercial POI API returns admission prices.

### Gaps
- No live coverage test of Google/TomTom/HERE for the three cities (no API keys in this research).
- Google Places ToS caching specifics not re-fetched this session.
- Whether TomTom's opening-hours data is included in Freemium requests not confirmed.

## 4. Ticket/admission prices: Tiqets, GetYourGuide, Viator, Musement, Headout; MCP servers

### Takeaway
Ticket-reseller APIs are the only structured source of museum/monument **prices**, but they return reseller product prices (often bundles/skip-the-line, with markup), not the official gate price, and coverage of small/free museums is absent. Viator Basic affiliate access is self-service and free; Tiqets is free but eligibility-reviewed; GetYourGuide requires partner approval. A community Viator MCP server exists.

### Cited Findings
- **Viator Partner API**: Basic Access is the default, "instant self-service access", "no prior approval or minimum requirements"; includes single-product data, product summaries by search criteria, single-product availability **and pricing**, review ratings/counts. Full access (bulk data, real-time availability, reviews/photos) needs approval/certification; Full + Booking applications are no longer accepted — [Viator levels of access](https://partnerresources.viator.com/travel-commerce/levels-of-access/); [Viator Partner API docs](https://docs.viator.com/partner-api/technical/)
- Community **viator-mcp** (GitHub chrischall/viator-mcp): read-only MCP connector to Viator Partner API; search tours, check availability; attraction tool returns name, destination, opening hours, review summary, product codes, URL; needs a Viator API key. Viator also lists an official remote MCP endpoint with OAuth — [LobeHub: chrischall viator-mcp](https://lobehub.com/mcp/chrischall-viator-mcp); [mcpservers.org: Viator](https://mcpservers.org/remote-mcp-servers/viator) (directory listings, not verified by running)
- **Tiqets Distributor API**: "available at no cost", but "all potential partners go through an eligibility review"; sign-up gives the affiliate portal plus Content and Availability APIs (OpenAPI). Since Nov 2025 affiliates can self-create Partner API tokens in the portal (Tools > API Access) after requesting access — [Tiqets API program](https://www.tiqets.com/en/partner-program/api-program/); [Tiqets Partner API blog](https://www.tiqets.com/partner-program/blog/tiqets-partner-api-affiliates/)
- Tiqets ToS: use only your own credentials, serve end customers, destroy non-public docs at termination — [Tiqets distributor API ToS](https://portals.tiqets.com/distributorapi/terms-of-service)
- **GetYourGuide Partner API**: OpenAPI spec is public on GitHub (Apache-2.0); auth via `X-ACCESS-TOKEN` "provided by GetYourGuide"; default rate limit 130 calls/min; must access in real time, no scraping/caching output. Access route conflicting: an MCP setup guide says key comes from the free partner program; a third-party directory says it requires a commercial agreement — [GitHub getyourguide/partner-api-spec](https://github.com/getyourguide/partner-api-spec); [GYG getting started wiki](https://github.com/getyourguide/partner-api-spec/wiki/Getting-started); [supergood.ai](https://supergood.ai/api-report-card/getyourguide)
- No standalone GetYourGuide MCP found; Apify actor `romy/travel-tools-mcp` exposes GYG/Klook/TripAdvisor search (paid Apify platform, scraping-based) — [Apify travel-tools-mcp](https://apify.com/romy/travel-tools-mcp.md)
- **Musement (TUI) and Headout**: no official public API-access information found in this research.

### Inferences
- For a student project, **Viator Basic** is the only ticket API realistically obtainable immediately with no approval; it gives "from" prices for products tied to attractions (e.g. Belém Tower tickets/tours). Present these as "tickets from €X via Viator", not as the official admission price.
- Affiliate APIs are designed for commercial resale; using them only to show prices without linking/booking may conflict with affiliate intent — check ToS before demoing.
- An MCP-based integration (viator-mcp) is possible with Google ADK's MCP toolset, but maturity is community-level.

### Gaps
- No live Viator query performed to check whether Tallinn/Helsinki museums (Kumu, Ateneum) appear as products.
- Musement/Headout access terms, and Smithery/mcp.so registry sweep for "attractions/opening hours" MCP servers, not completed.

## 5. Official city open data (Lisbon, Tallinn, Helsinki)

### Takeaway
Helsinki is the standout: the city's **Servicemap (palvelukartta) API** and **Hauki opening-hours API** return free, no-key, structured daily opening hours (incl. holiday exceptions) for city-run units such as Helsinki City Museum — this clearly beats web search for those venues. The old **MyHelsinki Open API is unreachable** (connection failed). Lisbon and Tallinn have no structured museum-hours/price API found.

### Cited Findings
- **Hauki** (City of Helsinki opening-hours service): `GET https://hauki.api.hel.fi/v1/resource/tprek:8663/opening_hours/?start_date=2026-11-17&end_date=2026-11-23` returned per-day JSON for Helsinki City Museum: Mon–Fri 11:00–19:00, Sat–Sun 11:00–17:00 with `resource_state:"open"` (live query, 2026-11-17, no API key) — [Hauki API](https://hauki.api.hel.fi/v1/)
- **Servicemap v2**: `https://api.hel.fi/servicemap/v2/unit/8663/?include=connections` returns an OPENING_HOURS connection with text hours plus exceptions (Independence Day 6 Dec 2026 closed, 24–25 Dec closed, 31 Dec 11–17, 1 Jan 2027 closed) (live query, 2026-11-17); search endpoint `/servicemap/v2/search/?q=ateneum&type=unit` finds units (Ateneum = unit 20444) — [Helsinki Servicemap unit 8663](https://palvelukartta.hel.fi/en/unit/8663); [Servicemap API](https://api.hel.fi/servicemap/v2/)
- State-run Ateneum (unit 20444) returned **no** connections/opening hours in Servicemap (live query) — coverage is mainly city-owned services
- **Linked Events** place `tprek:8663` returns name, multilingual description (which says admission is free) and info_url, but no structured hours/prices (live query) — [Linked Events API](https://api.hel.fi/linkedevents/v1/place/tprek:8663/)
- **MyHelsinki Open API** (`open-api.myhelsinki.fi/v2/places/`): connection failed / no HTTP response on 2026-11-17 (live query) — treat as discontinued. HRI still describes it as JSON of places/events/activities incl. opening hours (older page) — [HRI: Open data about sights and activities in Helsinki](https://hri.fi/en_gb/open-data-about-sights-and-activities-in-helsinki/)
- **Lisbon**: municipal CKAN portal (dadosabertos.cm-lisboa.pt) has an API and "Agenda Cultural de Lisboa" event dataset (agendalx.pt/api), but no museum hours/prices dataset found; CKAN `package_search` on dados.cm-lisboa.pt did not return JSON in this run; dados.gov.pt search "museus horários" = 0 results (live queries) — [Agenda Cultural de Lisboa dataset](https://dadosabertos.cm-lisboa.pt/pl/dataset/agenda-cultural-de-lisboa); [Lisboa Aberta](https://lisboaaberta.cm-lisboa.pt/)
- **Tallinn**: no Visit Tallinn API found; Visit Tallinn publishes holiday hours of Tallinn Card attractions as news articles only — [Visit Tallinn](https://visittallinn.ee/)

### Inferences
- Add a Helsinki-specific tool: Servicemap search -> unit id -> Hauki opening_hours for the trip dates. Free, no key, date-specific, includes holidays. Prices are not structured (sometimes in descriptions).
- For Lisbon/Tallinn, the best non-search path is OSM opening_hours + Wikidata P856 website fetch (or Google Places Enterprise if a card is acceptable).

### Gaps
- Hauki coverage of non-city venues (Suomenlinna, Ateneum, Kiasma) not systematically tested.
- Lisbon agendalx.pt API not tested; Estonian avaandmed.eesti.ee not searched.

## 6. Verdict: does each option beat Tavily web search for hours / prices?

### Takeaway
No single free source covers both hours and prices. Best combo for the planner: **OSM opening_hours (museums) + Helsinki Hauki/Servicemap + Wikidata P856 official-website fetch**, with web search kept as fallback for prices. Optional upgrades: Google Places Enterprise (hours, needs card) and Viator Basic (reseller "from" prices).

### Cited Findings
(Each row summarises evidence cited in sections 1–5.)

| Option | Hours | Prices | Free quota / card | Beats web search? |
|---|---|---|---|---|
| OSM `opening_hours`/`fee`/`charge` (Overpass, already integrated) | Museums 63–82% filled; sights/historic 0–15% | fee yes/no ~65% of museums; real price only Lisbon museums 27% | Free, no key, ODbL | **Yes for museum hours**; no for prices |
| `opening_hours_py` parser | Parses OSM syntax | – | Free (PyPI) | Enabler |
| Wikidata P3025/P2555 | ~0 for target cities | ~0 | Free | **No** (use only P856 website) |
| Wikidata P856 + fetch official site | Authoritative | Authoritative | Free | **Yes** — targeted fetch beats generic search |
| Helsinki Hauki + Servicemap API | Daily, holidays, structured (city venues) | Not structured | Free, no key | **Yes, Helsinki city venues** |
| MyHelsinki Open API | – | – | Unreachable 2026-11-17 | Discontinued |
| Linked Events (Helsinki) | No | No (free text only) | Free | No for this use |
| Lisbon open data (CKAN, Agenda LX) | Not found | Not found | Free | No |
| Visit Tallinn | No API | No API | – | No |
| Google Places (New) Enterprise | Best coverage | No admission price | 1,000/month free per Enterprise SKU; card needed | Yes for hours (if card ok) |
| Geoapify | OSM-derived | No | 3,000 credits/day, no card | No (same as OSM) |
| TomTom Search (Place by ID) | Yes, next 7 days | No | 2,500/day, no card | Possibly; coverage unverified |
| HERE Search | Some | No | 30k/month, card required (Limited plan retired) | Unverified |
| Foursquare | Premium field | No | Premium billed from 1st call | No |
| Tripadvisor Content API | Some | No | 5,000/month, card required, attribution rules | Marginal |
| Viator Partner API (Basic) | Attraction hours via community MCP | Reseller "from" prices | Free, self-service | **Yes for indicative ticket prices** |
| Tiqets API | – | Reseller prices | Free, eligibility review | Only if approved |
| GetYourGuide Partner API | – | Reseller prices | Token issued by GYG; no caching | Only if approved |
| Musement / Headout | ? | ? | No public info found | Unknown |

### Inferences
- Recommended order inside a "sight details" tool: (1) OSM tags via existing Overpass client (parse with `opening_hours_py`, flag non-standard strings); (2) if city == Helsinki, Servicemap search + Hauki for trip dates; (3) Wikidata P856 -> fetch official page for hours/prices; (4) Tavily only as last resort. This removes most Tavily calls for museum hours while prices still mostly come from official sites/web.
- Note in output that OSM/reseller prices are indicative and may be outdated (e.g. Kansallismuseo closed until 2027; Azulejo museum tagged "closed").

### Gaps
- No API-key-based live tests of Google, TomTom, HERE, Tripadvisor, Viator for the three cities.
- MCP registry sweep (Smithery, mcp.so, official modelcontextprotocol/servers list) for opening-hours-specific servers was not exhaustive; only Viator and Tripadvisor community servers found.
