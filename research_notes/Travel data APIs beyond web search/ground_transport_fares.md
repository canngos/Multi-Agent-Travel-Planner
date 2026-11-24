# Ground-transport fare data (train / bus / ferry, Europe) — options to replace Tavily web search

Research date: 2026-11-17. Besides web research, I sent live JSON-RPC calls to the two keyless MCP endpoints (Virail, Ferryhopper), the Transitous `/api/v4/plan` endpoint, and the CP / HSL / Estonian GTFS download URLs. Those results are marked **[live test 2026-11-17]**; for those the source link is the endpoint itself.

## Ferries: do Tallink Silja, Viking Line or Eckerö Line have an API, and does an aggregator (Ferryhopper, Direct Ferries, Omio, Distribusion…) cover them?

### Takeaway
None of the three Helsinki–Tallinn operators has a public or self-serve API. The only partner channel I found is a partner email address. Ferryhopper's official MCP server is free, needs no key, and returns real prices for Mediterranean routes. It knows Helsinki→Tallinn as a connection but returned **0 itineraries** for every Baltic date I tried, even though the same route can be booked on ferryhopper.com. So for Helsinki–Tallinn ferry prices it does **not** beat web search today. For Greek, Italian or Spanish ferries it clearly does.

### Cited Findings
- **Tallink Silja:** no developer API found. Its contacts page lists a travel-partner email, partner@tallinksilja.com, and business customers book through the call centre, agencies, the app or the website — [Tallink contacts](https://tallink.com/good-to-know/contacts)
- **Viking Line:** no API or developer portal found. Its distribution is through partners: it joined the FerryScan meta-search, and its Baltic services are connected to FlixBus's network through a partnership — [FerryScan: Welcoming Viking Line](https://ferryscan.com/info/welcoming-viking-line); [Viking Line on Omio](https://www.omio.co.uk/providers/ferries/viking-line). Careful: a "Viking has no public API" result refers to Viking *Cruises*, a different company — [supergood.ai](https://supergood.ai/api-report-card/viking)
- **Eckerö Line:** no API found. It is sold on Ferryhopper — [Ferryhopper Eckerö Line](https://ferryhopper.com/en/ferry-operators/eckero-line)
- **Ferryhopper website covers Helsinki–Tallinn:** the route page sells Eckerö Line, Viking Line and Tallink Silja Line and has "Find tickets" buttons. Indicative prices on the page: Helsinki→Tallinn from about €11, Tallinn→Helsinki from about €10.50. Its December 2026 fares were Viking Line from €11.00, Tallink Silja from €14.40, Eckerö from €21.50. The page intro says "starting around €17", which is inconsistent with those figures — [Ferryhopper Helsinki–Tallinn](https://www.ferryhopper.com/en/ferry-routes/direct/helsinki-tallinn)
- **Ferryhopper MCP server (official):** launched 10 December 2025 — [Ferryhopper press release](https://www.ferryhopper.com/en/press-releases/ferryhopper-launches-ai-ready-mcp-server-to-power-the-future-of-ferry-travel)
  - Remote endpoint `https://mcp.ferryhopper.com/mcp`. Tools: `search_ferry_trips` (1–5 legs), `trip_details` (accommodation tiers with prices), `get_ports`, `get_direct_connections_for_ports`, `get_disruptions`, and the deprecated `search_trips`. It returns "indicative prices" and a booking hand-off link to ferryhopper.com — [fh-mcp docs](https://ferryhopper.github.io/fh-mcp/)
  - The partner page claims 35 countries, 220+ operators and 5,000+ routes. It invites companies to "get in touch" but does not state any key or affiliate requirement, rate limits or ToS — [partners.ferryhopper.com/mcp](https://partners.ferryhopper.com/mcp). Glama lists it as "No Auth" — [Glama listing](https://glama.ai/mcp/servers/@FerryhopperOrg/Ferryhopper-MCP-Server)
  - **[live test 2026-11-17]** `initialize` and `tools/call` worked with no key and no session header. The server is stateless and returns streamable-HTTP SSE — [mcp.ferryhopper.com/mcp](https://mcp.ferryhopper.com/mcp)
  - **[live test]** Control route Piraeus→Mykonos on 2026-12-10 returned a Blue Star Ferries sailing at 07:30Z with `expectedPrice.totalPriceInCents` of 5300 (deck) and 5800 (numbered seat). The response `_meta` contains `affiliateId: "mcpint"` — [mcp.ferryhopper.com/mcp](https://mcp.ferryhopper.com/mcp)
  - **[live test]** `get_direct_connections_for_ports("Helsinki")` returned HELSINKI (ALL PORTS) → Travemünde, Mariehamn, Stockholm Värtahamnen, Tallinn (TAL) and Stockholm Stadsgården — [mcp.ferryhopper.com/mcp](https://mcp.ferryhopper.com/mcp)
  - **[live test]** `search_ferry_trips` Helsinki→Tallinn returned "Found 0 itineraries" for 2026-11-25, 2026-12-10 and 2027-01-15. Helsinki→Stockholm on 2026-12-10 also returned 0. `get_ports("Helsinki")` returned an alphabetical port list instead of a Helsinki match — [mcp.ferryhopper.com/mcp](https://mcp.ferryhopper.com/mcp)
- **Third-party Ferryhopper MCP wrapper:** MCPBundles hosts one that requires OAuth — [MCPBundles](https://tunnel.mcpbundles.com/skills/ferryhopper-mcp)
- **Distribusion:** a B2B ground-transport booking API (1,000+ providers). A demo API key or inventory overview must be requested by emailing partner@distribusion.com, so this is a business channel — [Distribusion docs](https://docs.distribusion.com/dt/api/getting-started/intro)
- **Omio:** API access is case by case and needs "a decent volume of traffic". Applicants are asked for traffic sources and monthly uniques. This comes from an undated Travelpayouts webinar Q&A — [Travelpayouts blog](https://www.travelpayouts.com/blog/qa-from-the-webinar-with-omio/amp/). Uber is Omio's largest B2B API partner — [TechCrunch](https://techcrunch.com/?p=2364840)
- **Direct Ferries / AFerry / Bookaway:** my searches turned up no public affiliate-API documentation for any of them (see Gaps).

### Inferences
- The Ferryhopper MCP's Baltic gap probably means the Baltic inventory on ferryhopper.com comes from a supplier that is not exposed through the MCP search, or the port resolver fails for Nordic names. Either way, the MCP cannot be relied on for Tallink, Viking or Eckerö today.
- For Helsinki–Tallinn ferry prices, the realistic options are:
  1. keep web search (Tavily) but scope it to operator or Ferryhopper pages; or
  2. use a small static price table (about €11–€40 one-way foot passenger, varying by operator, date and season), clearly labelled "indicative", plus booking deep links.
- A ToS-safe deep link such as `ferryhopper.com/en/ferry-routes/direct/helsinki-tallinn` can serve as the "book here" URL.
- The Ferryhopper MCP is still worth wiring in: it costs nothing, works over HTTP from Python (plain JSON-RPC POST, or an ADK `McpToolset` with streamable HTTP), and gives structured prices for Mediterranean legs, such as Lisbon-area itineraries that continue to islands.

### Gaps
- I could not confirm whether the Baltic gap in the Ferryhopper MCP is permanent or a resolver bug. Trying exact port names such as "HELSINKI (ALL PORTS)" or codes was not tested, and the schema says codes are not accepted.
- I found no published ToS or rate limits for the Ferryhopper MCP.
- Direct Ferries, AFerry and Bookaway affiliate or API programs could not be verified from primary sources in this session.

## FlixBus: official partner/affiliate API, and unofficial endpoints (legality, stability)?

### Takeaway
The official FlixBus API is partner-only: you apply by email with company, domains and monthly visits, and the affiliate side runs through AWIN. Unofficial access through `global.api.flixbus.com` and the `public-transport/flix` JS client does return prices without a key, but the client's own README says to ask FlixBus for permission before production use. The cleanest free option I found is **Virail's keyless MCP**, which returned live FlixBus prices for Helsinki→Tallinn, though it was unreliable on other routes.

### Cited Findings
- **Official affiliate program:** FlixBus partners with AWIN. The page offers an "advanced API integration for FlixBus and Greyhound"; to apply you email company information, domains and average monthly visits — [FlixBus affiliate partners (UK)](https://www.flixbus.co.uk/company/partners/affiliate-partners); [global partners page](https://global.flixbus.com/company/partners)
- **Developer portal:** a third-party API index says a partner API exists at developer.api.flixbus.com (search, availability, booking), that it needs a Microsoft Entra ID sign-in, and that credentials are issued only under a distribution-partner agreement. I could not confirm this on FlixBus's own site — [apis.io FlixBus](https://apis.io/providers/flixbus/)
- **Unofficial client `public-transport/flix`:**
  - "Inofficial", using "endpoints by FlixMobility". `journeys()` returns a `price` object (`amount`, `currency`, shop `url`), and methods accept an `apiKey` that defaults to a built-in key.
  - 33 stars, 114 commits, 2 open issues and 5 open PRs. I could not see the last-commit date.
  - The README says "Ask them for permission" before production use — [GitHub public-transport/flix](https://github.com/public-transport/flix)
- **Paid scrapers:** Apify sells FlixBus scrapers covering schedules and fares in 40+ countries. These are third-party and their ToS standing is unclear — [Apify FlixBus scraper](https://apify.com/jungle_synthesizer/flixbus-multi-country-scraper?fpr=data)
- **[live test 2026-11-17] Virail MCP, Helsinki→Tallinn:**
  - On 2026-12-10 it returned 5 options, all FlixBus "bus" products at €40.98, including Helsinki Kamppi 14:55 → Tallinn Bus Station 19:15 and Helsinki Harbour Terminal 2 16:30 → 19:15.
  - Booking URLs are AWIN affiliate deep links into shop.global.flixbus.com.
  - On 2026-12-12 it returned 4 FlixBus options at €40.98 — [virail.ai/mcp](https://virail.ai/mcp)

### Inferences
- FlixBus Helsinki→Tallinn is a bus+ferry product. It departs from Harbour Terminal 2, which is the terminal Ferryhopper says Tallink and Eckerö use. At about €41 it costs much more than a ferry-only foot-passenger ticket (about €11–€21), so it must not be shown as "the ferry price".
- For a student project the ToS-safe ways to get FlixBus prices are Virail's MCP (Virail is an AWIN affiliate and calls FlixBus legitimately) or deep links. Calling `global.api.flixbus.com` directly works technically but has no granted permission.

### Gaps
- I found no FlixBus ToS clause quoted in sources that explicitly forbids API scraping.
- I did not check the stability history of `global.api.flixbus.com`, such as issues about key rotation or blocking.

## Rail: VR (Finland), CP (Portugal), Deutsche Bahn, Trainline / Omio / Rail Europe / Rome2Rio / Kiwi, GTFS fares

### Takeaway
No national rail operator in scope (VR, CP) has a fare API. Digitraffic and CP's GTFS carry schedules only. DB fares can only be reached through unofficial clients (db-vendo-client, db-prices), which warn about "haphazard blocking" and say permission is needed. Trainline and Omio APIs are business-only. Rome2Rio's API does not give live prices. Kiwi's MCP covers flights only. GTFS fare files exist for Helsinki HSL local zones, not for intercity rail, and Transitous returned no fare data for Lisbon→Porto.

### Cited Findings
- **VR (Finland):**
  - No fare API found. The travel-agency page offers only website booking for agents and "collaboration agreements" (contacts businesstravel@vr.fi, agent.service@vr.fi) — [VR for travel agencies](https://www.vr.fi/en/for-travel-agencies)
  - Long-distance prices are dynamic: they depend on journey length, booking time and demand — [VR news 2023](https://uusi.vr.fi/en/news/2023/03/31/the-price-range-of-long-distance-train-tickets-is-expanding)
  - Commuter fares are fixed per route — [VR commuter single ticket](https://www.vr.fi/en/train-tickets/single-ticket-commuter-traffic)
  - A third-party Apify VR scraper says its Digitraffic fallback "does not include pricing" (secondhand) — [Apify VR scraper](https://apify.com/studio-amba/vr-scraper)
- **Digitransit (Finland, OpenTripPlanner GraphQL):**
  - Routers: HSL, Waltti, "Finland and Estonia" (`/routing/v2/finland/gtfs/v1`) and Varsinais-Suomi. Registration and an API key are required — [Digitransit Routing API](https://www.digitransit.fi/en/developers/apis/1-routing-api/)
  - The docs page does not mention fares.
- **HSL GTFS has fares** **[live test]:** `hsl.zip` (about 86 MB) contains `fare_attributes.txt` and `fare_rules.txt`, with zone fares such as AB €3.30, ABC €4.50, ABCD €5.00 and ABCDE €6.10 — [HSL GTFS](https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip)
- **CP (Portugal):**
  - Official GTFS feed of 183 routes, valid to 12 Dec 2026 — [Mobility Database mdb-2057](https://mobilitydatabase.org/feeds/gtfs/mdb-2057)
  - **[live test]** The `publico.cp.pt/gtfs/gtfs.zip` file contains only agency, calendar, calendar_dates, routes, stops, stop_times and trips. There are **no fare files** — [CP GTFS](https://publico.cp.pt/gtfs/gtfs.zip)
  - Fares are published as PDFs, e.g. the Lisbon urban fares in force since 1 Jan 2026 — [CP urban fares PDF](https://cp.pt/info/documents/d/cp/precos-comboios-urbanos-lisboa); [CP Alfa Pendular prices](https://cp.pt/info/prices-alfa-pendular)
  - The unofficial `comboios` npm client is archived, inactive and asks for CP permission — [npm comboios](https://npmjs.com/package/comboios)
- **Estonia:**
  - The national Public Transport Register publishes GTFS daily, covering bus, tram, trolleybus, rail and ferry — [Univ. of Tartu IMO](https://imo.ut.ee/en/public-transport-register-open-data/)
  - The download moved to peatus.ee/content/teenusest and is now split into 25 files — [OSM forum](https://community.openstreetmap.org/t/estonian-gtfs-data-at-new-location/140823)
  - **[live test]** The old URL `peatus.ee/gtfs/gtfs.zip` now returns an HTML page titled "Rakendus on suletud" ("application is closed").
- **Transitous (MOTIS)** **[live test]:** `api.transitous.org/api/v4/plan` Lisbon→Porto for 2026-12-10 returned 7 itineraries (CP regional rail, FlixBus-eu coach, metro legs). No itinerary had `fareTransfers`, and no leg had `fareTransferIndex`. So no prices came back for this route — [Transitous API](https://api.transitous.org/api/v4/plan?fromPlace=38.7139,-9.1394&toPlace=41.1496,-8.6109&time=2026-12-10T08:00:00Z)
- **GTFS Fares v2:** the spec defines fare products, rider categories and route-, zone- and time-based fares — [gtfs.org Fares v2](https://gtfs.org/extensions/fares-v2/). Coverage depends on what each feed publishes; CP publishes none.
- **Deutsche Bahn:**
  - `db-vendo-client` lists `journeys()` / `refreshJourney()` "including tickets and bestprice option". Tickets are available on the `db`, `dbnav` and `dbweb` profiles, not on `dbris`, which needs an API key.
  - Its README warns DB APIs are "subject to haphazard blocking" and "very unreliable". Limits: about 60 req/min for `db` and `dbnav`, "aggressive blocking" on `dbweb`, and the `db` backend "may be shut off soon". "Strictly speaking, permission is necessary." 209 stars, ISC license — [GitHub db-vendo-client](https://github.com/public-transport/db-vendo-client)
  - `db-prices` (behind bahn.guru) uses an "inofficial" Sparpreise endpoint and asks for DB permission — [GitHub db-prices](https://github.com/juliuste/db-prices); [bahn.guru](https://www.Github.com/juliuste/bahn.guru)
  - I found no evidence that the official DB API Marketplace offers fares (third-party description only).
- **Trainline:** no public developer portal or sandbox. Access needs a commercial agreement, an integration of roughly 12 weeks, and accreditation — [AltexSoft rail booking APIs](https://www.altexsoft.com/blog/business/rail-booking-apis/); [Trainline Partner Solutions](https://tps.thetrainline.com/); [TPS integrations overview](https://tpssupport.trainline.com/support/solutions/articles/52000116538)
- **Rome2Rio:** its v1.4 API documentation says it "does not provide live transit prices or airfares through this API" and recommends Skyscanner for this — [Rome2Rio API 1.4 docs](https://cms.rome2rio.com/documentation/1-4/search). Free API access was announced in 2013 — [TheNextWeb 2013](https://thenextweb.com/news/rome2rio-grants-free-access-to-its-api-to-bring-multi-modal-visual-search-to-travel-sites-everywhere). I could not confirm it is still offered today.
- **Kiwi.com:**
  - Official MCP at `https://mcp.kiwi.com`: no auth, streamable HTTP, **flight search only**, returns a few dozen "best" options and booking links. No source mentions trains or buses — [Smithery Kiwi](https://smithery.ai/server/kiwi); [Alpic case study](https://alpic.ai/case-studies/kiwi)
  - Tequila: since May 2024 new B2B partnerships are by invitation only — [Kiwi media 2024](https://media.kiwi.com/articles-and-interviews/better-for-business-kiwi-com-takes-a-new-approach-to-partnerships/)
- **[live test] Virail MCP for rail:**
  - Lisbon→Porto (2026-12-10 and -11), Lisboa→Porto with `tld=pt`, Porto→Lisbon (2026-12-15), Lisbon→Faro, Helsinki→Turku, Helsinki→Tampere and Munich→Berlin all returned **HTTP 200 with an empty SSE stream after about 10 s**.
  - Only Helsinki→Tallinn (FlixBus) returned data, twice — [virail.ai/mcp](https://virail.ai/mcp)

### Inferences
- Neither Finland nor Portugal has a legal, free, programmatic source for intercity rail fares. Options:
  1. **Static fare heuristics.** For CP, the Alfa Pendular and Intercidades price tables are published, so Lisbon–Porto could be a small hard-coded lookup with a "check cp.pt" caveat. For VR, prices are dynamic, so a range is the most a lookup can honestly give.
  2. **Scoped web search** on vr.fi or cp.pt.
  3. **Virail MCP** when it returns data.
- HSL zone fares from GTFS are useful for local transport in Helsinki but not for intercity trips.
- For cross-border European rail (e.g. DB), db-vendo-client works technically, but blocking and the permission caveat make it a poor fit for a demo.

### Gaps
- I did not find whether Omio, Rail Europe, Trainpal or Saveatrain offer self-serve affiliate APIs with fares. All appear to be affiliate-link or B2B only, but this is unverified in primary sources for this session.
- I did not inspect the Finland-national (Fintraffic) or the split Estonian GTFS for fare files. HSL was the only Finnish feed checked.
- I could not confirm whether Digitransit's GraphQL schema exposes fare products (HSL `fareProducts` / legacy `fares`) for the Finland router. That needs schema introspection with a registered key.
- The cause of the empty Virail responses is unknown. It may be a server-side timeout around 10 s or a coverage gap.

## MCP servers (official or community) that return European ground-transport prices — and the overall verdict vs web search

### Takeaway
Four MCP servers give real ground-transport prices with no or free credentials. Only Virail is pan-European and multimodal (train, bus, ferry, flight), but it is flaky. Ferryhopper is official and robust but Mediterranean-only in practice. The SBB and Italian servers are single-country. None reliably covers Finland, Estonia or Portugal rail, or the Baltic ferry operators. Recommended pattern: **Virail MCP → Ferryhopper MCP → scoped web search fallback**, with every price labelled "indicative" and paired with a booking link.

### Cited Findings
- **Virail MCP (official, by viraildev):**
  - Endpoint `https://virail.ai/mcp`, "No install, no API key". MIT-licensed repo with 0 stars and 5 commits.
  - Tools: `search_transport(departure, arrival, date, adults, tld, locale, currency)` and `resolve_place`. Results include price, carriers, modes, times and booking link.
  - Covers trains, buses, flights, ferries and carpooling "across Europe and beyond" — [GitHub viraildev/virail-mcp](https://github.com/viraildev/virail-mcp); [Glama Virail](https://glama.ai/mcp/servers/viraildev/virail-mcp)
  - **[live test]** 2 of 9 searches returned data; the rest returned empty streams (see the rail section).
- **Ferryhopper MCP (official):** keyless, structured prices in cents. It works on Greek routes but returned no Baltic itineraries (see the ferry section) — [fh-mcp](https://ferryhopper.github.io/fh-mcp/)
- **Kiwi.com MCP (official):** flights only, so it is not useful for ground transport — [Smithery Kiwi](https://smithery.ai/server/kiwi)
- **Swiss servers:**
  - `swiss-transport-mcp` has a `get_ticket_price` tool (OJP Fare). It needs a free opentransportdata.swiss key and covers Switzerland only. Its maintenance is rated "Slowing" — [Glama swiss-transport-mcp](https://glama.ai/mcp/servers/wkb0xn5g33)
  - `sbb-mcp` (ticket prices and purchase links, Switzerland) — [mcpservers.org sbb-mcp](https://mcpservers.org/servers/github-com-fabsbags-swisstrip-web-tree-main-packages-sbb-mcp)
- **Italian travel search MCP** (Alexxanddr/italian-travel-search): Trenitalia, Italo and FlixBus prices for Italy. Not affiliated with the operators — [Glama](https://glama.ai/mcp/servers/frc364oiu2)
- **`trvl`:** claims 22 train, bus and ferry providers across Europe, but the providers are unspecified — [Glama listing via search](https://glama.ai/mcp/servers/mc4eodkstr)

### Inferences
Verdict per candidate, compared with Tavily web search for this planner (origin Helsinki, demo trips to Tallinn and Lisbon):

| Candidate | Prices? | FI | EE | PT | Free / no card | ToS-safe | Beats web search? |
|---|---|---|---|---|---|---|---|
| Virail MCP | Yes (live) | Partial: FlixBus HEL–TLL only worked | Same | Failed in test | Yes, keyless | Yes (official) | **Yes, as primary tool with fallback** |
| Ferryhopper MCP | Yes (indicative, cents) | Connection known, 0 results | 0 results | n/a (mainland) | Yes, keyless | Yes (official) | **Yes for Med ferries; no for Baltic** |
| FlixBus official API | Yes | Yes | Yes | Yes | No (partner/AWIN application) | Yes | Not reachable for students |
| FlixBus unofficial (`public-transport/flix`) | Yes | Yes | Yes | Yes | Yes | No (permission needed) | Technically yes; legally risky |
| db-vendo-client / db-prices | Yes (DB) | No | No | No | Yes | No (permission needed, blocking) | No for this demo |
| VR / CP / Digitraffic | No | — | — | — | — | — | No |
| HSL GTFS fares | Yes (local zones) | Helsinki only | No | No | Yes | Yes | Only for local transport |
| Transitous / MOTIS | No fares observed | — | — | — | Yes | Yes | No (schedules only) |
| Trainline / Omio / Distribusion | Yes | Yes | Yes | Yes | No (B2B, traffic thresholds) | Yes | Not reachable |
| Rome2Rio API | No live prices | — | — | — | Unclear | — | No |
| Kiwi MCP / Tequila | Flights only / invite-only | — | — | — | — | — | No (ground) |
| Tallink / Viking / Eckerö | No API | — | — | — | — | — | No |

- Integration note: both keyless MCPs speak streamable HTTP with SSE responses and need no session ID, as observed. They can be used from Google ADK via `McpToolset` with streamable-HTTP connection params, or with a plain `httpx` POST of `{"jsonrpc":"2.0","method":"tools/call",...}` with `Accept: application/json, text/event-stream`.
- A timeout of at least 15 s, plus a fallback to web search, is advisable given Virail's empty responses.

### Gaps
- I found no MCP server that specifically covers VR, CP, Elron (Estonia) or the Baltic ferry operators with prices.
- I did not verify the provider list or authentication of `trvl`.
- Registries Smithery, mcp.so and Glama were only partially browsed through search results, so a smaller community server could have been missed.
