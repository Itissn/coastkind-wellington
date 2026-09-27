# WAINET database

The application stores accounts, observations, review decisions, points and vouchers in a real SQLite database. The default file is `data/wainet.sqlite3`; `server.py --db PATH` selects a different database. Starting the server creates missing tables and applies additive migrations. Existing observations, anonymous browser identifiers and points are retained. SQLite foreign keys are enabled on each connection, and write-ahead logging supports concurrent reads.

## Account ownership

| Table | Purpose and relationships |
| --- | --- |
| `users` | UUID account ID, unique normalized email, public display name, password hash, creation time, optional email verification time and a `member` or `admin` role. Existing accounts migrate to `member`. |
| `sessions` | A hash of a random session token, owning `user_id`, CSRF token, creation time and expiry. Multiple devices may have separate sessions. |
| `admin_invites` | Hash of a one-use administrator setup token, exact invited email, expiry, creation time and optional use time. No plaintext setup token is stored in SQLite. |
| `observations` | Original photo and text, timestamps, location, quality flags, AI output, consent, optional owning `user_id`, and permanent `rewards_waived` decision. |
| `comments` | Observation reference, body, public author name and optional `user_id`. New comments require an account. |
| `support` | Observation reference and optional `user_id`. New support is unique per observation/account. Historical browser support remains separate. |
| `reviews` | Append-only observation review decisions, reviewer, note, timestamp and the analysis snapshot reviewed. |
| `points_ledger` | Signed point adjustments tied to an observation and account. Historical anonymous adjustments retain their original contributor hash and no account owner. |
| `alerts` / `alert_events` | Local area/category attention signals and their audit history. These do not establish pollution severity or government receipt. |
| `reward_catalog` | Reward name, brand, optional points price and value, and operator-controlled availability. |
| `reward_vouchers` | Operator-imported voucher codes, expiry and import provenance. A voucher belongs to one catalog reward. |
| `reward_redemptions` | Immutable allocation of one voucher to one account, with the points spent and catalog details at redemption time. |
| `reward_requests` | Pending, rejected or fulfilled member request, saved cost/value, reviewer and member-visible decision note. |
| `reward_request_keys` / `reward_request_events` | Retry deduplication and audited voucher decisions. |
| `feedback` / `feedback_events` | Private member messages, administrator responses and status history. |
| `official_source_cache` | Public council/GeoNet payloads, original sample/event dates, retrieval time and last refresh outcome. Separate from user observations, review decisions and rewards. |

Account IDs are assigned by the server. The browser's old `X-Client-ID` value grants no ownership, wallet access or reward claim. Authenticated API writes derive the account from a server-side session, and supplied ownership fields are rejected. Public observations expose display names rather than email addresses or account IDs.

Official sources use a 15-minute cache and bounded requests to fixed public endpoints. The map reads cache/snapshot data without making 20 external requests on page load. Opening a community refreshes its sources when needed. The snapshot maintenance command uses a separate database at `data/official/source-cache.sqlite3`; static publication copies only curated public JSON. Government/research observation exports and points never include official cache rows or fictional demonstration records. Historical water samples remain dated and are not current swimming assessments.

## Guest contributions and rewards

A guest upload must explicitly contain `as_guest: true`. Its database row has `user_id = NULL` and `rewards_waived = 1`. A signed-in user can also choose this guest path. A guest upload goes through the same photo validation, duplicate detection, analysis and review pipeline as an account upload, but it can never earn points. Creating an account later does not claim guest records or historical browser points.

A normal signed-in upload has the session's `user_id` and `rewards_waived = 0`. Human review awards 10 points only when the observation passes the quality checks. Repeated approval does not award the same contribution twice. Reversing a decision adds an adjustment instead of deleting the previous ledger entry. A reversal after points have been spent may produce a negative balance, which remains visible and prevents further unaffordable redemptions.

The account balance is:

```text
SUM(points_ledger.delta WHERE user_id = account)
  - SUM(reward_redemptions.points_cost WHERE user_id = account)
```

Member requests reserve neither points nor stock. Administrator approval uses a SQLite `BEGIN IMMEDIATE` transaction for the current balance check and voucher allocation. A unique request UUID makes a retry return the same result; one pending request per member/reward prevents duplicates. A voucher can be allocated only once; another account cannot use the same request UUID to retrieve it. Original reward price and value are preserved even if the catalog changes later. Insufficient points or unavailable stock leaves a request pending. Rejection spends no points. Existing fulfilled redemptions remain valid.

Vouchers are delivered through the signed-in account's private rewards screen and `GET /api/rewards/mine`. The public catalog never returns voucher codes. An operator must configure a reward and import legitimately obtained inventory through `reward_admin.py` before redemption becomes available. The proposed retailer entries start disabled, with no assumed price, partnership, inventory or monetary value. The application does not issue retailer vouchers, send delivery email or verify a retailer code with the retailer.

## Sessions and local security

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes with 600,000 iterations. Passwords must be 12–128 characters. The database never stores plaintext passwords or raw browser session tokens. Login and registration attempts are throttled in memory.

Sessions expire after 14 days. The browser receives an `HttpOnly`, `SameSite=Lax` cookie; authenticated writes also require the session's `X-CSRF-Token`. Login and registration require JSON and same-origin browser requests. Signing out deletes the server-side session, and a successful new login rotates the current session. An expired or revoked cookie cannot open a wallet or private voucher history. Email verification is not implemented: `email_verified` remains false for newly registered accounts. An account count is therefore not a count of verified people.

By default this build binds to the local loopback interface and accepts `localhost` or `127.0.0.1` hosts on its server port. Deployment can set `WAINET_HOST`, `PORT`, `WAINET_DB_PATH` and `WAINET_PUBLIC_ORIGIN`; `RENDER_EXTERNAL_URL` is an optional origin fallback. A configured HTTPS origin makes session and logout cookies `Secure`. Origin and host checks use that configured origin and do not trust forwarded request headers. On a non-loopback listener, local-host requests are accepted only for the health endpoint. Keep the database on persistent storage. Public deployment still needs TLS termination and operational controls for the service and database. Email verification and password recovery require a configured email provider and are not available in this build.

## Observation quality and privacy

The database separates user statements, AI analysis, quality flags, human review and reward decisions. Guest reports remain useful evidence, but anonymous uploads do not count as distinct people. Area/category summaries count registered contributing accounts separately from legacy browser identifiers. Current attention rules require at least three usable distinct photos, two contributing accounts and a reporting span of seven days. They are local review prompts rather than verified environmental findings.

Exact opt-in location is retained privately in the database; public observation responses round coordinates to two decimal places. Uploaded image metadata is removed during normalization. Photos, free text and public display names remain visible on the public community feed, so the upload screen must guide users about public content. The server does not expose arbitrary files, database files, credentials, session records or voucher inventory through static routes.

The database file itself contains email addresses, password hashes, exact opt-in coordinates and unredeemed voucher codes. Anyone who can read that local file can access those records. Keep database files, backups, environment files and voucher import files outside public repositories and shared web folders. There is no database-at-rest encryption in this local build. Backups must preserve the database consistently, using SQLite backup facilities or a stopped server rather than copying an active database without its WAL state.

Government exports remain a deliberate operator action through `manage.py`. Licensed aggregate exports include only eligible, human-reviewed, opted-in observations and omit small groups. No dataset sale, government delivery or external publication happens automatically.

## Administrator access and research downloads

The administrator page at `/admin` uses the same account and session system. The server protects `/api/admin/data`, `/api/admin/export` and review mutations with the current database role; hiding controls in the browser is not the access check. Revoking a role takes effect on the next request, including requests from existing sessions. Registration, login and ordinary uploads reject client-supplied privilege fields. There is no default administrator or default password.

The local database operator can grant access to an existing, explicitly named account:

```powershell
python admin_manage.py grant administrator@example.com
python admin_manage.py revoke administrator@example.com
python admin_manage.py create
```

`create` asks for the email, public display name and password interactively, with the password hidden. It cannot reset an existing account. `--db PATH` selects a database and must precede the subcommand. The account must be exactly identified by its full email; the tool does not select the first account or promote accounts automatically.

To let the intended owner choose a password, the local operator can instead write a private invitation file:

```powershell
python admin_manage.py invite administrator@example.com --out data/admin-setup.json --origin https://your-site.example
```

The file is created exclusively with owner-only permissions where supported. It contains a setup URL with its token in the URL fragment, the invited email and expiry. The CLI prints only the file path, never the token or URL. The invitation expires in 24 hours, is usable once, and requires both the secret token and the invited email. Keep the JSON file private and out of published files and source control. Opening `/admin` and completing setup creates the new administrator account and consumes the token in one SQLite transaction. An existing account cannot have its password reset through this flow; use local `grant` instead.

On a first production deployment, `WAINET_ADMIN_EMAIL` and a random URL-safe `WAINET_ADMIN_SETUP_TOKEN` of at least 43 characters can seed the same 24-hour invitation. Configure the token as a hosting secret. Both settings are required together. Seeding happens only when no administrator exists; restarting does not refresh, reactivate or reuse a previously configured token. Knowing the configured email alone grants no privileges. Remove the setup secret from hosting configuration after setup is complete.

Administrator downloads support CSV and JSON and whitelist the `community`, `review` and `status` filters. CSV uses UTF-8 with BOM, quotes every cell, and prefixes spreadsheet formula-like values with an apostrophe. Downloads include the selected raw observation text, structured AI suggestions, quality flags and review history, including pending or rejected research records. Every record carries `export_eligible` and `export_blocks`; an administrator research download is distinct from the curated government export and the consented aggregate licensing export.

Research endpoints do not return account emails, account IDs, password hashes, sessions, invitation secrets, voucher codes, database files or exact GPS coordinates. Counts and trends are computed from the selected observations. Associated area alerts retain their historical scope and say so explicitly; selecting a filter does not recalculate the alert's evidence history. Browser review actions require an administrator session and CSRF token, and the server derives the reviewer name from that session. Reviews reuse the existing quality checks and points adjustment rules.
