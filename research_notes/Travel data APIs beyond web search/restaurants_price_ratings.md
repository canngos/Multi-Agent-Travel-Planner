# Restaurant data with price level and ratings for European cities: free and free-tier APIs, open data and MCP servers (status as of 17 Nov 2026)

Context: the planner (Python, Google ADK) already uses OSM Overpass (no price or ratings) and Wikivoyage "Eat" listings (budget tiers, though the Lisbon city page has none). Foursquare was tried: rating and price are Premium fields, and the free account got 429 "no API credits". Target test cities: Lisbon, Tallinn, Helsinki.

**Bottom line:**
- **Card on file, but free at student volume, with first-class price and rating fields:**
  - Google Places API (New), Text Search Enterprise SKU: 1,000 free calls per month, billing account required.
  - Google "Maps Experience agent tools" (formerly Maps Grounding Lite), a remote MCP server with 10,000 free requests per month. It also works with the no-billing Maps Demo Key, but that key is for prototyping only.
- **No card, open data that adds a real price tier:**
  - The community-maintained Michelin Guide CSV (MIT code, scraped data; ToS grey area). It has a `$`–`$$$$` price column for 42 Lisbon, 32 Tallinn and 29 Helsinki restaurants.
  - Wikivoyage district pages for Lisbon.
- **No longer viable without paying:**
  - TripAdvisor: the legacy Content API was sunset on 31 Aug 2026. Terra Discover gives only 1,000 lifetime free entities and forbids caching and most AI use.
  - Yelp: paid plans only, with a 30-day trial. No Estonia locale.
  - Foursquare: Premium fields are billed from the first call.
  - Zomato: API gone.
  - TheFork and OpenTable: partner-only.

---

## 1. Google Places API (New): price_level and rating fields, SKU, free caps after March 2025, billing and ToS

### Takeaway
`priceLevel`, `priceRange`, `rating` and `userRatingCount` all trigger the **Enterprise** SKU (`reviews` triggers Enterprise + Atmosphere). There is no free-of-billing tier: the free usage is 1,000 Enterprise calls per month per SKU. After that, Text Search and Nearby Search cost $35 per 1,000 and Place Details $20 per 1,000. A billing account (payment method) is required, except for the prototype-only Maps Demo Key. Caching of anything except place IDs is prohibited, and Google Maps attribution is mandatory.

### Cited Findings
- Billing is per field mask at the highest SKU touched: "You are then billed at the highest SKU applicable to your request … if you select fields in both the Essentials and the Pro SKUs, you are billed based on the Pro SKU." Text Search (New) SKUs are Essentials (IDs only), Pro, Enterprise and Enterprise + Atmosphere. Nearby Search (New) SKUs are Pro, Enterprise and Enterprise + Atmosphere. — [Google: Places usage and billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing)
- Field-to-SKU mapping:
  - **Enterprise** (Place Details and Text Search): `priceLevel`, `priceRange`, `rating`, `userRatingCount`, `regularOpeningHours`, `websiteUri`.
  - **Enterprise + Atmosphere**: `reviews`, `servesVegetarianFood`.
  - Source: [Google: Places data fields](https://developers.google.com/maps/documentation/places/web-service/data-fields)
- Free usage caps and first-tier prices per 1,000 calls (tier up to 100,000):

  | SKU | Free per month | First-tier price per 1,000 |
  |---|---|---|
  | Text Search Pro | 5,000 | $32 |
  | Text Search Enterprise | 1,000 | $35 |
  | Nearby Search Pro | 5,000 | $32 |
  | Nearby Search Enterprise | 1,000 | $35 |
  | Place Details Pro | 5,000 | $17 |
  | Place Details Enterprise | 1,000 | $20 |

  Source: [Google Maps Platform pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)
- The March 2025 change replaced the "USD $200 monthly recurring credit with a free monthly usage threshold for each Core Services SKU". SKUs are now grouped into Essentials, Pro and Enterprise categories. — [Google: March 2025 pricing changes](https://developers.google.com/maps/billing-and-pricing/march-2025)
- The legacy Places API (which returned `price_level` and `rating` in the old format) is listed as a legacy product as of 1 March 2025, replaced by Places API (New). Google advises "keep legacy APIs disabled on new projects". — [Google: Legacy products](https://developers.google.com/maps/legacy)
- Text Search (New) supports a `priceLevels` request filter, e.g. `["PRICE_LEVEL_INEXPENSIVE","PRICE_LEVEL_MODERATE"]`. "Places of non-supported types won't be included in the response if `priceLevels` is specified." `PRICE_LEVEL_FREE` appears only in responses, not as a filter. — [Google: Text Search (New)](https://developers.google.com/maps/documentation/places/web-service/text-search)
- Caching and ToS rules:
  - Don't "pre-fetch, index, store, or redistribute Google Maps Content … or copy and save business names, addresses, or user reviews".
  - Place IDs are exempt and may be stored indefinitely. Latitude and longitude may be cached for up to 30 consecutive calendar days.
  - Google Maps attribution (logo preferred) is required when Places content is shown off a Google map.
  - Source: [Google: Places API policies](https://developers.google.com/maps/documentation/places/web-service/policies)
- **Maps Demo Key:** "lets you start prototyping with Google Maps Platform without entering billing information".
  - It requires a personal @gmail.com account and is "not designed for production use".
  - It supports Places API (New) and Maps Grounding Lite.
  - "User-generated content, such as user-submitted photos and reviews, is not available through the Maps Demo Key."
  - "If you reach the daily limit, your usage is paused until the following day with no risk of charges."
  - Source: [Google: Maps Demo Key](https://developers.google.com/maps/demo-key)

### Inferences
- Getting price and rating means calling Text Search with an Enterprise field mask. With 1,000 free calls per month, a student planner doing about 1 text search per city per day-plan stays free, but the free cap is per SKU, not per field. Mixing Pro and Enterprise fields bills everything at Enterprise.
- Google's ban on storing names and reviews makes building an offline restaurant database from Google non-compliant. The planner should call the API live and store only place IDs.
- Coverage of Lisbon, Tallinn and Helsinki is not in doubt (Google Maps is global). Google returns `priceLevel` only for places that have it, and that share was not measured in this research.
- Google beats web search on structure: the rating, the `priceLevel` enum and the `priceLevels` filter are exactly what a budget-aware agent needs. The catch is the billing account.

### Gaps
- Could not confirm whether the Maps Demo Key returns `rating`, `userRatingCount` or `priceLevel`. Its documentation excludes "user-generated content such as photos and reviews" without saying whether aggregate ratings count as UGC, and it gives no numeric daily quota ("subject to change"). This needs a live test.
- Did not verify whether a new Google Cloud account still receives a sign-up trial credit (historically $300). Not researched.
- No measurement of the share of Lisbon, Tallinn and Helsinki restaurants that have a `priceLevel` in Google.

---

## 2. Google "Maps Experience agent tools" (formerly Maps Grounding Lite) and Gemini Grounding with Google Maps (MCP-native Google options)

### Takeaway
Google now runs an **official remote MCP server** at `https://mapstools.googleapis.com/mcp` with a `search_places` tool. It returns an AI-generated summary plus place IDs, coordinates and Maps links (not raw `rating` or `priceLevel` fields). It has 10,000 free Essentials requests per month, then $7 per 1,000. It needs a billing-enabled project or the no-billing Maps Demo Key. Gemini API "Grounding with Google Maps" is **not available on the Gemini free tier**. On the paid tier it includes 5,000 free queries per month, then $14 per 1,000.

### Cited Findings
- Tools: `search_places`, `search_along_route`, `lookup_weather`, `compute_routes`, `resolve_names`, `resolve_maps_urls`. MCP endpoint (Streamable HTTP): `https://mapstools.googleapis.com/mcp`. REST is also available, e.g. `https://mapstools.googleapis.com/v1:searchText`. — [Google: Maps Experience agent tools](https://developers.google.com/maps/ai/grounding-lite)
- Pricing:

  | SKU | Free per month | Price per 1,000 after that |
  |---|---|---|
  | Essentials | 10,000 requests | $7 |
  | Pro | 5,000 requests | $14 |

  - Pro options (`include_extended_details`, `page_size` above 5, and others) are off by default.
  - Essentials returns "place details for up to 5 places per search". Pro adds "extended place details (such as insights from reviews), up to 10 places per search".
  - Quota: 300 queries per minute for `search_places`.
  - Source: [Google: Maps Experience agent tools overview (md)](https://developers.google.com/maps/ai/maps-experience-agent-tools.md.txt)
- An older doc version and third parties cite 100 QPM and 1,000 queries per day per project, and say there was "no charge … while it is in the Experimental stage". This conflicts with the current page above, which has GA-style pricing. — [Google: Grounding Lite reference (older snapshot via search)](https://developers.google.com/maps/ai/grounding-lite/reference); contradicted by [current overview](https://developers.google.com/maps/ai/maps-experience-agent-tools.md.txt)
- `search_places` output:
  - `summary`: a natural-language summary with citations.
  - `places[]`: each entry has a place ID, lat/lng and `googleMapsLinks` (placeUrl, reviewsUrl, …).
  - `contentToken`: for free rendering with Places UI Kit.
  - "The structured `places` entries are the same for both SKUs."
  - Source: [Google: search_places](https://developers.google.com/maps/ai/maps-experience-agent-tools/search-places.md.txt)
- Setup and ToS:
  - Auth is an API key in the `X-Goog-Api-Key` header or OAuth. "Enable billing for the project". "For testing and prototyping, the Maps Demo Key is available at no cost without a billing account."
  - "you are responsible for ensuring that Google Maps Content is not cached by, stored by, or used to improve the LLM".
  - Don't use it with models that train on inputs.
  - Results must be attributed to Google Maps and linked.
  - Source: [Google: Maps Experience agent tools overview](https://developers.google.com/maps/ai/grounding-lite)
- Gemini API Grounding with Google Maps: the Free Tier column says "Not available". The Paid Tier gets "5,000 requests per month (free, shared across all Gemini 3 and newer models), then $14 / 1,000 search queries". Gemini 2.5 and older models are billed at $25 per 1,000 grounded prompts. — [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing); [Gemini API: Grounding with Google Maps](https://ai.google.dev/gemini-api/docs/maps-grounding)

### Inferences
- For an ADK agent, the Maps MCP server is the lowest-effort integration (ADK supports MCP toolsets). The response is a prose summary, so price and rating arrive only if the model's summary mentions them. That is unreliable for a deterministic budget filter.
- A practical pattern: call `search_places` (Essentials) to get place IDs, then Place Details Enterprise for the IDs that need `priceLevel` and `rating`.
- Gemini Maps grounding needs the paid Gemini tier, which is a billing account again. It is not a free-tier option.

### Gaps
- Not verified whether `search_places` summaries reliably include price level or star rating for European restaurants. This needs a live test with the Demo Key.
- The free-of-charge "Experimental" period's end date was not found. The current docs show pricing.

---

## 3. Yelp Fusion / Yelp Places API: any free tier in 2025–2026, and coverage in Portugal, Estonia, Finland

### Takeaway
**No free tier.** Yelp Places API (formerly Fusion) is paid: Base $229 per month, Enhanced $299, Premium $643. There is a one-off 30-day trial with 5,000 calls and a business email is required. Locales exist for Portugal (pt_PT) and Finland (fi_FI) but **not Estonia**. It does not beat web search for this project.

### Cited Findings
- Plans and trial:
  - Base: $229 per month + $5.91 per extra 1,000 calls (base attributes, no photos or review excerpts).
  - Enhanced: $299 per month + $6.57 per 1,000 (up to 3 photos and 3 review excerpts).
  - Premium: $643 per month + $14.13 per 1,000 (up to 7 excerpts, Review Highlights).
  - Trial: "Receive 5,000 free API calls during the 30-day trial period". A business email is required.
  - All plans include early access to the Yelp AI API at 100 calls per day.
  - Source: [Yelp: Places API product page](https://business.yelp.com/data/products/places-api/)
- The trial is reportedly "strictly for evaluation", and you may not publicly launch a product on it. This is a third-party summary. — [Supergood API Report Card: Yelp](https://supergood.ai/api-report-card/yelp)
- Older 2024 per-call pricing ($7.99 / $9.99 / $14.99 per 1,000) predates the current plans. — [AppDeveloperMagazine](https://appdevelopermagazine.com/yelp-fusion-api-outrageous-new-pricing/)
- The supported-locales list includes `pt_PT` (Portugal), `fi_FI` and `sv_FI` (Finland). No `et_EE` / Estonia entry was found. — [Yelp: Supported locales](https://docs.developer.yelp.com/docs/resources-supported-locales)

### Inferences
- A missing Estonia locale strongly suggests no or minimal Tallinn coverage. The Lisbon and Helsinki locales exist, but listing density there is likely far lower than Google or TripAdvisor. This is an inference, not measured.
- The trial cannot sustain a student project beyond 30 days, and the price after that is prohibitive. **Verdict: does not beat web search.**

### Gaps
- Whether a credit card is required to start the trial is not stated on Yelp's page.
- No restaurant counts for Yelp in Lisbon, Tallinn or Helsinki were obtained.

---

## 4. TripAdvisor Content API → Terra: free calls, credit card, fields, coverage, display and AI restrictions

### Takeaway
**The classic "5,000 free calls per month" Content API is discontinued.** Tripadvisor sunset legacy keys on **31 August 2026**. Its replacement, **Terra Discover**, is pay-as-you-go:
- the first **1,000 billable entities per account lifetime** are free (not monthly);
- after that, $0.015 to $0.009 per entity.

Terra returns exactly the fields wanted (rating, `price_level`, ranking, cuisine, hours, awards). However, its Master Terms **prohibit caching** (except location IDs), forbid re-sorting or combining content, and **forbid AI/LLM use** except internal, non-commercial RAG grounding tests. That makes it a poor fit for a multi-agent planner even before cost.

### Cited Findings
- Legacy FAQ (pre-sunset): "We offer the first 5000 API calls for free every month after you sign up. You do need a credit card to sign up as any overage will be charged." — [Tripadvisor Content API FAQ](https://tripadvisor-content-api.readme.io/reference/faq)
- Migration FAQ:
  - "The legacy Content API will be sunset and API keys deprecated on **August 31, 2026**."
  - "The previous 5,000-call free tier was a legacy Content API offering … Terra Discover offers a complimentary tier of up to 1000 calls to support initial testing and evaluation."
  - Terra Locations return "coordinates, phone, categories, opening hours, traveler ratings breakdown, rankings, attributes, awards, descriptions, price level, email, neighborhoods, and review summaries".
  - Old keys do not carry over; sign up at tripadvisor.com/developers.
  - Source: [Terra: Legacy Content API Migration FAQ](https://docs.terra.tripadvisor.com/docs/faq.md)
- Discover pricing:
  - "the **first 1,000 billable entities for your account are free** … granted once per account lifetime and does not repeat monthly".
  - Each location ID returned by search, nearby or details counts as one billable entity.
  - Tiers: $0.01500 per entity (1–1,000 per cycle), falling to $0.00900 (5,001+). 4xx and 5xx responses are not billed.
  - Source: [Terra: Discover Usage-based Pricing](https://docs.terra.tripadvisor.com/docs/usage-based-pricing.md)
- Limits:
  - Discover: 10 requests per second and 10,000 calls per day (rolling 24 h).
  - The search and nearby endpoints have a separate bucket: 1 request per second, burst of 5.
  - Source: [Terra: API Access & Rate Limits](https://docs.terra.tripadvisor.com/docs/rate-limits.md)
- Caching: "caching, copying, downloading, storing or indexing content is not permitted for any content, except that the Location ID attribute can be cached solely to improve the speed of your application." — [Terra: Caching Policy](https://docs.terra.tripadvisor.com/docs/caching-policy.md)
- Master Terms (Discover):
  - No AI/ML licence except "solely to perform Grounding on a large-language artificial intelligence model via retrieval-augmented generation … used solely for internal (e.g. not consumer or customer facing) non-commercial purposes". "Grounding outputs may not be used for commercial purposes".
  - Content must be displayed "as-is". Customers may not "combine any Licensed Content with any other content" or "modify or alter any display, sort or ranking order".
  - A Tripadvisor mark and link-back are required.
  - Pages must be non-indexable.
  - Source: [Terra: API Master Terms](https://docs.terra.tripadvisor.com/docs/api-master-terms)
- Legacy Location Details fields (also in Terra):
  - `rating` (1–5), `rating_image_url` (the owl bubble image must be shown), `num_reviews`, `price_level` ("not available for all POIs"; localised currency symbols).
  - `ranking_data` (ranking out of N in the destination), `cuisine[]`, `hours`, `subratings`, `awards`.
  - Parameters: `language` (including `pt_PT`) and `currency`.
  - Source: [Tripadvisor Content API: Location Details](https://tripadvisor-content-api.readme.io/reference/getlocationdetails)
- Tripadvisor's Terra "Developer MCP" at `https://docs.terra.tripadvisor.com/mcp` is aimed mainly at documentation search and code generation. It claims "Direct API access" with your key. — [Terra: Developer MCP](https://docs.terra.tripadvisor.com/docs/mcp.md)
- Secondary sources describe the legacy Content API as for consumer-facing (B2C) sites, with approval needed and dev keys capped at about 1,000 calls per day before launch review. — [Supergood: Tripadvisor](https://supergood.ai/api-report-card/tripadvisor); [socialcrawl.dev 2026 comparison](https://www.socialcrawl.dev/blog/best-tripadvisor-data-apis-2026)

### Inferences
- With 1,000 lifetime free entities, a nearby search returning 10 restaurants burns 10 entities, so the free allowance covers about 100 searches **total**. After that a card is charged.
- The ban on combining content with other content and on re-ranking conflicts with an agent that merges restaurants from several sources and ranks them by budget.
- The AI clause allows only internal, non-consumer-facing RAG testing. A student demo arguably fits "internal non-commercial", but a public demo would not. Flag as legally risky.
- **Verdict: does not beat web search for this project**, except as a short-lived experiment. Coverage of Lisbon, Tallinn and Helsinki is very likely strong (Tripadvisor is dominant in European tourism), but this was not measured.

### Gaps
- Not confirmed whether Terra Discover sign-up requires a credit card. It is pay-as-you-go with billing, so almost certainly yes, but no source states it explicitly.
- No numeric restaurant counts per city were retrieved from Tripadvisor.

---

## 5. Foursquare Places API (2025 platform): are rating and price truly premium-only, any free credits?

### Takeaway
Yes. `price` (1–4) and `rating` (0–10) are listed under **Places Premium** fields. Premium calls have **no free band**: $18.75 CPM from call 1. Only Pro (basic) fields have a small free band, and Foursquare's own page is inconsistent about its size (500 vs "up to 10,000" calls). This matches the 429 "no API credits" the project saw. The free FSQ OS Places open dataset contains Pro/open fields only, with no rating or price.

### Cited Findings
- The response-fields page splits fields into "Places Pro" ("all fields available via our Places Open Source data and extra fields about chain membership and location") and "Places Premium". The Premium group includes:
  - photos, tips, popularity;
  - `price`: "A numerical value (from 1 to 4) … 1 = Cheap … 4 = Very Expensive";
  - `rating`: "A numerical rating (from 0.0 to 10.0) … based on user votes, likes/dislikes, tips sentiment, and visit data";
  - `stats`.
  - Source: [Foursquare: Places Response Fields](https://docs.foursquare.com/fsq-developers-places/reference/response-fields)
- Pricing:
  - Pro endpoints: 0–500 calls $0.00 CPM, then $15 CPM.
  - Premium endpoints ("Search, Details, Place Match, and Geotagging Candidates when requested with premium fields"): 0–100,000 calls $18.75 CPM, with no free band.
  - The same page's call-to-action says "Enjoy up to 10,000 free calls on Pro endpoints", which is internally inconsistent.
  - Source: [Foursquare: Pricing](https://foursquare.com/pricing/)
- A third-party source claims direct sign-ups get "$200 in monthly free usage credits" (not on AWS Marketplace). Foursquare's terms reserve the right to modify or eliminate "Free Monthly Credits". The official pricing page does not mention $200. — [Foursquare Developer Master Terms](https://foursquare.com/legal/terms/enterprise-developermasterterms/); claim via [search summary of third-party pricing pages](https://pricingsaas.com/companies/foursquare)

### Inferences
- The observed 429 "no API credits" is consistent with Premium fields needing paid credits. Any "$200 credit" (if it still exists) likely requires a payment method on file. **Verdict: not free for price or rating. Does not beat web search unless the project adds a card and pays about $0.019 per call.**

### Gaps
- Could not confirm the current existence or conditions of the $200 monthly credit, or whether a credit card unlocks it.

---

## 6. HERE, TomTom, Geoapify, Mapbox Search: any price level or rating fields?

### Takeaway
- **Geoapify:** no ratings or price (OSM-based). It does have a no-card free tier.
- **HERE Geocoding & Search v7:** no rating or price fields found.
- **TomTom POI Details API:** documents rating and price range, but it is restricted or preview access and licence-constrained.
- **Mapbox Search Box API:** returns `metadata.rating` and supports `price_levels` / `minimum_rating` filters "if available" (US, Canada and Europe). 25,000 to 50,000 free requests per month.
- **Mapbox Places API (Public Preview):** `attributes.price_level` for food and drink in "parts of Western Europe", with a 1,000 records per month preview quota.
- Mapbox is the only non-Google mapping vendor with plausible price and rating data, but European coverage of these attributes is unverified.

### Cited Findings
- Geoapify Places is "based on OpenStreetMap (OSM) and other open data sources". The free plan is 3,000 credits per day with no credit card. Restaurants use `categories=catering.restaurant`. — [Geoapify pricing](https://geoapify.com/pricing); [Geoapify Places API docs](https://apidocs.geoapify.com/docs/places/)
- TomTom POI Details API "provides additional information about the POIs including: rating, price range, photo IDs, and user reviews". The rating bar image ID is mandatory, and displaying the rating in a different style breaches the licence. Some pages mark the API "restricted to private preview" or "public preview". One Orbis page says v1 "will be discontinued as of the 24th of June 2022" (an inconsistent date). — [TomTom: POI Details](https://developer.tomtom.com/poi-details-api/documentation/poi-details-api/poi-details); [TomTom Orbis POI Details](https://developer.tomtom.com/search-api/documentation/tomtom-orbis-maps/poi-categories-service/points-of-interest-details)
- Mapbox Search Box API:
  - The `/retrieve` example response includes `metadata` with `phone`, `website`, `rating`, `wheelchair_accessible`, `popularity`.
  - `/forward` supports `price_levels` "if price data is available" and `minimum_rating` "if rating data is available".
  - "The Search Box API supports the United States, Canada, and Europe."
  - "all data returned by the Search Box API endpoints is only available for temporary use."
  - Source: [Mapbox: Search Box API](https://docs.mapbox.com/api/search/search-box/)
- Mapbox SDK metadata also exposes `priceLevel` ("$" to "$$$$"), `rating` (1–5) and `reviewCount`. — [Mapbox iOS SearchResultMetadata](https://docs.mapbox.com/ios/search/api/core/2.34.1/Structs/SearchResultMetadata.html)
- Mapbox pricing:
  - Search Box Requests (standard): up to 25,000 per month free, then $1.70 per 1,000.
  - Introductory-preview variant: up to 50,000 free.
  - Sessions: 2,500 free (standard) or 500 free (introductory preview).
  - Source: [Mapbox pricing](https://www.mapbox.com/pricing)
- Mapbox Places API (Details):
  - Status: "Public Preview". `attributes` include "cuisine, price level", with examples like `"price_level": "Expensive"`.
  - "During Public Preview, coverage includes select categories, including food and drink … across the US, Canada, and parts of Western Europe."
  - Default quota: 1,000 records per month. "Data returned by the Places API is for temporary display and use only."
  - Source: [Mapbox: Places API](https://docs.mapbox.com/api/search/places/)

### Inferences
- Mapbox Search Box is the most promising no-Google commercial option. Whether `rating` and `price_levels` are populated for Lisbon, Tallinn and Helsinki is unknown. Estonia and Finland are not "Western Europe", so Places API preview coverage there is doubtful.
- Mapbox accounts have historically required a credit card at sign-up. **Unverified (see Gaps).**
- Geoapify and HERE add nothing beyond what Overpass already gives for price and rating. Geoapify is only a convenience wrapper over OSM.

### Gaps
- HERE: no source retrieved that explicitly confirms the absence of rating or price fields. HERE's docs are JavaScript-rendered and could not be fetched. The negative finding is based on the absence of such fields in what was found.
- Mapbox credit-card requirement and actual European rating/price fill rate are not verified. A live test is needed.
- TomTom's current self-serve availability and free tier for POI Details are unconfirmed.

---

## 7. Open data and other providers: OSM price tags, Michelin data, TheFork, Zomato, OpenTable

### Takeaway
- **OSM:** practically no price tags. `price_range` is used 6 times worldwide. Useful budget proxies are `amenity=fast_food`, `brand=*`, `diet:*`, and `award:michelin` (290 uses).
- **Michelin Guide:** no official API. The community **michelin-my-maps** CSV (updated November 2026) has `Price` ($–$$$$) and `Award` for about 20,000 restaurants, including the three target cities. It is scraped data, so there is a ToS grey area.
- **TheFork:** partner-only B2B API, gated by an agreement.
- **Zomato:** public API gone (developer portal 404 since about 2021).
- **OpenTable:** partner-only (by inference; see Gaps).

### Cited Findings
- taginfo (queried 17 Nov 2026 via API):
  - Price-like keys: `price_range` 6, `price:range` 0, `price` 562, `lunch:menu:price` 49, `budget` 0, `price:level` 0.
  - Rating-like keys: `rating` 37,512 (mostly non-restaurant), `stars` 62,546 (mostly hotels).
  - Michelin and diet keys: `award:michelin` 290, `award:michelin:bib_gourmand` 19, `diet:vegetarian` 120,098, `diet:vegan` 68,986.
  - Tag combinations with `amenity=restaurant`: `cuisine` 792,128, `opening_hours` 386,302, `website` 330,481, `outdoor_seating` 182,315, `brand` 69,185, `diet:vegetarian` 65,634.
  - Source: [taginfo key stats API](https://taginfo.openstreetmap.org/api/4/key/stats?key=price_range); [taginfo keys search](https://taginfo.openstreetmap.org/search?q=price); [taginfo amenity=restaurant combinations](https://taginfo.openstreetmap.org/tags/amenity=restaurant#combinations)
- michelin-my-maps:
  - A Go Colly spider that "curates a list of awesome restaurants from the Michelin Guide". MIT licence. The CSV includes address, price, cuisine, lon/lat, phone, website, award and green star.
  - The last commit to `data/michelin_my_maps.csv` was on 3 Nov 2026.
  - README disclaimer: "only used for research purposes".
  - Source: [GitHub: ngshiheng/michelin-my-maps](https://github.com/ngshiheng/michelin-my-maps); [raw CSV](https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv)
- Counts computed from that CSV (19,985 rows; `Location` field):

  | City | Restaurants | Awards | Price tiers |
  |---|---|---|---|
  | Lisbon | 42 | 3× two stars, 12× one star, 5 Bib Gourmand, 22 Selected | 13× `$$`, 11× `$$$`, 18× `$$$$` |
  | Tallinn | 32 | 1× two stars, 1× one star, 8 Bib Gourmand, 22 Selected | 1× `$`, 25× `$$`, 3× `$$$`, 3× `$$$$` |
  | Helsinki | 29 | 2× two stars, 4× one star, 3 Bib Gourmand, 20 Selected | 10× `$$`, 11× `$$$`, 8× `$$$$` |

  Source: [raw CSV](https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv) (own computation)
- French and Luxembourg governments publish annual Michelin-starred lists as open data, e.g. 2024 starred restaurants on data.gouv.fr. These do not cover Portugal, Estonia or Finland. — [data.gouv.fr: Restaurants étoilés Michelin 2024](https://www.data.gouv.fr/en/datasets/969521c8-0377-43a1-8806-4e264846c51e-uuid/)
- TheFork B2B API: access only after partnership approval (email integrations@thefork.com), with Auth0 client-credentials auth. Scope is reservations, availability and reviews for partner restaurants. — [DM Champ: TheFork integration](https://help.dmchamp.com/integrations/thefork/); [Packagist: omnifood/thefork](https://packagist.org/packages/omnifood/thefork)
- Zomato: the developers.zomato.com/api page returned 404 (2021 report). A scraper vendor notes the v2 developer API is deprecated. No official shutdown notice was found. — [StackOverflow archive: not able to access zomato developers page](https://kiwix.lema.org/stackoverflow.com_en_all_2023-11/questions/66469553/not-able-to-access-zomato-developers-page); [Apify Zomato scraper docs](https://apify.com/crawlerbros/zomato-scraper.md)
- Wikivoyage check: the Lisbon city page has an "Eat" section with no tier subsections. The district pages do have tiers: Lisbon/Alfama has Budget / Mid-range / Splurge, and Lisbon/Bairro_Alto has Budget, Mid-range, Splurge plus neighbourhood subsections. Lisbon/Baixa returned an Eat section with no tier subsections. — [Wikivoyage API: Lisbon/Alfama sections](https://en.wikivoyage.org/w/api.php?action=parse&page=Lisbon/Alfama&prop=sections&format=json)

### Inferences
- OSM cannot supply price level. Heuristic budget tiers can be derived from it:
  - `amenity=fast_food`, `brand=*` chains, or `cuisine=kebab|pizza|burger` → budget;
  - `award:michelin` → splurge.
- **The Michelin CSV is the single best no-key, no-card source of real price tiers for the target cities.** It is biased to mid and high end (few `$` entries) and is a scrape of guide.michelin.com, whose ToS likely prohibits scraping (not verified). Use it only for research, attribute "MICHELIN Guide", and do not redistribute.
- For Lisbon, the planner should fall back from the city page to Wikivoyage district pages (e.g. `Lisbon/Alfama`, `Lisbon/Bairro_Alto`) to get Budget / Mid-range / Splurge tiers.

### Gaps
- OpenTable: no current source retrieved. Its public affiliate/directory API is believed to be partner-only, but this is unverified in this research.
- guide.michelin.com terms of use regarding scraping were not retrieved.
- Wikivoyage Tallinn and Helsinki tier headings could not be checked (Wikimedia API returned HTTP 429 during research).

---

## 8. MCP servers for restaurants (official and community): backends and keys

### Takeaway
- **Google:** Google's official remote **Maps Experience agent tools MCP** (`mapstools.googleapis.com/mcp`) is the only maintained, first-party option with a usable free allowance.
- **Yelp:** the official **Yelp MCP** (`Yelp/yelp-mcp`) wraps the paid Yelp Fusion AI API.
- **TripAdvisor:** community servers wrap the legacy Content API (pab1it0, dead since 31 Aug 2026) or Terra (@chrischall).
- **Reference server:** the old `@modelcontextprotocol/server-google-maps` is archived and unmaintained.

### Cited Findings
- Google Maps Experience agent tools MCP: endpoint `https://mapstools.googleapis.com/mcp`. Needs an API key with billing, or the Maps Demo Key. 10,000 free Essentials requests per month. (See section 2.) It is listed on Glama as a remote server. — [Google docs](https://developers.google.com/maps/ai/grounding-lite); [Glama: com.googleapis.mapstools](https://glama.ai/mcp/remote-servers/com.googleapis.mapstools/google-maps)
- `@modelcontextprotocol/server-google-maps` (v0.6.2) was moved to `modelcontextprotocol/servers-archived`. That repo is "archived and no longer maintained", the package is marked "deprecated", and there are "no security guarantees". — [Glama mirror of servers-archived README](https://glama.ai/mcp/servers/@modelcontextprotocol/servers-archived/blob/9be4674d1ddf8c469e6461a27a337eeb65f76c2e/README.md); [Docker Hub mcp/google-maps](https://hub.docker.com/r/mcp/google-maps)
- Yelp official MCP: `github.com/Yelp/yelp-mcp` (Python, Apache-2.0). It exposes one conversational business-agent tool and needs `YELP_API_KEY` for Yelp Fusion AI (key creation starts a free trial). Last push was around July 2025. Community alternatives such as `server-yelp-fusionai` wrap Fusion v3. — [GitHub: Yelp/yelp-mcp](https://github.com/Yelp/yelp-mcp); [Glama: Yelp Fusion AI MCP](https://glama.ai/mcp/servers/Yelp/yelp-mcp)
- TripAdvisor MCP servers:
  - `pab1it0/tripadvisor-mcp` uses `TRIPADVISOR_API_KEY` against the legacy Content API (search, details, reviews, photos, nearby).
  - `@chrischall/tripadvisor-mcp` targets Terra and says legacy keys return 403 after the 2026-08-31 sunset. It reportedly can also read public listing pages without a key (ToS questionable).
  - Source: [pab1it0/tripadvisor-mcp README](https://cdn.jsdelivr.net/gh/pab1it0/tripadvisor-mcp@main/README.md); [socket.dev: @chrischall/tripadvisor-mcp](https://socket.dev/npm/package/@chrischall/tripadvisor-mcp)
- Tripadvisor's own Terra "Developer MCP" at `https://docs.terra.tripadvisor.com/mcp` is aimed mainly at docs and code assistance. — [Terra: Developer MCP](https://docs.terra.tripadvisor.com/docs/mcp.md)

### Inferences
- Every restaurant MCP server inherits its backend's cost and ToS. None creates a free path to ratings or price. The Google MCP is the only one with a meaningful free monthly allowance and an official maintainer.
- Scraping-based MCP servers (e.g. Apify, HasData Yelp connectors on Glama) exist but are paid and/or ToS-risky. Not recommended.

### Gaps
- Smithery and mcp.so were not individually surveyed. Coverage relied on Glama and GitHub, which mirror most entries.

---

## 9. Coverage checks (Lisbon, Tallinn, Helsinki) and verdict per candidate

### Takeaway
For the three target cities:
- Google (Places API / Maps MCP) is the only option that combines full coverage, structured `priceLevel` and `rating`, and a free monthly allowance. It requires a billing account, or the Demo Key for prototyping only.
- With no card at all, the best option is to keep OSM and Wikivoyage, add Lisbon district pages, and add the Michelin CSV for verified price tiers in the mid and high range.

### Cited Findings
- Coverage measured or confirmed:
  - **Michelin CSV:** Lisbon 42, Tallinn 32, Helsinki 29 restaurants with price tiers. — [raw CSV](https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv)
  - **Yelp locales:** Portugal and Finland yes, Estonia not listed. — [Yelp supported locales](https://docs.developer.yelp.com/docs/resources-supported-locales)
  - **Mapbox Places preview:** "parts of Western Europe" only. — [Mapbox Places API](https://docs.mapbox.com/api/search/places/)
  - **Wikivoyage Lisbon:** tiers exist on district pages (Alfama, Bairro Alto). — [Wikivoyage API](https://en.wikivoyage.org/w/api.php?action=parse&page=Lisbon/Alfama&prop=sections&format=json)

Verdict table (W = beats generic web search for structured price and rating; CC = credit card or billing on file):

| Option | Price field | Rating field | Free allowance | CC? | Status | Verdict |
|---|---|---|---|---|---|---|
| Google Places API (New), Text Search Enterprise | `priceLevel`, `priceRange` | `rating`, `userRatingCount` | 1,000/month (Enterprise), then $35/1k | Yes (Demo Key: no, prototype only) | GA | **W** (best if a card is acceptable) |
| Google Maps Experience agent tools (MCP) | in prose summary only | in prose summary only | 10,000/month Essentials | Yes, or Demo Key | GA/new | W for discovery, weak for filtering |
| Gemini Grounding with Google Maps | in prose | in prose | Not on Gemini free tier; 5,000/month on paid tier | Yes | GA | Only if already on paid Gemini |
| Tripadvisor Terra Discover | `price_level` | `rating`, ranking | 1,000 entities **lifetime** | Yes (pay-as-you-go) | Legacy Content API **discontinued 31 Aug 2026** | Not viable (cost, no-cache, AI clause) |
| Yelp Places API | `price` | `rating` | 30-day trial, 5,000 calls | Unclear; paid after | Paid only | Not viable; no Estonia |
| Foursquare Places | `price` 1–4 (Premium) | `rating` 0–10 (Premium) | None for Premium | Credits needed | GA | Not viable free |
| Mapbox Search Box | `price_levels` filter (if available) | `metadata.rating` (if available) | 25k–50k requests/month | Likely (unverified) | GA | Possible; EU fill rate untested |
| Mapbox Places (Details) | `attributes.price_level` | not documented | 1,000 records/month (preview) | Likely | Public Preview | Experimental only |
| TomTom POI Details | price range | rating | Unknown | Unknown | Restricted/preview | Not practical |
| HERE / Geoapify | none | none | Geoapify 3,000/day, no card | No (Geoapify) | GA | No gain over Overpass |
| OSM tags | `price_range` ~6 uses | none | Free | No | – | Proxies only (`fast_food`, `brand`, `award:michelin`) |
| Michelin CSV (community scrape) | `$`–`$$$$` | Award (stars, Bib) | Free download | No | Updated Nov 2026 | **W for upscale/Bib**, research-only ToS caveat |
| Wikivoyage district pages | Budget / Mid / Splurge | none | Free | No | – | Keep; add Lisbon districts |
| TheFork B2B | – | reviews | Partner only | Contract | Gated | Not available |
| Zomato API | – | – | – | – | **Discontinued** (portal 404) | Dead |

### Inferences
- Recommended stack for a no-card student project:
  1. Wikivoyage tiers (city page, then district pages);
  2. the Michelin CSV for verified price and award;
  3. OSM-derived budget heuristics;
  4. Tavily only as a fallback for ratings.
- If a billing account is acceptable, add one Google Places Text Search Enterprise call per city and category. The field mask is `places.id,places.displayName,places.priceLevel,places.priceRange,places.rating,places.userRatingCount`, and 1,000 free calls per month is ample. Set a GCP budget alert and a per-day quota cap.

### Gaps
- No live API calls were made to Google, Tripadvisor, Yelp or Mapbox, so the share of restaurants in each city with populated price and rating fields remains unmeasured.
- An Overpass restaurant count for each city was attempted but failed (no parsable response).
