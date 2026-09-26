"""Conservative map signals from recent community evidence, never water-safety ratings."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json


WINDOW_DAYS = 30
HARD_QUALITY_FLAGS = {"low_resolution", "imprecise_gps", "location_far_from_community"}
NOTICE = ("Colours summarise recent community observations, not measured water quality, verified pollution "
          "severity or swimming safety. Check official advice before entering the water.")


def _timestamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _is_synthetic(row):
    return row["schema_version"].startswith(("demo-", "synthetic-demo"))


def generate(store, now=None, synthetic=False):
    """Real signals always use current time; explicitly synthetic previews use dataset time."""
    from manage import export_blocks
    from server import COMMUNITIES

    current = datetime.now(timezone.utc) if now is None else _timestamp(now)
    if current is None:
        raise ValueError("Community signal time must include a timezone.")
    with store.connect() as db:
        rows = db.execute("""SELECT o.id,o.community,o.hint,o.created,o.observed_at,o.status,o.analysis,
            o.quality_flags,o.duplicate_of,o.review_status,o.image_hash,o.user_id,o.schema_version,
            (SELECT decision FROM reviews r WHERE r.observation_id=o.id ORDER BY r.id DESC LIMIT 1) AS latest_review
            FROM observations o ORDER BY o.created,o.id""").fetchall()
    rows = [row for row in rows if _is_synthetic(row) == bool(synthetic)]
    as_of = current
    if synthetic:
        valid_dates = [_timestamp(row["observed_at"] or row["created"]) for row in rows]
        valid_dates = [value for value in valid_dates if value is not None and value <= current]
        if valid_dates:
            as_of = max(valid_dates)
    earliest = as_of - timedelta(days=WINDOW_DAYS)
    buckets = {}
    for community in sorted(COMMUNITIES):
        buckets[community] = {"counts": {
            "recent_observations": 0, "usable_observations": 0, "concern_observations": 0,
            "reviewed_positive_observations": 0, "unresolved_observations": 0, "excluded_observations": 0,
            "repeated_concern_photos": 0, "repeated_concern_accounts": 0,
        }, "seen_photos": set(), "concern_categories": defaultdict(list)}
    for row in rows:
        if row["community"] not in buckets:
            continue
        # A malformed/future photo date is not replaced with a plausible upload date.
        observed = _timestamp(row["observed_at"]) if row["observed_at"] else None
        event_time = observed if row["observed_at"] else _timestamp(row["created"])
        if event_time is None or not earliest <= event_time <= as_of:
            continue
        bucket = buckets[row["community"]]
        counts = bucket["counts"]
        counts["recent_observations"] += 1
        try:
            flags = json.loads(row["quality_flags"])
            analysis = json.loads(row["analysis"]) if row["analysis"] else None
            if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                raise ValueError()
            if analysis is not None and not isinstance(analysis, dict):
                raise ValueError()
        except (ValueError, TypeError):
            counts["excluded_observations"] += 1
            continue
        excluded = (row["review_status"] == "rejected" or row["duplicate_of"] or row["status"] == "duplicate"
                    or "duplicate_photo" in flags or HARD_QUALITY_FLAGS.intersection(flags)
                    or (analysis is not None and analysis.get("image_relevant") is False)
                    or not row["image_hash"] or row["image_hash"] in bucket["seen_photos"])
        if excluded:
            counts["excluded_observations"] += 1
            continue
        bucket["seen_photos"].add(row["image_hash"])
        counts["usable_observations"] += 1
        analysis = analysis or {}
        concern = row["hint"] == "concern" or analysis.get("observation_type") == "concern"
        counts["concern_observations"] += int(concern)
        try:
            eligible = (row["review_status"] == "approved" and row["latest_review"] == "approved"
                        and observed is not None and not export_blocks(row))
        except (ValueError, KeyError, TypeError):
            eligible = False
        positive = (eligible and not concern and analysis.get("observation_type") == "moment"
                    and analysis.get("sentiment") == "positive" and not analysis.get("pollution_types"))
        counts["reviewed_positive_observations"] += int(positive)
        if concern or not eligible:
            counts["unresolved_observations"] += 1
        if eligible and concern and row["user_id"]:
            categories = analysis.get("pollution_types") or ["unclassified"]
            if not isinstance(categories, list) or any(not isinstance(category, str) for category in categories):
                categories = ["unclassified"]
            for category in set(categories):
                bucket["concern_categories"][category].append((row["image_hash"], row["user_id"], observed))
    results = []
    for community, bucket in buckets.items():
        counts = bucket["counts"]
        repeated = []
        for evidence in bucket["concern_categories"].values():
            photos = len({item[0] for item in evidence})
            accounts = len({item[1] for item in evidence})
            dates = [item[2] for item in evidence]
            if photos >= 3 and accounts >= 2 and max(dates) - min(dates) >= timedelta(days=7):
                repeated.append((photos, accounts))
        if repeated:
            photos, accounts = max(repeated)
            counts["repeated_concern_photos"], counts["repeated_concern_accounts"] = photos, accounts
            status, label = "red", "Repeated concerns"
            detail = (f"{photos} reviewed, distinct concern photos from {accounts} accounts in the same category "
                      f"span at least 7 days within the last {WINDOW_DAYS} days. This is not a pollution severity rating.")
        elif counts["unresolved_observations"]:
            status, label = "orange", "Needs review"
            detail = ("Recent concerns or incomplete observations need review. Community reports alone do not "
                      "establish pollution or water safety.")
        elif counts["reviewed_positive_observations"]:
            status, label = "green", "Positive observations"
            detail = ("Recent reviewed positive observations, with no other usable unresolved reports in this window. "
                      "Positive experiences do not establish safe water.")
        else:
            status, label = "gray", "Not enough evidence"
            detail = (f"No qualifying recent observations in the last {WINDOW_DAYS} days. Rejected, duplicate or "
                      "unusable records do not establish current conditions.")
        if synthetic:
            detail = "Synthetic demo. " + detail
        results.append({"community": community, "status": status, "label": label, "detail": detail, "counts": counts})
    return {"basis": "community_observations", "window_days": WINDOW_DAYS, "as_of": as_of.isoformat(),
            "synthetic": bool(synthetic), "notice": ("Synthetic demo using the dataset's latest valid observation date. " if synthetic else "") + NOTICE,
            "communities": results}
