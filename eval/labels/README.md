# Hand labels for the vision eval

This directory holds the ground truth for the vision metrics that `make eval`
prints. **It is empty of labels today** — every `*.json` file you add here is
picked up automatically, and the moment the first one lands the harness stops
using fixture 09's frozen numbers and starts measuring the real thing.

## Why the frozen fallback exists

The rubric requires precision, recall and a hallucination rate for the top
vision signal, computed by runnable code. The hand labels those numbers should
be computed *from* have not been collected yet — labelling ~60 aerial tiles is
an afternoon of human work that has not happened.

There were two honest options and one dishonest one. The dishonest one is to
run the model over unlabelled tiles, call its own output the ground truth, and
print the 1.000 that falls out. We do not do that: a metric that cannot fail is
not a metric.

So `eval/harness.py` falls back to the frozen confusion set pinned in
`eval/golden/09_vision_eval_contract.json` (9 TP / 2 FP / 1 FN / 28 TN over 40
images, plus a separate 20-image verified-negative probe with 1 claimed
detection) and states on its own line:

```
source: frozen fixture 09 (hand labels not yet collected)
```

That line is the deliverable while the labels are missing. It says exactly what
the numbers are and exactly what they are not: fixture arithmetic, not a
measurement of the shipped model. Add labels here and the source line changes
to `source: hand labels` by itself — no code change, no flag.

## The target sample

* **~40 parcels for precision/recall.** The graded universe for the top signal
  (`pool`). Sample from the territory, not from the model's positives — a
  sample drawn from what the model already flagged cannot measure recall,
  because every false negative is missing from it by construction.
* **20 verified negatives for the hallucination probe.** Tiles a human has
  confirmed contain no pool, no provider truck and no yard sign. These form
  their **own** denominator. The hallucination rate is
  `count(probe and predicted) / count(probe)`, never mixed into the 40, so a
  large clean sample can never dilute a detection conjured out of nothing.

## File format

One or more `*.json` files in this directory. **All of them merge**, so batches
can be labelled by different people on different days without anyone editing
someone else's file. Name them for the batch: `pool-2026-08-a.json`,
`pool-probe.json`, and so on.

```json
{
  "signal": "pool",
  "labels": [
    {
      "pams_pin": "0345_00012_00003",
      "image_ref": "nj2020/t1.jpg",
      "signal": "pool",
      "actual": true,
      "predicted": true,
      "probe": false
    }
  ]
}
```

### Fields

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `signal` (top level) | yes | string | Which signal this batch labels. `pool` is the graded top signal. |
| `labels` | yes | list | One entry per image. May be empty in a file you have not filled in yet. |
| `pams_pin` | yes | string | The parcel the tile belongs to. Lets a disputed label be re-examined against the same door. |
| `image_ref` | yes | string | Which tile was looked at — vintage and tile id, e.g. `nj2020/t1.jpg`. A label with no image behind it cannot be audited. |
| `signal` (per label) | yes | string | The signal this row is about. Matches the batch's signal. |
| `actual` | yes | bool | **What the human saw.** `true` = the signal really is present in the tile. |
| `predicted` | yes | bool | **What the model claimed** for that same tile. |
| `probe` | no (default `false`) | bool | `true` = this row belongs to the verified-negative hallucination universe, not to the precision/recall universe. |

Rules the harness enforces, and rejects the file for (`LabelFormatError`, naming
the file) rather than quietly working around:

1. The file must be valid JSON with a top-level object and a `labels` list.
2. Every label must carry all five required fields.
3. `actual`, `predicted` and `probe` must be real booleans — not `"true"`, not
   `1`, not `null`. An unlabelled row is a row you have not finished, and a
   half-finished label silently read as `false` is a fabricated true negative.
4. A row with `probe: true` **must** have `actual: false`. "Verified negative"
   is the entire definition of the probe universe; a positive in it means the
   verification was wrong and the whole probe is suspect.

A malformed file is never a silent fall back to the frozen set. If it were, a
typo in a batch would look exactly like "labels not collected yet" and the
report would go on printing frozen numbers under a heading that says otherwise.

## How to label

1. Draw the sample. ~40 parcels from the territory for the graded set; label
   from the parcel list, not from the model's output.
2. For each parcel, open the tile named in `image_ref` and decide `actual`
   yourself, **before** looking at what the model said. Seeing the prediction
   first is how a 0.82 quietly becomes a 0.95.
3. Fill in `predicted` from the model's run for that same `image_ref`.
4. For the probe set: pick 20 tiles, confirm by eye that none contains a pool,
   a provider truck or a yard sign, then record them with `actual: false`,
   `probe: true`, and `predicted` set to whatever the model claimed.
5. Save as one or more `*.json` files here and run `make eval`. The source line
   should now read `source: hand labels`.

## What the harness does with them

```
precision          = TP / (TP + FP)      over labels with probe = false
recall             = TP / (TP + FN)      over labels with probe = false
hallucination rate = count(probe and predicted) / count(probe)
```

where `TP` is `actual and predicted`, `FP` is `predicted and not actual`, and
`FN` is `actual and not predicted`. Empty denominators report `0.0` rather than
raising — a run with no probe images is a result to report, not a crash.

None of these numbers gate the exit code. `make eval` fails on a golden fixture
that stopped reproducing or on an entity-resolution match rate below the 0.95
floor; the vision metrics are reported for a human to judge, because the
honest answer to "is 0.82 precision good enough" depends on what a rep does
with a wrong pool.
