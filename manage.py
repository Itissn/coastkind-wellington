"""Local operator review and export. Never called from the public upload API."""
import argparse
import json
from pathlib import Path
from server import ROOT, Store, now, POINTS_PER_APPROVED_OBSERVATION


def export_blocks(row):
    blocks = []
    if row["status"] != "analyzed" or not row["analysis"]:
        blocks.append("AI analysis is not complete")
    if not row["observed_at"]:
        blocks.append("Photo date was not supplied")
    if row["duplicate_of"]:
        blocks.append("Repeated photo: use the original record")
    flags = json.loads(row["quality_flags"])
    if "low_resolution" in flags:
        blocks.append("A higher-resolution photo is needed")
    if "imprecise_gps" in flags or "location_far_from_community" in flags:
        blocks.append("Location needs correction or a replacement observation")
    if row["analysis"]:
        analysis = json.loads(row["analysis"])
        if not analysis["image_relevant"] or analysis["observation_type"] == "uncertain":
            blocks.append("Evidence is unrelated or insufficient")
        if analysis["text_image_consistency"] == "conflicting":
            blocks.append("Photo and text conflict")
    return blocks


def review(store, record_id, decision, reviewer, note):
    if decision not in ("approved", "rejected") or not reviewer.strip() or not note.strip():
        raise ValueError("A decision, reviewer name and review note are required.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM observations WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("Observation not found.")
        blocks = export_blocks(row)
        if decision == "approved" and blocks:
            raise ValueError("Cannot approve: " + "; ".join(blocks))
        db.execute("INSERT INTO reviews(observation_id,decision,reviewer,note,created,analysis_snapshot) VALUES (?,?,?,?,?,?)",
                   (record_id, decision, reviewer.strip(), note.strip(), now(), row["analysis"] or "null"))
        db.execute("UPDATE observations SET review_status=? WHERE id=?", (decision, record_id))
        if row["user_id"] and not row["rewards_waived"]:
            previous_points = db.execute("SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE observation_id=? AND user_id=?", (record_id, row["user_id"])).fetchone()[0]
            target_points = POINTS_PER_APPROVED_OBSERVATION if decision == "approved" else 0
            if target_points != previous_points:
                db.execute("INSERT INTO points_ledger(contributor_hash,observation_id,delta,reason,created,user_id) VALUES (?,?,?,?,?,?)",
                           (row["contributor_hash"] or row["user_id"], record_id, target_points - previous_points,
                            "Quality contribution approved" if decision == "approved" else "Points adjusted after review", now(), row["user_id"]))
    store.refresh_alerts()


def export_records(store, licensed=False):
    records = []
    with store.connect() as db:
        for row in db.execute("SELECT * FROM observations WHERE review_status='approved' ORDER BY observed_at"):
            if row["schema_version"].startswith(("demo-", "synthetic-demo")) or (row["model"] or "").startswith("demo-"):
                continue
            if export_blocks(row):
                continue
            if licensed and not row["dataset_consent"]:
                continue
            audit = db.execute("SELECT reviewer,note,created FROM reviews WHERE observation_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if not audit:
                continue
            records.append({"id": row["id"], "community": row["community"], "observed_at": row["observed_at"],
                            "submitted_at": row["created"], "feelings": row["feelings"],
                            "location_precision": "approximate_device_location" if row["latitude"] is not None else "community_only",
                            "approximate_coordinates": [round(row["latitude"], 2), round(row["longitude"], 2)] if row["latitude"] is not None else None,
                            "image_hash": row["image_hash"], "analysis": json.loads(row["analysis"]),
                            "model": row["model"], "schema_version": row["schema_version"], "prompt_version": row["prompt_version"],
                            "quality_flags": json.loads(row["quality_flags"]), "review": dict(audit)})
    if licensed:
        # No photos, free text, coordinates, identifiers, or reviewer information in licensed output.
        cells = {}
        for record in records:
            for category in set(record["analysis"]["pollution_types"]) or {"no_specific_pollution_category"}:
                key = (record["community"], record["observed_at"][:7], category)
                cells[key] = cells.get(key, 0) + 1
        cells = [{"community": k[0], "month": k[1], "category": k[2], "reviewed_observations": count}
                 for k, count in cells.items() if count >= 5]
        return {"generated_at": now(), "format": "consented-monthly-aggregates-v1", "minimum_cell_size": 5,
                "cells": cells, "notice": "Only explicitly opted-in, human-reviewed observations are aggregated. Small groups are omitted. This is a draft dataset export, not a sale or a guarantee of anonymisation."}
    return {"generated_at": now(), "record_count": len(records), "records": records,
            "notice": "Human-reviewed community observations, not laboratory findings. Not submitted to government."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "coastkind.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("queue")
    commands.add_parser("alerts")
    command = commands.add_parser("acknowledge")
    command.add_argument("id")
    command.add_argument("--operator", required=True)
    command.add_argument("--note", required=True)
    command = commands.add_parser("review")
    command.add_argument("id")
    command.add_argument("--decision", choices=["approved", "rejected"], required=True)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--note", required=True)
    command = commands.add_parser("export")
    command.add_argument("--out", required=True)
    command.add_argument("--licensed", action="store_true", help="Export only consented, reviewed monthly aggregates with cells of at least five observations.")
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == "alerts":
        store.refresh_alerts()
        print(json.dumps(store.alerts(), indent=2))
    elif args.command == "acknowledge":
        if not args.operator.strip() or not args.note.strip():
            parser.error("An operator name and note are required.")
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM alerts WHERE id=? AND status='open'", (args.id,)).fetchone():
                parser.error("Open alert not found.")
            db.execute("UPDATE alerts SET status='acknowledged',updated=? WHERE id=?", (now(), args.id))
            db.execute("INSERT INTO alert_events(alert_id,created,action,operator,note) VALUES (?,?,?,?,?)", (args.id, now(), "acknowledged", args.operator.strip(), args.note.strip()))
        print("Alert acknowledged. This does not mark the pollution as resolved.")
    elif args.command == "queue":
        with store.connect() as db:
            rows = db.execute("SELECT * FROM observations ORDER BY created DESC").fetchall()
            print(json.dumps([{"id": r["id"], "community": r["community"], "review": r["review_status"], "export_blocks": export_blocks(r)} for r in rows], indent=2))
    elif args.command == "review":
        try:
            review(store, args.id, args.decision, args.reviewer, args.note)
        except ValueError as error:
            parser.error(str(error))
        print("Review saved to the audit log.")
    else:
        result = export_records(store, args.licensed)
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f'Exported {len(result["cells"]) if args.licensed else result["record_count"]} {"aggregate cells" if args.licensed else "human-reviewed records"}. Nothing has been sold or sent to government.')


if __name__ == "__main__":
    main()
