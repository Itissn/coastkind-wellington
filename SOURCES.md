# Official coastal resources

Links and purposes checked on 26 September 2026. These are independent official resources; no partnership or endorsement is implied. Official measurements and events are kept separate from community observations. Coastkind does not reproduce swimming advisories, weather forecasts or fishing limits as its own findings.

| Resource | Why a coastal user would open it |
| --- | --- |
| [LAWA — Can I Swim Here?](https://www.lawa.org.nz/explore-data/swimming) | Select Wellington and a swimming site to consult monitoring results, predicted water quality and site warnings. The former Wellington coastal index redirects to this swimming search. |
| [MetService — Kāpiti and Wellington marine forecast](https://www.metservice.com/marine/regions/kapiti-wellington) | Check the region's marine forecast, warnings and tides before water activities. Greater Wellington's [harbours and coasts page](https://www.gw.govt.nz/environment/harbours-and-coasts/) links directly to this forecast. |
| [Greater Wellington — Environmental incidents](https://www.gw.govt.nz/environment/environmental-incidents/) | Find the council's reporting contact and what location, timing and observation details help it respond to pollution. A Coastkind post is not a council submission. |
| [MPI / Fisheries New Zealand — Fishing rules](https://www.mpi.govt.nz/fishing-aquaculture/recreational-fishing/fishing-rules) | Choose the applicable recreational fishing area and check current limits, closures and gear restrictions before each trip. This link concerns recreational fishing, not commercial fishing permission. |
| [Maritime New Zealand — Key safety messages for recreational boating](https://www.maritimenz.govt.nz/recreational-craft/on-the-water/rules-and-safety/) | Review boating preparation, the Boating Safety Code and guidance on equipment, communications and skipper responsibilities. |

The LAWA swimming page and official explanatory material describe the difference between sampling and predictions. A community photograph or an AI interpretation does not replace those sources or establish water safety. See [LAWA's coastal and freshwater recreational monitoring explanation](https://www.lawa.org.nz/learn/factsheets/can-i-swim-here/coastal-and-freshwater-recreational-monitoring).

Verification note: the source pages were checked through primary-source web content and official cross-links. MetService renders its forecast using JavaScript. LAWA and MPI intermittently blocked direct automated retrieval, so checks also used their indexed official content. No current weather, swimming status or numerical fishing limit is claimed here. Recheck links when maintaining the site.

## Map locations

The map contains 20 approximate coastal community locations. Most are based on beach and harbour locations in Appendix 1 of Greater Wellington's [2013–14 coastal monitoring data report](https://www.gw.govt.nz/assets/Documents/2014/11/Coastal-SoE-Monitoring-Programme-Annual-Data-Report-2013-14.pdf), with NZTM coordinates converted to latitude/longitude. Mākara's locality is supported by the [LINZ consultation appendix](https://www.linz.govt.nz/sites/default/files/2025-02/3%20Advice%20from%20Iwi%20consultation.pdf). Southern and eastern bay names and approximate positions are also checked against Wellington City Council's [coastal reserve management plan overview](https://www.letstalk.wellington.govt.nz/coastal-reserve-management-plan/crmp-overview).

These are navigation markers, not exact incident positions, current monitoring coverage, or water-quality ratings. The map uses OpenStreetMap tiles and retains the required on-map attribution. Five established demonstration communities have fictional records; the other community pages can be explored without inventing observations for those areas.

## Council laboratory samples and LAWA guidance

Greater Wellington publishes a documented, public [Hilltop service](https://hilltop.gw.govt.nz/data.hts), linked from its [service examples](https://hilltop.gw.govt.nz/). No API key is required. The service's SOS `GetObservation` request returns WaterML 2; omitting a temporal filter requests the latest available value for the named station and measurement. Requests must encode spaces as `%20`, because this server treats `+` literally.

The application requests **Enterococci Bacteria** from fixed named coastal stations. It does not request similarly named forecast or virtual-warning series. The [official site catalogue](https://hilltop.gw.govt.nz/data.hts?Service=Hilltop&Request=SiteList&Location=LatLong) supplies station names and coordinates; `official_water.py` records the verified mapping for all 20 communities. Mākara Beach and Houghton Bay have no station coordinates in that catalogue, so no official-location marker or invented distance is supplied for those stations.

[Verified Lyall Bay sample request](https://hilltop.gw.govt.nz/data.hts?Service=SOS&Request=GetObservation&FeatureOfInterest=Lyall%20Bay%20at%20Tirangi%20Road&ObservedProperty=Enterococci%20Bacteria): on 26 September 2026 the response contained **180 n/100ml**, sampled **12 August 2026 at 11:49 +12:00**. This is an example of a dated measurement, not a live swimming rating. Other genuine responses include detection limits such as `<4`, which are retained as text. The WaterML point's `time` is the sampling timestamp; `om:resultTime` is response generation time and must not replace it.

Sample age and retrieval age are different. During verification, the latest Mākara Beach sample was from 2001 and Houghton Bay from 2005; those are historical measurements. Even successfully refreshed data may contain old samples. One station cannot establish conditions throughout a whole bay. Consult [LAWA swimming guidance](https://www.lawa.org.nz/explore-data/swimming) for current site advisories and warnings.

Attribution: **Greater Wellington Regional Council**, with a link to the exact measurement request and the [environmental data dashboard](https://graphs.gw.govt.nz/envmon?view=map). The council's [geographic data page](https://www.gw.govt.nz/environment/environmental-data-and-information/geographic-mapping-information/) states a default CC BY 4.0 licence for its open geographic data. The Hilltop sample responses do not contain a dataset-specific licence statement; do not label them as Coastkind-owned data or silently apply the GIS licence to every time series. LAWA's [download page](https://www.lawa.org.nz/download-data) describes CC BY 4.0 for most downloadable datasets and requires checking individual metadata. This integration retrieves council measurements directly; it does not scrape blocked LAWA pages.

## GeoNet earthquake context

The [GeoNet API documentation](https://api.geonet.org.nz/) describes `/quake?MMI=3`, returning at most 100 possibly felt earthquakes from the past 365 days. It is public and requires no API key. Version the request using `Accept: application/vnd.geo+json;version=2`. GeoJSON supplies longitude/latitude, stable `publicID`, event `time`, `magnitude`, depth in kilometres, `mmi`, `locality` and `quality`. Regional and recent-time filtering is applied after retrieval; this limited feed is not a complete regional earthquake catalogue.

Earthquakes are geohazard context, not water-quality tests, pollution findings or tsunami alerts. Keep their event timestamps, coordinates, source links and quality separate from bacterial samples and community reports. A retrieval with no matching events does not prove the absence of hazards.

The [GeoNet data policy](https://www.geonet.org.nz/policy) makes data available free of charge, requests acknowledgement of GeoNet and its programme sponsors, and identifies the earthquake catalogue DOI [10.21420/0S8P-TZ38](https://doi.org/10.21420/0S8P-TZ38). Its site content is licensed under CC BY 3.0 New Zealand; retain source attribution and the data-policy link in redistributed snapshots.
