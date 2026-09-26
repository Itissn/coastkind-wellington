# Shared online service

GitHub Pages is a read-only presentation. Real uploads, member rewards and the
administrator workspace need the Python server and its persistent SQLite database.
The same server serves the English website and API, so browser sessions remain
same-origin. Do not point the presentation's JavaScript at a second origin.

## Prepared hosting configuration

`render.yaml` defines one Python web service in Singapore, one 1 GB persistent
disk at `/var/data`, and health checks at `/api/health`. The database, original
photos, account hashes and review records live in this disk. Service redeploys
must keep the disk attached; never use Render's ephemeral filesystem for this data.

The configured `0.5c-512mb` plan costs US$7/month and the 1 GB disk US$0.25/month
as checked on 26 September 2026: about US$7.25/month before taxes, additional
usage and AI charges. Confirm the current total before creating the paid service.
Sources: [Render pricing](https://render.com/pricing),
[persistent disks](https://render.com/docs/disks),
[Blueprint specification](https://render.com/docs/blueprint-spec).

## First deployment

1. Connect the owner's Render account and GitHub repository, then review the
   prepared Blueprint and cost before creating it. Automatic deploys are off.
2. Keep `COASTKIND_HOST=0.0.0.0` and the database path from the Blueprint. Render
   supplies `PORT` and `RENDER_EXTERNAL_URL`; the server uses that HTTPS origin
   to validate requests and issue Secure cookies.
3. Set `COASTKIND_ADMIN_EMAIL` and `COASTKIND_ADMIN_PASSWORD_HASH` privately in
   Render's environment. Supply a PBKDF2 hash made by `accounts.hash_password`,
   never a plaintext password. This creates a first administrator only if no
   administrator or account with that email already exists. It never resets an
   existing account. The administrator must change the temporary password on
   first login. Remove the bootstrap environment values after setup.
4. Alternatively, use `admin_manage.py invite` through the operator shell and
   deliver its private setup URL directly to the administrator.
5. Set `OPENAI_API_KEY` privately only when live AI analysis is wanted. With no
   key, original uploads are saved as awaiting analysis; no analysis is fabricated.
6. Visit `/admin`, complete first-password setup, then verify a real upload from
   another browser appears in the administrator records and export. Verify records
   survive a service restart before inviting public contributions.
7. Keep synthetic presentation data separate. Never import demo records into
   the shared collection database or publish the live database to GitHub.

## Operations

Use one server instance while SQLite and photos share the disk. Monitor storage
capacity and copy consistent SQLite backups using its online backup API to a
private location outside the service. Persistent storage alone is not a backup.
Record review corrections instead of deleting original observations. Voucher
stock must contain legitimately obtained codes; administrator approval does not
create a retailer partnership or fund a voucher. Raw research exports remain
restricted to administrators and are separate from consent-filtered aggregates.

Publishing this configuration does not create an online service. A Render account
connection and approval of the paid resources are still required.
