"""Generate isolated, explicitly synthetic Coastkind demonstration data.

This program never calls an AI provider, writes the live database, or creates
real rewards. Run ``python seed_demo.py`` and browse the separate demo server.
"""
import argparse
import base64
from collections import Counter
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from PIL import Image, ImageDraw, ImageFont

import manage
from server import ROOT, COMMUNITY_POINTS, Store, validate_analysis, validate_observation


DATASET_KIND = "synthetic-demo-v1"
MODEL = "demo-simulated-v1"
SCHEMA = "demo-coastal-observation-v1"
PROMPT = "demo-evidence-separated-v1"
DEFAULT_OUTPUT = ROOT / "data" / "demo"
SENTINEL = ".coastkind-demo.json"
DB_NAME = "coastkind-demo.sqlite3"
# The versioned fixture remains five communities even as the live map expands.
COMMUNITIES = ("Oriental Bay", "Lyall Bay", "Island Bay", "Porirua Harbour", "Petone Beach")
CATEGORIES = ("litter", "suspected_industrial", "oil_or_fuel", "suspected_wastewater", "unusual_water", "other")
ACTIVITIES = ("swimming", "recreational_fishing", "paddling", "boating", "commercial_fishing", "conservation")
NOTICE = ("DEMO / SYNTHETIC: Every person, observation, image, analysis, review, point and alert in this "
          "dataset is invented. These are workflow examples, not evidence about Wellington water quality, "
          "AI accuracy, real pollution, government savings, or redeemable rewards. Nothing was sent to government.")
REPORTS = {
    "litter": "I noticed several wrappers and bottles near the shoreline after my visit.",
    "suspected_industrial": "I noticed an unusual coloured patch near the harbour. I wondered about industrial discharge, but do not know its source.",
    "oil_or_fuel": "I noticed a rainbow-like sheen while boating. I wondered about fuel, but cannot identify the substance.",
    "suspected_wastewater": "I noticed a strong odour near an outfall. I wondered about wastewater, but this is an unverified impression.",
    "unusual_water": "The water looked unusually cloudy to me today. I do not know whether weather, sediment or another cause explains it.",
    "other": "I noticed discarded fishing line near the rocks and want the community to keep an eye on this area.",
}
FEATURES = {
    "litter": "schematic rectangles representing reported litter",
    "suspected_industrial": "an invented coloured patch with no identifiable source",
    "oil_or_fuel": "an invented surface sheen, without chemical identification",
    "suspected_wastewater": "an invented outfall symbol; odour is reported text, not visible evidence",
    "unusual_water": "an invented cloudy-water patch",
    "other": "an invented loop representing discarded fishing line",
}


def identifier(kind, value):
    return str(uuid5(NAMESPACE_URL, f"coastkind/{DATASET_KIND}/{kind}/{value}"))


def _read_metadata(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("The demo database must be a regular file.")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            metadata = dict(db.execute("SELECT key,value FROM demo_metadata"))
    except sqlite3.Error as error:
        raise ValueError("Refusing a database without the synthetic demo metadata marker.") from error
    if metadata.get("dataset_kind") != DATASET_KIND:
        raise ValueError("Refusing a database that is not this synthetic demo dataset.")
    return metadata


def _prepare_directory(output_dir):
    requested = Path(output_dir).expanduser()
    output = requested.resolve()
    data_root = (ROOT / "data").resolve()
    try:
        relative = output.relative_to(data_root)
    except ValueError as error:
        raise ValueError("Demo output must stay inside this workspace's data/demo or data/demo-* directory.") from error
    if not relative.parts or not (relative.parts[0] == "demo" or relative.parts[0].startswith("demo-")):
        raise ValueError("Use data/demo or a data/demo-* directory; the live database is never a demo target.")
    if output.exists() and not output.is_dir():
        raise ValueError("Choose a demo output directory, not a file.")
    expected = (SENTINEL, DB_NAME, "observations.csv", "dataset.json", "summary.json", "README.md", "CODEBOOK.md")
    for name in expected:
        if (output / name).is_symlink():
            raise ValueError("Demo output files cannot be symbolic links.")
    marker = output / SENTINEL
    if output.exists() and any(output.iterdir()):
        if not marker.is_file():
            raise ValueError("Refusing a nonempty directory without a synthetic demo marker.")
        try:
            sentinel = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Invalid demo directory marker.") from error
        if sentinel.get("dataset_kind") != DATASET_KIND:
            raise ValueError("This directory is not marked as the expected synthetic demo dataset.")
    if (output / DB_NAME).exists():
        _read_metadata(output / DB_NAME)
    output.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(json.dumps({"dataset_kind": DATASET_KIND, "synthetic": True}), encoding="utf-8")
    return output


@lru_cache(maxsize=16)
def _font(size):
    for name in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _placeholder(community_index, scenario_index, low_resolution=False):
    """An obvious geometric test fixture, never a simulated real photograph."""
    size = (300, 220) if low_resolution else (900, 600)
    image = Image.new("RGB", size, "#e5f1ed")
    draw = ImageDraw.Draw(image)
    width, height = size
    scale = width / 900
    draw.rectangle((0, int(height * .40), width, height), fill="#c9dfeb")
    for line in range(5):
        y = int(height * .46 + line * height * .095)
        draw.line((0, y, width, y), fill="#83a6ba", width=max(1, int(3 * scale)))
    # Unique fixture identifiers make all photos distinct except deliberate duplicates.
    hue = (40 + scenario_index * 4, 95 + community_index * 20, 120 + scenario_index * 2)
    draw.rectangle((int(width * .70), int(height * .48), int(width * .78), int(height * .61)), fill=hue)
    draw.rectangle((0, 0, width, int(height * .27)), fill="#164843")
    draw.text((int(25 * scale), int(16 * scale)), "DEMO / SYNTHETIC", fill="white", font=_font(max(16, int(48 * scale))))
    draw.text((int(25 * scale), int(78 * scale)), "NOT PHOTO EVIDENCE", fill="white", font=_font(max(12, int(30 * scale))))
    draw.rectangle((0, int(height * .78), width, height), fill="#fff4da")
    lines = (f"FICTIONAL FIXTURE {community_index + 1:02d}-{scenario_index + 1:02d}",
             COMMUNITIES[community_index], "For workflow testing only")
    for index, text in enumerate(lines):
        draw.text((int(20 * scale), int(height * .79) + index * max(12, int(31 * scale))), text,
                  fill="#374743", font=_font(max(10, int(24 * scale))))
    result = io.BytesIO()
    image.save(result, format="PNG")
    return "data:image/png;base64," + base64.b64encode(result.getvalue()).decode("ascii")


def _scenario(community_index, index):
    activity = ACTIVITIES[(community_index + index) % len(ACTIVITIES)]
    scenario = {"scenario_id": f"C{community_index + 1:02d}-S{index + 1:02d}", "scenario_type": "concern_followup",
                "activity": activity, "kind": "concern", "category": None, "status": "analyzed", "review": "pending",
                "defect": None, "duplicate": False, "guest": False, "sentiment": "negative", "offset": 0}
    if index < 36:
        category_index, repetition = divmod(index, 6)
        scenario["category"] = CATEGORIES[category_index]
        scenario["offset"] = (0, 10, 21, 34, 46, 57)[repetition] + category_index // 3
        scenario["guest"] = repetition == 3
        if repetition < 3:
            scenario["review"] = "approved"
            scenario["scenario_type"] = "repeated_concern_quality_approved"
        elif repetition == 4:
            scenario["status"] = "pending" if category_index % 2 == 0 else "failed"
            scenario["scenario_type"] = "analysis_pending" if scenario["status"] == "pending" else "analysis_failure"
        elif repetition == 5:
            defects = ("duplicate_photo", "low_resolution", "imprecise_gps", "text_image_conflict", "analysis_failure", "missing_photo_date")
            scenario["defect"] = defects[category_index]
            scenario["scenario_type"] = defects[category_index]
            if category_index == 0:
                scenario["status"], scenario["duplicate"] = "duplicate", True
            elif category_index in (1, 2, 3):
                scenario["review"] = "rejected"
            elif category_index == 4:
                scenario["status"] = "failed"
    elif index < 44:
        scenario.update(kind="moment", scenario_type="positive_coastal_experience", sentiment="positive", offset=(index - 36) * 8)
        scenario["guest"] = index in (38, 41) or (index == 43 and community_index < 3)
        if index in (36, 37, 38, 39, 43):
            scenario["review"] = "approved"
        elif index == 41:
            scenario["status"] = "pending"
        elif index == 42:
            scenario["status"] = "failed"
    else:
        defect = ("low_resolution", "imprecise_gps", "text_image_conflict", "missing_photo_date")[index - 44]
        scenario.update(kind="uncertain", scenario_type=f"uncertain_{defect}", sentiment="unknown" if index % 2 else "neutral",
                        defect=defect, offset=56 + index - 44, guest=index == 47)
        if index in (44, 46):
            scenario["review"] = "rejected"
    return scenario


def _words(scenario):
    if scenario["kind"] == "moment":
        story = "I enjoyed my time by the water. It looked clear to me and I felt refreshed. This personal impression does not establish water safety."
    elif scenario["kind"] == "uncertain":
        story = "I could not tell what the patch in the water was. I am sharing an uncertain observation for follow-up, without claiming pollution."
    else:
        story = REPORTS[scenario["category"]]
    return f"DEMO / SYNTHETIC {scenario['scenario_id']}: Fictional {scenario['activity'].replace('_', ' ')} experience. {story} This event never occurred."


def _analysis(scenario, words):
    feature = FEATURES.get(scenario["category"], "a geometric blue-water placeholder without environmental evidence")
    return validate_analysis({
        "summary": f"DEMO / SYNTHETIC: Scripted {scenario['kind']} example for {scenario['activity']}; not an AI prediction or environmental finding.",
        "observation_type": scenario["kind"],
        "pollution_types": [scenario["category"]] if scenario["category"] else [],
        "visible_evidence": [f"DEMO fixture only: {feature}. No real photograph was analyzed."],
        "reported_experience": words,
        "sentiment": scenario["sentiment"], "activity": scenario["activity"],
        "image_relevant": scenario["kind"] != "uncertain",
        "text_image_consistency": "conflicting" if scenario["defect"] == "text_image_conflict" else "uncertain" if scenario["kind"] == "uncertain" else "consistent",
        "uncertainties": ["DEMO: All fields are scripted scenario labels, not model outputs or verified observations.",
                          "Water safety, chemical composition, pathogens and pollution source cannot be established by this fixture."],
        "needs_human_review": True,
    })


def _populate(store, metadata):
    start = datetime.fromisoformat(metadata["period_start"])
    users = [identifier("user", i) for i in range(30)]
    with store.connect() as db:
        for i, user_id in enumerate(users):
            db.execute("INSERT OR IGNORE INTO users(id,email,display_name,password_hash,created_at) VALUES (?,?,?,?,?)",
                       (user_id, f"demo-{i + 1:02d}@example.test", f"Demo Coastal Member {i + 1:02d}",
                        "!disabled-synthetic-account-no-login!", start.isoformat()))
    for community_index, community in enumerate(COMMUNITIES):
        for index in range(48):
            scenario = _scenario(community_index, index)
            record_id = identifier("observation", scenario["scenario_id"])
            submitted = start + timedelta(days=scenario["offset"], minutes=community_index * 10 + index)
            timestamp = submitted.isoformat()
            owner = None if scenario["guest"] else users[(community_index * 7 + index) % len(users)]
            words = _words(scenario)
            with store.connect() as db:
                exists = db.execute("SELECT 1 FROM observations WHERE id=?", (record_id,)).fetchone()
            if not exists:
                latitude, longitude = COMMUNITY_POINTS[community]
                position = {"latitude": latitude + .0001 * (index % 5), "longitude": longitude + .0001 * (index % 7),
                            "accuracy": 1500 if scenario["defect"] == "imprecise_gps" else 18 + index % 12}
                # Some valid records intentionally provide community only, as the live form allows.
                if index % 11 == 0 and scenario["defect"] != "imprecise_gps":
                    position = None
                photo_index = 0 if scenario["duplicate"] else index
                data = validate_observation({"id": record_id, "community": community, "feelings": words,
                    "hint": scenario["kind"] if scenario["kind"] != "uncertain" else "observation",
                    "photo": _placeholder(community_index, photo_index, scenario["defect"] == "low_resolution"),
                    "observed_at": None if scenario["defect"] == "missing_photo_date" else (submitted - timedelta(minutes=15)).isoformat(),
                    "position": position, "location_confirmed": position is not None, "dataset_consent": index % 3 != 1})
                data.update(user_id=owner, contributor_hash=hashlib.sha256(owner.encode()).hexdigest() if owner else None,
                            rewards_waived=scenario["guest"])
                data["quality_flags"].append("synthetic_demo")
                store.insert(data)
                analysis = _analysis(scenario, words) if scenario["status"] == "analyzed" else None
                with store.connect() as db:
                    db.execute("""UPDATE observations SET created=?,status=?,analysis=?,model=?,schema_version=?,prompt_version=?,
                        analyzed_at=?,analysis_error=?,consent_at=? WHERE id=?""",
                        (timestamp, scenario["status"], json.dumps(analysis) if analysis else None, MODEL, SCHEMA, PROMPT,
                         (submitted + timedelta(minutes=2)).isoformat() if analysis else None,
                         "DEMO: Simulated provider failure. No AI provider was contacted." if scenario["status"] == "failed" else None,
                         timestamp if data["dataset_consent"] else None, record_id))
            if scenario["review"] in ("approved", "rejected"):
                with store.connect() as db:
                    reviewed = db.execute("SELECT 1 FROM reviews WHERE observation_id=?", (record_id,)).fetchone()
                if not reviewed:
                    manage.review(store, record_id, scenario["review"], "DEMO scripted reviewer",
                                  "DEMO / SYNTHETIC: Scripted workflow decision on invented evidence; not an environmental verification.")
                    with store.connect() as db:
                        reviewed_at = (submitted + timedelta(hours=2)).isoformat()
                        db.execute("UPDATE reviews SET created=? WHERE observation_id=?", (reviewed_at, record_id))
                        db.execute("UPDATE points_ledger SET created=?,reason='DEMO: Simulated quality contribution approved; no redeemable value' WHERE observation_id=?",
                                   (reviewed_at, record_id))
            if index < 36 and index % 6 < 4:
                with store.connect() as db:
                    for number in range(2):
                        commenter = users[(community_index * 7 + index + number + 4) % len(users)]
                        body = f"DEMO / SYNTHETIC follow-up {number + 1}: Fictional community member asks for another dated observation. This is not independent real-world corroboration."
                        if not db.execute("SELECT 1 FROM comments WHERE observation_id=? AND user_id=?", (record_id, commenter)).fetchone():
                            db.execute("INSERT INTO comments(observation_id,author,body,created,user_id) VALUES (?,?,?,?,?)",
                                       (record_id, f"Demo Coastal Member {users.index(commenter) + 1:02d}", body,
                                        (submitted + timedelta(hours=3, minutes=number)).isoformat(), commenter))
                    for number in range(3):
                        supporter = users[(community_index * 7 + index + number + 1) % len(users)]
                        db.execute("INSERT OR IGNORE INTO support(observation_id,client_id,user_id) VALUES (?,?,?)", (record_id, supporter, supporter))
    store.refresh_alerts()
    with store.connect() as db:
        for row in db.execute("SELECT id,evidence FROM alerts").fetchall():
            evidence = json.loads(row["evidence"])
            evidence.update(synthetic=True, dataset_kind=DATASET_KIND, notice="DEMO attention signal from fictional reports; not a real pollution alert.")
            db.execute("UPDATE alerts SET evidence=?,rule_version=? WHERE id=?", (json.dumps(evidence), "demo-3-photos-2-accounts-7-days-v1", row["id"]))
        db.execute("UPDATE alert_events SET operator='DEMO scenario generator',note='DEMO / SYNTHETIC: Scripted attention signal; not verified pollution and never sent to government.'")
        db.execute("INSERT OR REPLACE INTO demo_metadata(key,value) VALUES ('generation_complete','true')")


def _export(store, output, metadata):
    records = []
    with store.connect() as db:
        rows = db.execute("SELECT * FROM observations ORDER BY community,created,id").fetchall()
        scenarios = {identifier("observation", _scenario(c, i)["scenario_id"]): _scenario(c, i) for c in range(5) for i in range(48)}
        for row in rows:
            if (row["id"] not in scenarios or row["model"] != MODEL or row["schema_version"] != SCHEMA
                    or row["prompt_version"] != PROMPT or not row["feelings"].startswith("DEMO / SYNTHETIC")):
                raise ValueError("Refusing to label unexpected or unmarked database rows as synthetic demo data.")
            scenario = scenarios[row["id"]]
            analysis = json.loads(row["analysis"]) if row["analysis"] else None
            points = db.execute("SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE observation_id=?", (row["id"],)).fetchone()[0]
            audit = [dict(r) for r in db.execute("SELECT decision,reviewer,note,created FROM reviews WHERE observation_id=? ORDER BY id", (row["id"],))]
            comments = [dict(r) for r in db.execute("SELECT author,body,created FROM comments WHERE observation_id=? ORDER BY id", (row["id"],))]
            records.append({"id": row["id"], "synthetic": True, "scenario_id": scenario["scenario_id"], "scenario_type": scenario["scenario_type"],
                "community": row["community"], "submitted_at": row["created"], "observed_at": row["observed_at"], "activity": scenario["activity"],
                "contributor_type": "guest" if row["rewards_waived"] else "registered", "fictional_user_id": row["user_id"],
                "rewards_waived": bool(row["rewards_waived"]), "feelings": row["feelings"], "observation_type": analysis["observation_type"] if analysis else None,
                "pollution_types": analysis["pollution_types"] if analysis else [], "sentiment": analysis["sentiment"] if analysis else None,
                "analysis_status": row["status"], "review_status": row["review_status"], "quality_flags": json.loads(row["quality_flags"]),
                "duplicate_of": row["duplicate_of"], "dataset_consent": bool(row["dataset_consent"]), "points_awarded": points,
                "simulated_analysis": bool(analysis), "analysis": analysis, "analysis_error": row["analysis_error"], "model": row["model"],
                "schema_version": row["schema_version"], "prompt_version": row["prompt_version"], "reviews": audit, "comments": comments,
                "support_count": db.execute("SELECT COUNT(*) FROM support WHERE observation_id=?", (row["id"],)).fetchone()[0],
                "location_precision": "community_only" if row["latitude"] is None else "imprecise_device_location" if "imprecise_gps" in json.loads(row["quality_flags"]) else "approximate_device_location",
                "scenario_category": scenario["category"], "scenario_observation_type": scenario["kind"], "scenario_sentiment": scenario["sentiment"]})
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("observations", "users", "comments", "support", "reviews", "points_ledger", "alerts", "sessions", "reward_vouchers", "reward_redemptions")}
        counts.update(communities=len({r["community"] for r in records}), guest_observations=sum(r["rewards_waived"] for r in records),
                      points=sum(r["points_awarded"] for r in records))
        problems = db.execute("PRAGMA foreign_key_check").fetchall()
        if problems or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The demo database failed its integrity check.")
    summaries = store.concerns()
    for item in summaries:
        item.update(synthetic=True, dataset_kind=DATASET_KIND)
    alerts = store.alerts()
    for item in alerts:
        item.update(synthetic=True, dataset_kind=DATASET_KIND)
    files = {"database": str(store.path), "csv": str(output / "observations.csv"), "json": str(output / "dataset.json"),
             "summary": str(output / "summary.json"), "readme": str(output / "README.md"), "codebook": str(output / "CODEBOOK.md")}
    summary = {"dataset_kind": DATASET_KIND, "synthetic": True, "created_at": metadata["created_at"], "db_path": str(store.path),
        "counts": counts, "by_community": dict(Counter(r["community"] for r in records)), "analysis_status": dict(Counter(r["analysis_status"] for r in records)),
        "review_status": dict(Counter(r["review_status"] for r in records)), "quality_flags": dict(Counter(flag for r in records for flag in r["quality_flags"])),
        "period": {"start": min(r["submitted_at"] for r in records), "end": max(r["submitted_at"] for r in records), "calendar_days": 60},
        "coverage": {"activities": dict(Counter(r["activity"] for r in records)), "scenario_categories": dict(Counter(r["scenario_category"] or "none" for r in records)),
            "scenario_observation_types": dict(Counter(r["scenario_observation_type"] for r in records)),
            "scenario_sentiments": dict(Counter(r["scenario_sentiment"] for r in records)), "dataset_consent": dict(Counter(str(r["dataset_consent"]).lower() for r in records))},
        "files": files, "notice": NOTICE}
    flat_fields = ["id", "synthetic", "scenario_id", "scenario_type", "community", "submitted_at", "observed_at", "activity", "contributor_type", "fictional_user_id",
        "rewards_waived", "feelings", "pollution_types", *[f"category_{name}" for name in CATEGORIES], "observation_type", "sentiment", "analysis_status", "review_status",
        "quality_flags", "duplicate_of", "dataset_consent", "points_awarded", "simulated_analysis", "model", "schema_version", "prompt_version", "location_precision",
        "support_count", "scenario_category", "scenario_observation_type", "scenario_sentiment"]
    with (output / "observations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=flat_fields)
        writer.writeheader()
        for record in records:
            flat = {key: record.get(key) for key in flat_fields}
            flat["pollution_types"] = ";".join(record["pollution_types"])
            flat["quality_flags"] = ";".join(record["quality_flags"])
            for category in CATEGORIES:
                flat[f"category_{category}"] = int(category in record["pollution_types"]) if record["analysis"] else None
            for key in ("synthetic", "rewards_waived", "dataset_consent", "simulated_analysis"):
                flat[key] = int(bool(flat[key]))
            writer.writerow(flat)
    (output / "dataset.json").write_text(json.dumps({**summary, "records": records, "concerns": summaries, "alerts": alerts}, indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "README.md").write_text(_README, encoding="utf-8")
    (output / "CODEBOOK.md").write_text(_CODEBOOK, encoding="utf-8")
    return summary


_README = """# Coastkind synthetic demonstration dataset

**DEMO: All observations, users, images, analyses, reviews, alerts and points are fictional.**
This collection makes no claim about real water quality in any named Wellington community.
It contains 240 invented observations (48 per community), 30 disabled fictional accounts,
six activity types and six example concern categories over a 60-day scenario period.

Files:

- `coastkind-demo.sqlite3`: isolated SQLite database; open with a SQLite viewer.
- `observations.csv`: UTF-8 with BOM, one observation per row; suitable for Excel, pandas or BI tools.
- `dataset.json`: structured observations, simulated analyses, quality flags, reviews, comments and attention signals.
- `summary.json`: counts and scenario coverage.
- `CODEBOOK.md`: field definitions and interpretation rules.

Run `python seed_demo.py` in the project directory to regenerate exports. Repeated runs preserve
the same IDs and do not append records. The generator only accepts an explicitly marked demo
directory inside `data/demo` or `data/demo-*`. It does not modify `data/coastkind.sqlite3`.

For the separate local demonstration viewer, run `python demo_server.py` from the project root.
No real API keys, AI requests, user passwords, login sessions or vouchers are created.
The schematic watermarked images are test fixtures, not photographs or pollution evidence.
Guest uploads waive rewards permanently; only eligible, simulated approved account records
receive fictional points. Fictional points have no monetary or redemption value.

The normal government/licensed export excludes these demo records. Nothing is sold or sent
to a government agency. This dataset is useful for testing storage, charts, filters, quality
gates and workflow demonstrations. It cannot measure AI accuracy, actual pollution prevalence,
independent community support, public health risk or government cost savings.
"""

_CODEBOOK = """# Synthetic demonstration data codebook

Every record has `synthetic=true` and all text identifies its fictional nature. No real
environmental photograph was analyzed. `demo-simulated-v1` means scripted data, never a live model.

## Units and identifiers

One CSV row / JSON record represents one invented upload. UUID5 identifiers are stable within
this scenario version. `scenario_id` and `scenario_type` link each record to its scripted case.
`fictional_user_id` links registered contributions to an invented account; it is blank for guests.
The CSV and JSON omit emails, passwords, sessions, photo binaries, exact coordinates and vouchers.
`submitted_at` and `observed_at` are ISO 8601 UTC timestamps. A missing photo date is null/blank,
never replaced with upload time. The persisted scenario period is 60 calendar days.

## Categories and activities

`community`: Oriental Bay, Lyall Bay, Island Bay, Porirua Harbour or Petone Beach.

`activity`: swimming, recreational_fishing, paddling, boating, commercial_fishing, conservation.

`pollution_types`: litter, suspected_industrial, oil_or_fuel, suspected_wastewater,
unusual_water, other. These are example concern labels, not verified pollutants or their sources.
In CSV, multiple categories use semicolons; corresponding `category_*` indicator columns are
1 or 0 for analyzed records and blank when analysis is unavailable. JSON stores a list.
This version uses one category per analyzed concern; the schema permits multiple labels.

`observation_type`: concern, moment or uncertain. A moment has no pollution categories.
`sentiment`: positive, negative, neutral or unknown. A positive experience is not a water-safety test.
`scenario_category`, `scenario_observation_type` and `scenario_sentiment` are generator labels
available even for failed/pending analyses. Keep these separate from the simulated analysis fields.
They are scenario ground truth only, never scientific ground truth or model-validation labels.

## Missing data and workflow states

CSV blank fields represent null/unavailable; booleans are 1/0. JSON uses native null and booleans.
An empty `pollution_types` list plus no analysis means unknown, not clean water.
`analysis_status`: analyzed, pending, failed or duplicate. `simulated_analysis` is true only when
a scripted analysis object exists. All records retain the demo model/schema/prompt markers.
`review_status`: pending, approved or rejected. All review decisions are explicitly simulated.
`quality_flags` are semicolon-delimited in CSV and arrays in JSON: synthetic_demo,
community_location_only, missing_photo_date, low_resolution, imprecise_gps and duplicate_photo.
Text/image conflict is recorded in `analysis.text_image_consistency`, not a separate photo flag.
An uncertain classification, unrelated image or conflicting text prevents approval.
`duplicate_of` links a deliberate repeated image to its original upload. Do not count it as
independent photographic evidence. A guest record is not evidence of an independent person.

## Quality and rewards

`feelings` contains original invented words. `analysis.visible_evidence` describes fixture
features; `analysis.reported_experience` preserves the invented report. Never treat reported
odour or suggested sources as something established by a photograph. The synthetic image
relevance/consistency fields are scenario inputs, not actual visual-model measurements.

`contributor_type`: registered or guest. All guest rows have `rewards_waived=1`, no account ID
and zero points; they cannot be claimed by a later account. `points_awarded` is the ledger sum.
Only valid simulated approved account observations receive 10 fictional points via the same
review logic as the application. There are no voucher codes, financial values or redemptions.
`dataset_consent` is an invented opt-in example, not permission to use this as real field data.

## Trends and useful analyses

Aggregate by community, activity, category, day and workflow state. Compare missing dates,
duplicate rates, approval rates and pending queues. Sum category indicators carefully: in a
future multilabel dataset their total may exceed record count. Exclude duplicates/rejected
records as appropriate, state denominators, and distinguish uploads from account counts.
Attention signals use at least 3 usable photos, 2 registered accounts and a 7-day span.
All demo signals are fictional and never sent to government. Account counts do not establish
independent people; support clicks are not corroborating environmental evidence.

This balanced scenario sample is intentionally artificial. Do not infer real prevalence,
water safety, AI accuracy, causal effects, merchant participation or government cost savings.
"""


def generate_demo(output_dir=DEFAULT_OUTPUT):
    output = _prepare_directory(output_dir)
    database = output / DB_NAME
    metadata = _read_metadata(database) if database.exists() else None
    store = Store(database)
    if metadata is None:
        current = datetime.now(timezone.utc).replace(microsecond=0)
        start = (current - timedelta(days=60)).replace(hour=8, minute=0, second=0)
        metadata = {"dataset_kind": DATASET_KIND, "created_at": current.isoformat(), "period_start": start.isoformat(),
                    "schema_version": SCHEMA, "model": MODEL, "generation_complete": "false"}
        with store.connect() as db:
            db.execute("CREATE TABLE demo_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.executemany("INSERT INTO demo_metadata(key,value) VALUES (?,?)", metadata.items())
    if metadata.get("generation_complete") != "true":
        _populate(store, metadata)
    return _export(store, output, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="A directory under workspace/data/demo or workspace/data/demo-*.")
    args = parser.parse_args()
    try:
        result = generate_demo(args.out)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Demo generation failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
