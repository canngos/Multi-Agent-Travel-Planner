# Programmatic sources for traditional dishes of a city/country (replacing Tavily web search)

All "live test" findings below come from my own HTTP calls on 2026-11-17 (Python urllib / curl, descriptive User-Agent, no API keys). Scripts were throwaway, kept outside the project. Country QIDs: Portugal Q45, Estonia Q191, Finland Q33. Cuisine QIDs (found via `wbsearchentities`): Portuguese cuisine **Q180817**, Estonian cuisine **Q1069518**, Finnish cuisine **Q40606**; dish **Q746549**; national dish **Q1391582**; food **Q2095**; property **P2012 = "cuisine"** ("type of food served by a restaurant or restaurant chain or national food culture").

## 1. Wikidata SPARQL: how dishes are modelled, a tested query, result counts/noise, rate limits and UA policy

### Takeaway
Wikidata works, needs no key, and returns English labels, descriptions and images for most items. But dish modelling is inconsistent. A naive "instance of dish (Q746549) + P495 country" query finds only 2 dishes for Estonia, 10 for Portugal and 15 for Finland. A broader union query (P495 country, OR P2012/P361 cuisine, AND instance-or-subclass of food Q2095, minus restaurants), ranked by sitelink count, gives a usable "top dishes" list: 194 rows for Portugal, 26 for Estonia, 117 for Finland. It still needs post-filtering for drinks, brands and generic items, and coverage for Estonia is thin. The official WDQS endpoint worked but was slow (24–40 s, against a 60 s limit) and was rate-limited to 1 request/min during an outage earlier the same day. The QLever mirror ran the same query in 0.4–10 s.

### Cited Findings
- **How famous dishes are actually modelled (live test, via QLever).** pastel de nata (Q1421401): P31 = "type of food or dish", P495 = Portugal. Karelian pasty (Q598113): P31 = "type of food or dish", P495 = Russia and Finland, with P2012 set but not to Q40606. Caldo verde (Q770977), bifana (Q9657134), mämmi (Q1630666), mulgipuder (Q12370530) and kama (Q134213) have **no P31 at all**; they carry P495 only, plus P279 subclass links. Bacalhau (Q10989778): P31 = "fare", P361 = Q614394. So "instance of dish" misses the most iconic dishes. Caldo verde's P495 lists Q148 (China) next to Portugal, an apparent data error. — [QLever Wikidata SPARQL endpoint (live test)](https://qlever.dev/api/wikidata)
- **Naive counts (live test, Wikidata CirrusSearch `haswbstatement`):**
  - `P2012=<cuisine>`: Portugal 40, Estonia 6, Finland 18.
  - `P495=<country> + P31=Q746549`: Portugal 10, Estonia 2, Finland 15.
  - `P495=<country>` alone (any entity type): Portugal 7,430, Estonia 3,988, Finland 14,555, so P495 alone is useless without a type filter.
  — [Wikidata Action API, list=search (live test)](https://www.wikidata.org/w/api.php?action=query&list=search&srsearch=haswbstatement:P2012=Q1069518)
- **First query version** (P2012 cuisine ∪ P361 part-of cuisine ∪ [P495 country AND P31/P279* dish Q746549]): Portugal 77 items, Estonia 10, Finland 42.
  - Noise: P2012 is also used on **restaurants**, e.g. "A Licorista – restaurant in Lisbon", "Adega – Portuguese restaurant in San Jose, California", "The Hoito – restaurant in Ontario". Generic and foreign dishes also leak in: gazpacho, Tocino de cielo ("Spanish egg dessert"), "Salame turco PAT (traditional Italian bakery product)".
  - The Estonia result of 10 included kvass, glogg, aspic and Smörgåstårta, but **missed verivorst, kama and rosolje**.
  — [QLever Wikidata endpoint (live test)](https://qlever.dev/api/wikidata)
- **Improved query (the recommended one)**, tested on both endpoints:
  ```sparql
  SELECT ?dish (SAMPLE(?label) AS ?l) (SAMPLE(?desc) AS ?d) (SAMPLE(?img) AS ?im) (MAX(?sl) AS ?links) WHERE {
    { ?dish wdt:P495 wd:Q191 } UNION { ?dish wdt:P2012 wd:Q1069518 } UNION { ?dish wdt:P361 wd:Q1069518 }
    ?dish (wdt:P31|wdt:P279)/wdt:P279* wd:Q2095 .          # any food, as instance OR subclass
    MINUS { ?dish wdt:P31/wdt:P279* wd:Q11707 }             # drop restaurants
    ?dish rdfs:label ?label . FILTER(lang(?label)="en")
    OPTIONAL { ?dish schema:description ?desc . FILTER(lang(?desc)="en") }
    OPTIONAL { ?dish wdt:P18 ?img }
    OPTIONAL { ?dish wikibase:sitelinks ?sl }
  } GROUP BY ?dish ORDER BY DESC(?links)
  ```
  — [QLever (live test)](https://qlever.dev/api/wikidata); [WDQS (live test)](https://query.wikidata.org/sparql)
- **Results of the improved query (live test):**
  - **Portugal:** 194 items; 183 with an English description, 123 with an image.
    - Top by sitelinks: gazpacho, marmalade, Port Wine, sangria, chorizo, Mediterranean diet, Madeira wine, **pastel de nata**, **feijoada**, quince cheese, **Francesinha**, **Caldo verde**, cocido, São Jorge cheese, galão, Serra da Estrela cheese, **ovos moles**, fios de ovos, **Bacalhau à Brás**, **pastéis de bacalhau**, Malasada, Alheira, **Caldeirada**, Bolo de mel, **Açorda**, bica, ginjinha, cabidela, **carne de porco à alentejana**, **Tripas à moda do Porto**.
  - **Estonia:** 26 items; 25 with a description, 17 with an image.
    - kvass, aspic, cabbage roll, **kama**, glogg, Tarhun (soda), **Vana Tallinn** (liqueur), Smörgåstårta, egg butter, **Sepik**, **Mulgikapsad**, **mulgipuder**, sulze, Viru Valge (vodka), **kiluvõileib**, handsa, Dynami:t (energy drink), **sõir**, Crafter's, **Tallinn Spiced Sprats**, Kelluke, H.F.Puls, Blue Diamond Vodka, Stön, Saku Originaal, Frikadellisupp.
    - Still **missing verivorst** (Estonia's de facto national dish according to Wikivoyage) and rosolje.
  - **Finland:** 117 items; 112 with a description, 80 with an image.
    - Top: rice pudding, cabbage roll, kama, **kalakukko**, **Karelian pasty**, glogg, semla, **mämmi**, salty licorice, **Runeberg's torte**, Finlandia Vodka, **Leipäjuusto**, Smörgåstårta, sahti, egg butter, **Lohikeitto**, **mustamakkara**, kringle, **sautéed reindeer (poronkäristys)**, Rosolli, Porkkanalaatikko, Karelian hot pot, ruisreikäleipä, joulutorttu, Porilainen, Pulla, lihapiirakka, plus brands (Battery energy drink, Koff, Karjala beer).

  — [QLever (live test)](https://qlever.dev/api/wikidata)
- **WDQS vs QLever speed and identical results (live test).** WDQS returned the same rows: Estonia 26 rows in 24.1 s, Portugal 194 rows in 40.1 s. QLever: Portugal 3.6 s, Estonia 10.6 s, Finland 8.0 s; the simpler first query took 0.4–0.8 s. — [WDQS](https://query.wikidata.org/sparql); [QLever](https://qlever.dev/api/wikidata)
- **WDQS outage throttling (live test, 15:30 UTC).** The first WDQS call returned `HTTP 429: Aggressively rate-limiting to 1 req / min - this rule was created during active wdqs outage`. About 15 minutes later WDQS returned 200 again. — [WDQS (live test)](https://query.wikidata.org/sparql)
- **WDQS documented limits:**
  - 60 s query runtime.
  - Each client (User-Agent + IP) gets 60 s of processing time per 60 s window.
  - 30 error queries per minute.
  - 5 parallel queries per IP.
  - Over-limit clients get HTTP 429 with `Retry-After`; clients that keep sending after a 429 may be temporarily banned; clients not following the User-Agent policy "may be blocked completely".
  - Limits are "subject to change".
  — [WDQS User Manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual)
- **WDQS backend migration.** "In 2026-2027, the Wikidata Query Service is migrating to a new backend" with new v2 endpoint URLs, and existing queries may need revising. Blazegraph is no longer actively developed. — [WDQS User Manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual)
- **Wikimedia Action/REST API rate limits ("new in 2026", "subject to experimentation and change"):**
  - Requests identifiable only by IP: **10 req/min**.
  - Unauthenticated bots with a compliant User-Agent: **200 req/min**.
  - Logged-in new users: 200 req/min.
  - Clients should keep concurrency at 3 or fewer and honour `Retry-After` (else wait at least 5 s).
  - The User-Agent should be "meaningful … includes contact information", e.g. `CoolBot/0.0 (https://example.org/coolbot/; coolbot@example.org) generic-library/0.0`.
  - The page does not cover WDQS.
  — [Wikimedia APIs/Rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits)
- **I hit these limits myself (live test).** Wikidata and Wikipedia API calls with a User-Agent that had **no contact info** got `429 Too Many Requests` (`x-envoy-ratelimited: true`, `retry-after: 7`–`30`) after a handful of calls, even with 1.5–3 s sleeps. The response body points to the rate-limit page and bot-traffic@wikimedia.org. — [Wikidata Action API (live test)](https://www.wikidata.org/w/api.php)

### Inferences
- A good recipe is: query by country and cuisine, rank by `wikibase:sitelinks` as a popularity proxy, then filter out:
  - beverages (P31/P279* beverage, Q40050) and brands (Q431289);
  - generic, multi-country items with very high sitelink counts (marmalade, Mediterranean diet).
  Then let the LLM pick 5–8 items. That gives the planner traceable names with QIDs, descriptions and Commons images.
- Wikidata is country-level only. P495 essentially never points to Lisbon or Tallinn, so city-level "what to eat in Lisbon" has to fall back to country dishes or to regional descriptions (e.g. "typical fish from Porto").
- For an ADK tool, use QLever (fast) or WDQS with a contact-bearing User-Agent, response caching and a fallback. The planner already uses the MediaWiki API, so it must also send a compliant User-Agent with contact info (a project URL works) to stay in the 200 req/min tier rather than the 10 req/min tier.

### Gaps
- I did not find terms of use or rate limits for the public QLever endpoint (qlever.dev, University of Freiburg). Treat it as a best-effort academic service.
- I did not test filtering beverages and brands out of the query, so post-filter counts are unknown.
- I did not find the QID behind "type of food or dish", the P31 value used on pastel de nata and Karelian pasty.
- Wikidata data is CC0 (no attribution needed) according to general knowledge, but I did not fetch a page this session to cite it.

## 2. Wikipedia "<Country> cuisine" / "List of … dishes" pages and Wikivoyage "Eat" sections: parseable via the MediaWiki API? Do they list typical dishes?

### Takeaway
Wikipedia's "List of Portuguese dishes" is the cleanest structured source for Portugal. Section 1 alone has 270 bullet items of wikilinks (`* [[Bacalhau à Brás]]`), easy to regex out of the wikitext. Estonia and Finland have **no "List of … dishes" page**; their "<Country> cuisine" articles are prose with section headings, which still parse well because dish names are wikilinked or italicised. Wikivoyage **country** "Eat" sections are short and traveller-focused, and name the right dishes as italic terms. Wikivoyage **city** "Eat" sections (Lisbon, Tallinn, Helsinki) list neighbourhoods and restaurants, not dishes.

### Cited Findings
- **Wikipedia page structure (live test, MediaWiki `action=parse`):**
  - **"List of Portuguese dishes":** 10 sections (Portuguese dishes, Breads, Cheeses, Desserts and sweets, Sausages and hams, Soups and stews, Beverages, Alcoholic beverages, …), 786 article links. Section 1 is 8,543 characters of wikitext with **270 bullet lines and no wikitable**, e.g. `* [[Açorda de bacalhau]]`, `* [[Amêijoas à Bulhão Pato]]`, `* [[Duck rice|Arroz de pato]] - A Portuguese variant of duck rice.`
  - **"Portuguese cuisine":** 19 sections (Fish and seafood, Meat, Charcuterie, Cheese, Pastries and sweets, …).
  - **"Estonian cuisine":** 12 sections (Cold table, Soups, Main course, Desserts, Drinks, Seasons, Winter and Christmas, …).
  - **"Finnish cuisine":** 32 sections, including "Examples of Finnish dishes", "Typical Finnish dishes", "Holiday specialties" and regional sections (Lapland, Kainuu, Karelia, Savonia, Ostrobothnia and Åland).
  — [English Wikipedia API (live test)](https://en.wikipedia.org/w/api.php?action=parse&page=List_of_Portuguese_dishes&prop=sections)
- **No list pages for Estonia or Finland (live test).** "List of Finnish dishes" and "List of Estonian dishes" return `missingtitle`. — [English Wikipedia API (live test)](https://en.wikipedia.org/w/api.php)
- **"Estonian cuisine", "Main course" section (live test):** prose with wikilinked, italic dish names.
  - Black bread (*leib*, *rukkileib*).
  - "*[[Mulgikapsad]]* (sauerkraut with pork and pearl barley) … can be considered to be an Estonian national food".
  - "*[[Mulgipuder]]* is the only Estonian national food that is in the UNESCO List of the Intangible Cultural Heritage".

  — [Wikipedia: Estonian cuisine](https://en.wikipedia.org/wiki/Estonian_cuisine)
- **Wikipedia categories (live test, `list=categorymembers`):**
  - "Category:Portuguese cuisine": 79 pages (Açorda, Bacalhau, Bolo-rei, Cabidela, Cataplana, Chanfana, …), but also noise such as "Jean Anderson (cookbook author)" and "Atlantic diet".
  - "Category:Estonian cuisine": 26 pages (Blood sausage, Kama (food), Karelian pasty, Kringle, Mulgikapsad, Mulgipuder, Sepik, Pea soup, …).
  - "Category:Finnish cuisine": 83 pages.
  - "Category:Portuguese/Estonian/Finnish dishes": 0 members (they don't exist in that form).

  — [English Wikipedia API (live test)](https://en.wikipedia.org/w/api.php?action=query&list=categorymembers&cmtitle=Category:Estonian_cuisine)
- **Wikivoyage "Portugal#Eat" (section 44, 6,818 chars; live test):**
  - Italic dish terms: Caldo verde, Bacalhau à Brás, Sardinhas assadas, Cozido à portuguesa, bitoque, Carne de Porco à Alentejana, tiras de choco frito, pastel de nata / Pastéis de Belém, bolo de arroz, castanhas.
  - Also many single-word ingredient names (peixe, linguado, robalo, amêijoas, perceves, …).
  - Subsections: Drink, Port Wine, Vinho Verde, Moscatel, Liqueur, Café.

  — [Wikivoyage API (live test)](https://en.wikivoyage.org/w/api.php?action=parse&page=Portugal&prop=wikitext&section=44)
- **Wikivoyage "Estonia#Eat" (2,029 chars; live test):**
  - "The closest thing to a national dish is ''verivorst'' pig's blood sausage … always accompanied by lingonberry jam and often served with ''mulgikapsad''".
  - Also *hapukoor*, *kartulisalat*, *kohuke*, the Kalev chocolate brand, and the lunch-special terms *päevapakkumine* / *päevapraad* (€5–12).

  — [Wikivoyage: Estonia](https://en.wikivoyage.org/wiki/Estonia#Eat)
- **Wikivoyage "Finland#Eat" (15,576 chars; live test):** subsections Fish and meat, Milk products, Bread, Desserts, Places to eat, Dietary restrictions. 72 italic terms, including kalakukko, muikku, silakka, poro, ruisleipä, reikäleipä, rieska, kiisseli, salmiakki, lihapiirakka. Many are Finnish/Swedish word pairs (lohi/lax). — [Wikivoyage: Finland](https://en.wikivoyage.org/wiki/Finland#Eat)
- **City pages (live test):**
  - **Lisbon#Eat** (2,748 chars): bold terms are neighbourhoods only (Bairro Alto, Chiado, Alfama, Baixa, Belem, …).
  - **Tallinn#Eat** (22,799 chars): restaurant listings by budget and area.
  - **Helsinki#Eat** (3,048 chars): mentions lunch, pizza, kebab, Unicafe, fresh seafood.

  — [Wikivoyage API (live test)](https://en.wikivoyage.org/w/api.php?action=parse&page=Lisbon&prop=sections)

### Inferences
- Simplest robust pipeline: fetch the Wikivoyage **country** "Eat" section through the existing MediaWiki client (find the section index with `prop=sections`, then `prop=wikitext&section=N`, or plain text via the TextExtracts API). Hand the text to the LLM to extract 5–8 dishes. This beats Tavily on reliability and attribution, and the planner already speaks this API.
- Pair it with Wikidata (section 1) or the Wikipedia list or category to supply canonical names, descriptions and images.
- Licence: Wikipedia and Wikivoyage text is CC BY-SA. The planner should attribute ("from Wikivoyage/Wikipedia, CC BY-SA") when it shows extracted text. I did not fetch the licence page this session.

### Gaps
- I did not test the TextExtracts (`prop=extracts`) or Parsoid HTML outputs, which would avoid wikitext regex.
- I did not check other-language Wikipedias (pt/et/fi), which likely have richer dish lists.

## 3. TheMealDB: limits, coverage for Portugal/Estonia/Finland, can the free key be used in apps?

### Takeaway
TheMealDB is a recipe database, not a "traditional dishes" registry. It has 8 Portuguese recipes, some authentic and some anglicised (piri-piri chicken and slaw). Estonian and Finnish appear in its area list but return **zero** meals. The free key "1" is allowed for development and education only; publishing to an app store needs a paid supporter subscription. It does not beat web search for this use case.

### Cited Findings
- **Area list has grown (live test, 2026-11-17).** `list.php?a=list` now returns about 200 demonym-style areas (Afghan … Zimbabwean), including "Estonian", "Finnish" and "Portuguese". — [TheMealDB list.php?a=list (live test)](https://www.themealdb.com/api/json/v1/1/list.php?a=list)
- **Portuguese coverage (live test).** `filter.php?a=Portuguese` returned 8 meals: Fish fofos, Grilled Portuguese sardines, Piri-piri chicken and slaw, Portuguese barbecued pork (Febras assadas), Portuguese custard tarts, Portuguese fish stew (Caldeirada de peixe), Portuguese prego with green piri-piri, Spring onion and prawn empanadas. — [TheMealDB filter.php?a=Portuguese (live test)](https://www.themealdb.com/api/json/v1/1/filter.php?a=Portuguese)
- **No Estonian or Finnish meals (live test).** `filter.php?a=Estonian` and `filter.php?a=Finnish` returned `{"meals": null}`. — [TheMealDB filter.php?a=Estonian (live test)](https://www.themealdb.com/api/json/v1/1/filter.php?a=Estonian)
- **Free key terms:**
  - Test key "1" may be used "during development of your app or for educational use".
  - Releasing on an app store requires becoming a supporter (premium); the price is not stated on the API page, only a PayPal sign-up.
  - Non-premium listing is capped at 100 items; premium adds multi-ingredient filter, random selection, latest, and full-DB listing.
  - Endpoints: search.php?s=, search.php?f=, lookup.php?i=, random.php, categories.php, list.php, filter.php?i=/c=/a=.

  — [TheMealDB API page](https://www.themealdb.com/api.php)
- **Terms of use:**
  - "You cannot publish apps to an appstore unless you are a paid subscriber."
  - "You can scrape, copy and modify any content returned from the API, as long as you use the official end points."
  - Artwork: "must mention us as the source of the data"; check the `strCreativeCommons` tag for CC-licensed images.
  - No numeric rate limit is stated.

  — [TheMealDB Terms of Use](https://www.themealdb.com/terms_of_use.php)

### Inferences
- A student/educational project fits the free-key terms. TheMealDB can be a "try cooking it" add-on for Portugal, but is useless for Estonia and Finland.
- TheMealDB "area" values are cuisine demonyms, not cities.

### Gaps
- No published rate limit; I could not find the premium price on the page.

## 4. Other candidates: TasteAtlas, Spoonacular, Edamam, Open Food Facts, DBpedia

### Takeaway
None of these beats the Wikimedia stack for "traditional dishes of a European country".
- **TasteAtlas** is the best content-wise, but I found no public API, and scraping it would be a licence problem.
- **Spoonacular** has a no-card free tier (50 points/day), but only coarse cuisines ("Nordic", "Eastern European"; no Portuguese). It also requires a backlink and deletion of cached data after 1 hour.
- **Edamam** has no free plan any more (cheapest $9/month) and only coarse cuisineTypes.
- **Open Food Facts** covers packaged products, not dishes.
- **DBpedia** I did not test.

### Cited Findings
- **TasteAtlas:** a search for a TasteAtlas API or developer programme found no evidence of either; results were for other "Taste"-named companies (Tastewise, TasteDive). TasteAtlas is a Zagreb-based food guide founded in 2018. — [Wikipedia: TasteAtlas](https://wikipedia.com/wiki/TasteAtlas); [search: no API found, e.g. apitracker](https://www.apitracker.io/a/gettaste/developers)
- **Spoonacular free plan:**
  - $0/month with **50 points/day** (a request is about 1 point + 0.01 per result); calls return 402 when the quota is used up.
  - 1 request/s, 2 concurrent.
  - **No credit card** when subscribing directly (RapidAPI requires one).
  - **Backlink required** on the free plan.
  - Cache user-requested data for at most 1 hour, then delete and refresh.

  — [Spoonacular pricing](https://spoonacular.com/food-api/pricing)
- **Spoonacular cuisines:** a secondary source lists the cuisine values as including African, Eastern European, Nordic and Japanese (about 30+ values). I could not confirm the official list, which sits deep in a 400k-character docs page. — [Educative: Spoonacular supported parameter values](https://educative.io/courses/plan-meals-and-recipes-with-the-spoonacular-api-in-javascript/supported-parameter-values); [Spoonacular docs](https://spoonacular.com/food-api/docs#Cuisines)
- **Edamam Recipe Search API:**
  - No free or developer plan listed.
  - EnterpriseBasic: $9/month prepaid, 10-day trial, 10,000 calls/month, 10/min.
  - EnterpriseCore: $99/month.
  - EnterprisePlus: $399/month.
  - Attribution (logo + link) is required on all plans.

  — [Edamam Recipe API](https://developer.edamam.com/edamam-recipe-api)
- **Edamam cuisineType values:** american, asian, british, caribbean, **central europe**, chinese, **eastern europe**, french, greek, indian, italian, japanese, korean, kosher, mediterranean, mexican, middle eastern, **nordic**, south american, south east asian, world. There is no Portuguese, Estonian or Finnish value. — [Edamam Recipe API docs](https://developer.edamam.com/edamam-docs-recipe-api)

### Inferences
- Spoonacular and Edamam would at best return "Nordic" or "Eastern European" recipes for Tallinn or Helsinki, which is wrong-grained ("Nordic" ≠ Estonian). Their attribution and cache-deletion rules add burden.
- Open Food Facts (packaged products with origin and labels) answers "what can I buy", not "what to eat". Not suitable.
- DBpedia mirrors Wikipedia infobox and category data, so it is unlikely to add anything over Wikidata plus the Wikipedia API.

### Gaps
- **Open Food Facts:** not researched this session (no citation); the claim that it covers packaged products, not dishes, is from background knowledge.
- **DBpedia:** endpoint not tested for dish coverage or reliability this session.
- **Spoonacular:** the official cuisine list is unverified.
- **Edamam:** credit-card policy for the trial not stated on the page.

## 5. MCP servers for recipes / cuisine / Wikidata

### Takeaway
The most useful MCP option is the **official Wikidata MCP** by Wikimedia Deutschland. Its endpoint is `https://wd-mcp.wmcloud.org/mcp/`, over streamable HTTP; I connected with no key or auth. Tools: `search_items`, `search_properties`, `get_statements`, `get_statement_values`, `get_instance_and_subclass_hierarchy`, `execute_sparql`. A live call with "traditional Estonian dish" returned relevant dishes mixed with heavy noise. Recipe MCP servers exist, but they are thin community wrappers around TheMealDB and inherit its Portugal-only coverage.

### Cited Findings
- **Official Wikidata MCP:**
  - Endpoint `https://wd-mcp.wmcloud.org/mcp/` (accepts /mcp and /mcp/), streamable HTTP; tools as listed above.
  - Release log: released October 2025; hierarchy tool added November 2025; keyword and vector search merged January 2026; /docs test environment February 2026; language fallback August 2026.
  - A separate Wikibase MCP was released in May 2026.
  — [Wikidata:MCP](https://www.wikidata.org/wiki/Wikidata:MCP); [Heise coverage](https://heise.de/-10688399); [WikidataCon 2025 talk](https://pretalx.com/wikidatacon-2025/speaker/NZZF7U)
- **Live test (curl JSON-RPC):**
  - `initialize` returned serverInfo `"Wikidata MCP"` version `1.15.0`, protocol 2025-06-18, with no authentication.
  - `search_items("traditional Estonian dish")` returned **relevant dishes**: Tallinn Spiced Sprats, mulgipuder, rössypottu, Uunijuusto, Leipäjuusto, Karelian pasty, mämmi, joulutorttu, mustamakkara, loimukala, Viru Valge.
  - **Noise mixed in**: the Estonian language, glacial boulders, sheep breeds, streets, book series, plus Latvian, Faroese and Swiss dishes.
  — [Wikidata MCP endpoint (live test)](https://wd-mcp.wmcloud.org/mcp/)
- **Community TheMealDB MCP servers:**
  - suraj-yadav-aiml "Recipe Research MCP Server" (FastMCP; search by dish or cuisine, `recipes://cuisines` resource).
  - traves-theberge "MealDB MCP Server" (area filter, meal details, random, category, ingredient).
  - d-c2211 "mealserver" (Python; area tool).
  - Pipeworx "mcp-recipes" (TheMealDB, free, no auth).
  I found none dedicated to traditional or national dishes.
  — [Glama: recipe-mcp](https://glama.ai/mcp/servers/suraj-yadav-aiml/recipe-mcp); [LobeHub: mealdb-mcp](https://lobehub.com/de/mcp/traves-theberge-mealdb-mcp); [GitHub: d-c2211/mealserver](https://github.com/d-c2211/mealserver)
- **Third-party Wikidata SPARQL MCP servers** also exist (e.g. zzaebok/mcp-wikidata, cyanheads/wikidata-mcp-server); they are not Wikimedia Deutschland projects. — [Glama: zzaebok/mcp-wikidata](https://glama.ai/mcp/servers/zzaebok/mcp-wikidata); [Glama: cyanheads wikidata-mcp-server](https://glama.ai/mcp/connectors/io.github.cyanheads/wikidata-mcp-server)

### Inferences
- Google ADK can attach the Wikidata MCP as a remote streamable-HTTP toolset. An LLM agent could then run `execute_sparql` with the query from section 1.
- A deterministic Python function tool (fixed SPARQL + MediaWiki call) is cheaper, testable and less noisy than letting the agent free-search Wikidata through MCP.
- The community MealDB MCP servers add nothing over calling TheMealDB's 3 endpoints directly.

### Gaps
- No rate limits, terms or SLA are documented for wd-mcp.wmcloud.org. It runs on Wikimedia Cloud VPS, so treat it as experimental.
- I did not check maintenance status, stars or licence for the community MCP servers, or Smithery and mcp.so listings beyond the search results.

## 6. Verdict: does each source beat generic web search (Tavily) for "what to eat in Lisbon/Portugal, Tallinn/Estonia, Finland"?

### Takeaway
Yes for the Wikimedia stack, no for the recipe APIs. Recommended chain: Wikivoyage country "Eat" section (traveller-curated, correct for all three countries), then Wikidata SPARQL (canonical names, descriptions, images, popularity ranking), then the Wikipedia list or cuisine article as a fallback. TheMealDB, Spoonacular, Edamam and TasteAtlas do not beat web search here, for reasons of coverage, granularity, cost or the lack of an API.

### Cited Findings
- **Wikivoyage country "Eat" — beats web search.** It names the canonical dishes for all three countries in a few KB of text, e.g. verivorst + mulgikapsad for Estonia; caldo verde, bacalhau à Brás, sardinhas assadas and pastel de nata for Portugal; kalakukko and ruisleipä for Finland. It is free and the planner already uses this API. It is weak for city pages. — [Wikivoyage Estonia#Eat](https://en.wikivoyage.org/wiki/Estonia#Eat); [Wikivoyage Portugal#Eat (live test)](https://en.wikivoyage.org/w/api.php?action=parse&page=Portugal&prop=wikitext&section=44)
- **Wikidata SPARQL — beats web search as a structured complement, with caveats.** Portugal (194) and Finland (117) are rich; Estonia (26) is thin and misses verivorst. Results need filtering of drinks and brands. WDQS can be slow or throttled; QLever is faster. — [QLever (live test)](https://qlever.dev/api/wikidata); [WDQS User Manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual)
- **Wikipedia — beats web search for Portugal** (270-item bulleted list) and is fine for Estonia/Finland via cuisine articles. There are no list pages for EE/FI. — [English Wikipedia API (live test)](https://en.wikipedia.org/w/api.php?action=parse&page=List_of_Portuguese_dishes&prop=sections)
- **Wikidata MCP — useful but noisy.** — [Wikidata MCP endpoint (live test)](https://wd-mcp.wmcloud.org/mcp/)
- **TheMealDB — loses** (8 PT recipes, 0 EE/FI). — [TheMealDB (live test)](https://www.themealdb.com/api/json/v1/1/filter.php?a=Portuguese)
- **Spoonacular — loses** (coarse cuisines, 50 points/day, backlink, 1-hour cache limit). — [Spoonacular pricing](https://spoonacular.com/food-api/pricing)
- **Edamam — loses** (paid only, coarse cuisines). — [Edamam Recipe API](https://developer.edamam.com/edamam-recipe-api)
- **TasteAtlas — not usable** (no public API found). — [search: no API found](https://www.apitracker.io/a/gettaste/developers)

### Inferences
- For Lisbon, Tallinn and Helsinki specifically: no free structured source maps dishes to cities. Use the country-level dishes and let the LLM add city colour from the Wikivoyage city "Eat" text (neighbourhoods, markets, lunch specials).
- Operational requirements for all Wikimedia calls:
  - send a contact-bearing User-Agent;
  - keep concurrency ≤ 3;
  - honour `Retry-After`;
  - cache per country, since dish lists change rarely.
  Without these, a student app will hit the 10 req/min anonymous tier, as happened in testing.

### Gaps
- I did not test the planner's own MediaWiki client or User-Agent string. Whether it already meets the 2026 User-Agent policy is unknown.
