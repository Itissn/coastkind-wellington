# Synthetic demonstration data codebook

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
