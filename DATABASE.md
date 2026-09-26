# Coastkind database

The application stores accounts, observations, review decisions, points and vouchers in a real SQLite database. The default file is `data/coastkind.sqlite3`; `server.py --db PATH` selects a different database. Starting the server creates missing tables and applies additive migrations. Existing observations, anonymous browser identifiers and points are retained. SQLite foreign keys are enabled on each connection, and write-ahead logging supports concurrent reads.

## Account ownership

| Table | Purpose and relationships |
| --- | --- |
| `users` | UUID account ID, unique normalized email, public display name, password hash, creation time and optional email verification time. |
| `sessions` | A hash of a random session token, owning `user_id`, CSRF token, creation time and expiry. Multiple devices may have separate sessions. |
| `observations` | Original photo and text, timestamps, location, quality flags, AI output, consent, optional owning `user_id`, and permanent `rewards_waived` decision. |
| `comments` | Observation reference, body, public author name and optional `user_id`. New comments require an account. |
| `support` | Observation reference and optional `user_id`. New support is unique per observation/account. Historical browser support remains separate. |
| `reviews` | Append-only observation review decisions, reviewer, note, timestamp and the analysis snapshot reviewed. |
| `points_ledger` | Signed point adjustments tied to an observation and account. Historical anonymous adjustments retain their original contributor hash and no account owner. |
| `alerts` / `alert_events` | Local area/category attention signals and their audit history. These do not establish pollution severity or government receipt. |
| `reward_catalog` | Reward name, brand, optional points price and value, and operator-controlled availability. |
| `reward_vouchers` | Operator-imported voucher codes, expiry and import provenance. A voucher belongs to one catalog reward. |
| `reward_redemptions` | Immutable allocation of one voucher to one account, with the points spent and catalog details at redemption time. |

Account IDs are assigned by the server. The browser's old `X-Client-ID` value grants no ownership, wallet access or reward claim. Authenticated API writes derive the account from a server-side session, and supplied ownership fields are rejected. Public observations expose display names rather than email addresses or account IDs.

## Guest contributions and rewards

A guest upload must explicitly contain `as_guest: true`. Its database row has `user_id = NULL` and `rewards_waived = 1`. A signed-in user can also choose this guest path. A guest upload goes through the same photo validation, duplicate detection, analysis and review pipeline as an account upload, but it can never earn points. Creating an account later does not claim guest records or historical browser points.

A normal signed-in upload has the session's `user_id` and `rewards_waived = 0`. Human review awards 10 points only when the observation passes the quality checks. Repeated approval does not award the same contribution twice. Reversing a decision adds an adjustment instead of deleting the previous ledger entry. A reversal after points have been spent may produce a negative balance, which remains visible and prevents further unaffordable redemptions.

The account balance is:

```text
SUM(points_ledger.delta WHERE user_id = account)
  - SUM(reward_redemptions.points_cost WHERE user_id = account)
```

Redemption uses a SQLite `BEGIN IMMEDIATE` transaction for the balance check and voucher allocation. A unique request UUID makes a retry return the same allocation. A voucher can be allocated only once; another account cannot use the same request UUID to retrieve it. Original reward price and value are preserved even if the catalog changes later.

Vouchers are delivered through the signed-in account's private rewards screen and `GET /api/rewards/mine`. The public catalog never returns voucher codes. An operator must configure a reward and import legitimately obtained inventory through `reward_admin.py` before redemption becomes available. The proposed retailer entries start disabled, with no assumed price, partnership, inventory or monetary value. The application does not issue retailer vouchers, send delivery email or verify a retailer code with the retailer.

## Sessions and local security

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes with 600,000 iterations. Passwords must be 12–128 characters. The database never stores plaintext passwords or raw browser session tokens. Login and registration attempts are throttled in memory.

Sessions expire after 14 days. The browser receives an `HttpOnly`, `SameSite=Lax` cookie; authenticated writes also require the session's `X-CSRF-Token`. Login and registration require JSON and same-origin browser requests. Signing out deletes the server-side session, and a successful new login rotates the current session. An expired or revoked cookie cannot open a wallet or private voucher history. Email verification is not implemented: `email_verified` remains false for newly registered accounts. An account count is therefore not a count of verified people.

This build binds to the local loopback interface and accepts only `localhost` or `127.0.0.1` hosts on its server port. Its session cookie is suitable for local HTTP. Public deployment needs HTTPS with a `Secure` session cookie, production serving and durable abuse controls. Email verification and password recovery require a configured email provider and are not available in this local build.

## Observation quality and privacy

The database separates user statements, AI analysis, quality flags, human review and reward decisions. Guest reports remain useful evidence, but anonymous uploads do not count as distinct people. Area/category summaries count registered contributing accounts separately from legacy browser identifiers. Current attention rules require at least three usable distinct photos, two contributing accounts and a reporting span of seven days. They are local review prompts rather than verified environmental findings.

Exact opt-in location is retained privately in the database; public observation responses round coordinates to two decimal places. Uploaded image metadata is removed during normalization. Photos, free text and public display names remain visible on the public community feed, so the upload screen must guide users about public content. The server does not expose arbitrary files, database files, credentials, session records or voucher inventory through static routes.

The database file itself contains email addresses, password hashes, exact opt-in coordinates and unredeemed voucher codes. Anyone who can read that local file can access those records. Keep database files, backups, environment files and voucher import files outside public repositories and shared web folders. There is no database-at-rest encryption in this local build. Backups must preserve the database consistently, using SQLite backup facilities or a stopped server rather than copying an active database without its WAL state.

Government exports remain a deliberate operator action through `manage.py`. Licensed aggregate exports include only eligible, human-reviewed, opted-in observations and omit small groups. No dataset sale, government delivery or external publication happens automatically.
