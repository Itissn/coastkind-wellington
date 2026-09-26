"""Administrator research summaries and privacy-scoped dataset downloads."""
from collections import Counter
import csv
from datetime import datetime, timezone
import io
import json
from urllib.parse import parse_qs


ANALYSIS_STATUSES = {"pending", "processing", "analyzed", "failed", "duplicate"}
REVIEW_STATUSES = {"pending", "approved", "rejected"}
NOTICE = ("Community observations for research and triage. AI suggestions and unreviewed reports are not verified "
          "pollution findings or water-safety advice. Only export_eligible records have passed the human-review "
          "and quality checks. This download includes the selected raw research records, including ineligible "
          "records, and is not a licensed aggregate dataset or a government submission.")


def parse_filters(query, communities, allow_format=False):
    try:
        params = parse_qs(query, keep_blank_values=True, max_num_fields=8, strict_parsing=True) if query else {}
    except ValueError:
        raise ValueError("Invalid dataset filter parameters.") from None
    allowed = {"community", "review", "status"} | ({"format"} if allow_format else set())
    if set(params) - allowed or any(len(values) != 1 for values in params.values()):
        raise ValueError("Use one value for each supported dataset filter.")
    filters = {key: params.get(key, [""])[0] for key in ("community", "review", "status")}
    valid_values = {"community": communities, "review": REVIEW_STATUSES, "status": ANALYSIS_STATUSES}
    for key, value in filters.items():
        if value and value not in valid_values[key]:
            raise ValueError(f"Choose a valid {key} filter.")
    format_name = params.get("format", ["json"])[0]
    if allow_format and format_name not in {"csv", "json"}:
        raise ValueError("Choose csv or json for the download format.")
    return filters, format_name


def _where(filters):
    columns = {"community": "community", "review": "review_status", "status": "status"}
    selected = [(columns[key], filters.get(key)) for key in columns if filters.get(key)]
    return (" WHERE " + " AND ".join(f"{column}=?" for column, _ in selected) if selected else ""), [value for _, value in selected]


def dataset_payload(store, filters=None):
    # Local import avoids a module cycle: manage imports Store from server.
    from manage import export_blocks

    filters = filters or {"community": "", "review": "", "status": ""}
    where, parameters = _where(filters)
    observations = []
    contributor_ids = set()
    with store.connect() as db:
        db.execute("BEGIN")
        # Research summaries never need image blobs or precise coordinate values.
        rows = db.execute("""SELECT id,community,feelings,hint,created,observed_at,status,analysis,model,analyzed_at,
            quality_flags,duplicate_of,review_status,user_id,rewards_waived,dataset_consent,consent_version,
            schema_version,prompt_version,latitude IS NOT NULL AS has_device_location
            FROM observations""" + where + " ORDER BY created DESC,id", parameters).fetchall()
        for row in rows:
            analysis = json.loads(row["analysis"]) if row["analysis"] else None
            history = [dict(item) for item in db.execute("""SELECT decision,reviewer,note,created
                FROM reviews WHERE observation_id=? ORDER BY id""", (row["id"],))]
            flags = json.loads(row["quality_flags"])
            synthetic = row["schema_version"].startswith(("demo-", "synthetic-demo"))
            blocks = export_blocks(row)
            review_blocks = list(blocks)
            if row["review_status"] != "approved" or not history or history[-1]["decision"] != "approved":
                blocks.insert(0, "Human approval is required")
            if synthetic:
                blocks.insert(0, "Synthetic demonstration record")
                review_blocks.insert(0, "Synthetic demonstration record")
            user = db.execute("SELECT display_name FROM users WHERE id=?", (row["user_id"],)).fetchone() if row["user_id"] else None
            if row["user_id"]:
                contributor_ids.add(row["user_id"])
            points = db.execute("SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE observation_id=?", (row["id"],)).fetchone()[0]
            observations.append({
                "id": row["id"], "community": row["community"], "feelings": row["feelings"], "hint": row["hint"],
                "created": row["created"], "observed_at": row["observed_at"], "status": row["status"],
                "analysis": analysis, "model": row["model"], "analyzed_at": row["analyzed_at"],
                "quality_flags": flags, "duplicate_of": row["duplicate_of"], "review_status": row["review_status"],
                "author_name": user["display_name"] if user else "Guest contributor", "rewards_waived": bool(row["rewards_waived"]),
                "contributor_type": "account" if row["user_id"] else "guest", "points_awarded": points,
                "dataset_consent": bool(row["dataset_consent"]), "consent_version": row["consent_version"],
                "schema_version": row["schema_version"], "prompt_version": row["prompt_version"],
                "activity": (analysis or {}).get("activity", "unknown"), "synthetic": synthetic,
                "photo": f'/api/observations/{row["id"]}/photo', "review_history": history,
                "export_eligible": not blocks, "export_blocks": blocks, "review_blocks": review_blocks,
                "location_precision": "approximate_device_location" if row["has_device_location"] else "community_only",
            })
    categories = Counter(category for row in observations for category in (row["analysis"] or {}).get("pollution_types", []))
    months = {}
    for row in observations:
        month = (row["observed_at"] or row["created"])[:7]
        item = months.setdefault(month, {"month": month, "observations": 0, "concerns": 0, "curated": 0})
        item["observations"] += 1
        item["concerns"] += int((row["analysis"] or {}).get("observation_type") == "concern" or row["hint"] == "concern")
        item["curated"] += int(row["export_eligible"])
    selected_ids = {row["id"] for row in observations}
    # Area signals have their own historical scope; never imply filters recomputed their evidence.
    concerns = [item for item in store.concerns() if selected_ids.intersection(item["report_ids"])]
    concern_ids = {item["id"] for item in concerns}
    alerts = [item for item in store.alerts() if item["id"] in concern_ids]
    summary = {
        "observations": len(observations), "users": len(contributor_ids),
        "guest_observations": sum(row["contributor_type"] == "guest" for row in observations),
        "duplicates": sum(bool(row["duplicate_of"]) for row in observations),
        "communities": dict(Counter(row["community"] for row in observations)),
        "analysis_status": dict(Counter(row["status"] for row in observations)),
        "review_status": dict(Counter(row["review_status"] for row in observations)),
        "activities": dict(Counter(row["activity"] for row in observations)), "categories": dict(categories),
        "points_awarded": sum(row["points_awarded"] for row in observations),
        "curated_observations": sum(row["export_eligible"] for row in observations),
        "unverified_observations": sum(not row["export_eligible"] for row in observations),
        "submitted_from": min((row["created"] for row in observations), default=None),
        "submitted_to": max((row["created"] for row in observations), default=None),
        "alerts": len(alerts), "monthly_trends": [months[key] for key in sorted(months)],
    }
    return {"synthetic": bool(observations) and all(row["synthetic"] for row in observations),
            "dataset_kind": "community-research-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "notice": NOTICE, "filters": dict(filters), "summary": summary, "observations": observations,
            "alerts": alerts, "concerns": concerns,
            "signal_scope": "Historical area/category signals related to selected records; signals are not recomputed from the filtered selection."}


CSV_FIELDS = ("id", "community", "created", "observed_at", "feelings", "hint", "author_name", "contributor_type",
              "status", "review_status", "export_eligible", "export_blocks", "quality_flags", "duplicate_of",
              "activity", "analysis", "model", "analyzed_at", "review_history", "schema_version", "prompt_version",
              "dataset_consent", "consent_version", "rewards_waived", "points_awarded", "synthetic", "location_precision")


def csv_cell(value):
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif value is None:
        value = ""
    elif isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = str(value)
    # Excel can execute formulas after leading whitespace or control characters.
    offset = 0
    while offset < len(value) and (value[offset].isspace() or ord(value[offset]) < 32 or value[offset] == "\ufeff"):
        offset += 1
    stripped = value[offset:]
    if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        value = "'" + value
    return value


def export_bytes(payload, format_name):
    if format_name == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8"
    if format_name != "csv":
        raise ValueError("Choose csv or json for the download format.")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in payload["observations"]:
        writer.writerow({key: csv_cell(row.get(key)) for key in CSV_FIELDS})
    return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
