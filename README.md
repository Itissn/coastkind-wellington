# Coastkind

[Public presentation](https://itissn.github.io/coastkind-wellington/) · [Multimodal AI and dataset explorer](https://itissn.github.io/coastkind-wellington/demo.html)

An English-language, map-first coastal community application for Wellington. Residents upload a photo, optionally attach a confirmed device location and a few words, and contribute structured observations to a local SQLite database.

Built for swimmers, anglers, boaters, fishing crews and environmental advocates. Its purpose is to turn small everyday contributions into shared evidence that can help councils prioritise monitoring and clean-up, reduce monitoring costs, and improve coastal life. These are intended benefits, not measured outcomes. Community observations complement water-quality testing; photographs and personal experiences do not establish safe swimming or fishing conditions.

## Run

Requires Python 3.10+ and Pillow (already available in this workspace).

```powershell
python -m pip install -r requirements.txt
python server.py
```

Open **http://localhost:8000**. The server listens on the local computer only. Opening `index.html` directly allows map browsing, but saving requires the Python server. This is not a public deployment.

## Public presentation and synthetic dataset

The `docs/` directory is a standalone, read-only presentation for GitHub Pages. Publish the `main` branch's `/docs` folder; the repository root contains the full Python application. The presentation includes the coastal map, community feeds, a photo-plus-text analysis walkthrough, database table counts, filters, charts and CSV/JSON downloads. Its conspicuous synthetic banner applies to every observation, model output, review, alert and point balance. No real accounts, passwords, sessions, precise device locations, database files or voucher inventory are published.

The demonstration includes **240 fictional observations**, **30 fictional accounts**, **48 guest contributions**, five communities, six activities, six pollution categories and a 60-day reporting period. It covers positive experiences, uncertain evidence, repeated concerns, duplicate images, missing dates, low-resolution images, imprecise locations, pending/failed analysis, review decisions and account-only points. The synthetic dataset and visual placeholders are scripted; they are not live AI results, real pollution reports, or evidence of model accuracy or government cost savings.

```powershell
python seed_demo.py
python demo_server.py
python build_presentation.py --out docs
python -m unittest test_demo -v
```

The local demonstration is at **http://localhost:8001/demo-data**; its separate database is `data/demo/coastkind-demo.sqlite3`. `seed_demo.py` also writes `observations.csv`, `dataset.json`, `summary.json` and `CODEBOOK.md` in that directory. Re-running it preserves the generated records without duplication and refuses unmarked databases or production paths. Synthetic records are blocked from live AI processing and from the normal government/licensed export. See [PRESENTATION.md](PRESENTATION.md) for publishing and presentation steps.

The public analysis walkthrough reads precomputed examples and does not call a model or change a database. The full local backend implements real multimodal API calls after a key is configured. GitHub Pages hosts the static presentation; the working account/upload backend requires separate server hosting.

Official external resources are available through **Useful links** on the map and community pages. They connect visitors to current swimming information, marine forecasts, pollution reporting, fishing rules and boating guidance. The links open independently of Coastkind and do not imply an official partnership. See [SOURCES.md](SOURCES.md) for the purpose and source of each link.

The map offers 20 coastal communities. Regional zoom shows five main labels and smaller points for nearby communities; zooming in reveals additional names. All locations remain available in the selector. The 240-record demonstration intentionally covers the original five communities, while the other areas open without fabricated extra observations.

Map source buttons separate community signals, Greater Wellington water-sampling stations and GeoNet earthquake points. Community colours use the past 30 days: red for repeated reviewed concerns, orange for evidence needing review, green for eligible positive observations, and grey for insufficient evidence. They are not swimming ratings. Water markers describe sample age (blue within seven days, grey older/unavailable); GeoNet markers describe event age (orange within seven days, grey 8–30 days). An earthquake point opens the nearest coastal community without claiming that community was affected.

Each community has two views: **Community** for photos and words, and **Official data** for dated samples and nearby earthquakes. Real water values come from the council's public Hilltop service, with LAWA links for swimming advice. The GeoNet feed is limited; it is not a tsunami warning service. Source failures retain dated cached records rather than manufacturing results. Refresh the separate public snapshot with `python official_data.py --refresh-snapshot`, then rebuild the presentation. See [SOURCES.md](SOURCES.md) for provenance and attribution.

## Accounts and guest uploads

The website has three simple access levels. Guests upload photos and optional
words without rewards. Members upload, earn reviewed contribution points, request
vouchers and send private feedback. Administrators sign in at **/admin** to inspect
and export observation data, view analysis, review evidence, approve voucher
requests and respond to feedback. Ordinary registration always creates a member.
An operator-created temporary administrator password must be replaced before any
administration functions can be used. See [DEPLOYMENT.md](DEPLOYMENT.md) for the
prepared shared online backend; GitHub Pages remains a read-only presentation.

Everyone uses the same **Sign in** entry. Only an administrator sees **Data management**. Members open **My account** to see points, usable vouchers and redemption options. The administrator workspace separates **Records**, **Analysis** and **Requests**; server-side permission checks protect every private operation.

Use **Create account** to register with a display name, email and a password of 12–128 characters. Passwords use a random salt and PBKDF2-HMAC-SHA256 with 600,000 iterations; raw passwords are not stored. Login uses a revocable, expiring server session in an HttpOnly, SameSite cookie; authenticated writes require a CSRF token. The local HTTP cookie is not Secure: public deployment requires HTTPS and secure cookies. See [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Guests can upload immediately without registration. **Every guest upload permanently waives rewards**, including an upload explicitly made as a guest while signed in. Its database row has `user_id = NULL` and `rewards_waived = 1`. Registering later never claims guest evidence or anonymous browser points. The same photo validation, AI pipeline and review rules apply to guest and account evidence.

Normal signed-in uploads are tied to the session's user ID. Clients cannot select another account ID or award points. Comments and support require sign-in. The public feed shows only a display name (or Guest contributor), never account emails. Points and issued vouchers are private account data and are available after signing in from another browser.

The SQLite database is created and migrated on startup at `data/coastkind.sqlite3`, with users, sessions, owned observations, point transactions, reward inventory and redemption records. Existing anonymous observations are preserved without reassignment. See [DATABASE.md](DATABASE.md) for the relationships and migration details. No email is sent; email ownership verification and password recovery are not yet configured.

## AI configuration

Copy `.env.example` to `.env`, set `OPENAI_API_KEY` locally, and restart the server. `OPENAI_MODEL` defaults to `gpt-4.1-mini`; select a vision model with Structured Outputs available to your account. Never put API keys in browser code or commit `.env`.

The backend sends the submitted photo and words to OpenAI's Responses API with a strict JSON schema and `store: false`. Exact GPS coordinates are not sent to the model. Implementation references: [image inputs](https://developers.openai.com/api/docs/guides/images-vision), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

Without a key, observations are genuinely saved as `pending`, with no invented AI results. Once a key is configured, queued records are processed. Failed records retain their evidence; retry them after fixing the configuration with:

```powershell
python server.py --retry-failed
```

Only one server process should own a database. In-progress jobs return to the queue on restart. A provider request interrupted after completion but before local persistence may be billed again on restart; the local observation ID still prevents duplicate database records.

## Data and quality controls

`data/coastkind.sqlite3` stores submitted photo copies, words, timestamps, optional exact coordinates and device accuracy, image hashes, AI results, model/response identifiers, schema/prompt versions, comments, support, and review audit entries. Photos are resized in the browser, decoded and validated by Pillow, and normalised without EXIF metadata. Retain camera originals separately for formal evidence.

- Required: a decodable photo and a community (prefilled when entering a community).
- Optional: feelings, photo date, and device location. Device coordinates require an explicit user confirmation that they correspond to the photo location. Photo dates and submission dates are separate; an unknown photo date stays unknown.
- Exact coordinates stay in the local database. Public responses and exports use coordinates rounded to 0.01 degrees; community-only records explicitly retain that lower precision.
- Upload IDs make retries idempotent. Matching decoded-pixel hashes link repeated photos, skip additional AI calls, and exclude them from independent evidence exports. Crops, rephotographs and materially changed versions are not reliably detected.
- Quality flags cover small images, missing dates, community-only locations, imprecise GPS, locations over 10 km from the selected community point, and duplicate photos. The 10 km check is a coarse consistency heuristic, not a coastal boundary definition.
- AI suggestions separate visible evidence, reported feelings, sentiment, activity, pollution categories and uncertainty. A photo cannot establish water safety, industrial responsibility or chemical composition. Schema validation checks structure, not factual accuracy.
- AI completion **never** constitutes verification or approval. Raw observations and AI suggestions stay distinct. New analysis resets review eligibility.

## Repeated community concerns

Community pages group concerns by area and category and show submission counts, different photos, reporting date span, follow-ups, and support. Users may flag a pollution concern before AI is configured; otherwise classification can come from AI. These are **area/topic trends**, not confirmed incidents or proof of continuous pollution.

Repeated photos are identified separately. Supporting a record is not new photographic evidence. New contributor/support counts use registered accounts, not verified people; a person may create multiple accounts. Guest reports contribute evidence but no inferred person count. Old browser-based counters are kept separate in the API. A record can contribute to more than one topic. Do not sum categories as independent incident totals.

Downloadable community summaries are explicitly **unverified community signals**, separate from the operator's human-reviewed export. No file download sends anything to government.

## Operator review and government-ready export

The local command-line workflow keeps approval out of the public upload API:

```powershell
python manage.py queue
python manage.py review RECORD_ID --decision approved --reviewer "Reviewer name" --note "Checked photo, stated date, community, classification and uncertainties."
python manage.py review RECORD_ID --decision rejected --reviewer "Reviewer name" --note "Location is inconsistent; a corrected observation is needed."
python manage.py export --out data/reviewed-observations.json
```

Every review is appended to an audit log with an analysis snapshot. Approval requires completed analysis, a supplied photo date, a sufficiently sized non-duplicate photo, no flagged GPS inconsistency, and no AI flag of unrelated/conflicting/insufficient evidence. Community-only location is allowed, explicitly labelled. Original values are not silently corrected: reject and request a replacement when the evidence needs correction. Only approved records passing the checks appear in the curated export. A human-reviewed observation is still not a laboratory result.

## Backend trend alerts

The server persists a local attention alert when a topic has **at least three different usable photos, at least two contributing registered accounts, and at least seven days of submitted reports**. Guest reports may contribute usable photos and reporting dates but cannot satisfy the two-account condition. Repeated images, low-resolution images, rejected reports and flagged location inconsistencies do not satisfy this trigger. Supporting a post does not trigger an alert. These thresholds are prototype heuristics, not validated predictions of severity or verified numbers of residents. Reporting span is not proof of continuous pollution.

```powershell
python manage.py alerts
python manage.py acknowledge ALERT_ID --operator "Reviewer name" --note "Evidence assigned for review."
```

Alerts and acknowledgements have an audit history. Additional different photos can reopen attention; an alert becoming inactive means it no longer meets the rule, not that pollution is resolved. No email, council report or emergency notification is sent.

## Optional licensed dataset exports

Normal uploading does not give commercial-use permission. An unchecked, optional preference records explicit consent and the consent-text version. The licensed export separately requires this consent and human approval, aggregates by community/month/category, and suppresses groups smaller than five observations.

```powershell
python manage.py export --licensed --out data/licensed-dataset-draft.json
```

These exports contain no raw photos, original text, precise coordinates, record IDs or reviewer identities. Aggregation reduces exposure but is not a guarantee of anonymisation. There is no dataset store, payment integration, customer delivery or completed sale. Before commercial release, establish licensing terms, rights verification, withdrawal/deletion processes, customer purpose restrictions and an appropriate privacy review. Consent can currently be changed only by the local operator; there is no public consent-management account interface yet.

## Contribution points and proposed vouchers

The prototype awards **10 community points only after human approval** of eligible account-owned evidence. AI completion or uploading alone awards nothing. The points ledger is transactional, auditable, idempotent across repeated approvals, and reverses the award if approval is withdrawn. Guest, duplicate or ineligible photos cannot earn points. Optional commercial dataset consent is unrelated to rewards.

The wallet is now bound to the registered account. The interface lists New World, Woolworths, Farmers and Chemist Warehouse as **suggested brands, not partners**. These seeded options have no price, denomination or inventory and cannot be redeemed.

An operator can configure an actual reward and import legitimately obtained voucher codes. Use the actual agreed denomination and point cost; the following placeholders are not a commercial offer:

```powershell
python reward_admin.py configure REWARD_SLUG --brand "ISSUER" --title "ACTUAL VOUCHER TITLE" --points POINT_COST --value-label "ACTUAL DENOMINATION" --enable
python reward_admin.py import REWARD_SLUG --file data/private-voucher-inventory.json --operator "Operator name"
python reward_admin.py catalog
```

The private import file is a JSON array of objects with `code` and `expires_at` (an ISO timestamp with timezone or `null`). Keep it under the ignored `data/` directory, never in source control. Import commands do not log codes. A member request enters a pending queue without spending points or allocating stock. Administrator approval rechecks the balance and unexpired stock, atomically spends points and allocates one voucher. Retries cannot double-charge or double-issue. Rejection leaves the points untouched. Issued codes appear only under **My account → My vouchers** for the owning account; no email is sent. A later evidence-review reversal can leave a negative balance after points were spent, preventing further unaffordable redemption.

Real reward fulfilment still requires a funded budget, legitimate voucher supply, issuer conditions and abuse controls. No real voucher inventory was imported during implementation.

## Verification

```powershell
python -m unittest test_server test_accounts test_rewards -v
```

Tests use isolated temporary databases and mocked AI responses, with no provider charges. They cover real HTTP upload, persistence, duplicate/idempotency handling, location confirmation/privacy, failed analysis, request/response structure, review eligibility, export exclusion, collective concerns, and restricted static file serving. Browser checks also cover map navigation, actual upload, reload persistence, denied geolocation and responsive layout. A live AI call requires a configured key and has not been verified in this workspace.

## Remaining deployment work

The current app runs locally and is not yet a publicly hosted community. Public rollout still needs email verification/password recovery, abuse-resistant contribution counts, moderation and reviewer permissions, broader rate limits, retention/deletion rules, HTTPS/secure cookies, protected database backups, and a production application server. Authentication includes only basic per-process attempt throttling. Model accuracy needs validation against a locally labelled Wellington dataset before treating it as a triage tool. No measured cost savings or response-time improvement is claimed.

Map tiles, Leaflet and Google Fonts need internet access. Name-based community access works when the map is unavailable. Earlier browser-local demo posts are not automatically uploaded to the database. Their old localStorage data is left untouched.
