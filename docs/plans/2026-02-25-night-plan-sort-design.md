# Night Plan Builder — Set / Transit Sort Design

**Date:** 2026-02-25
**Scope:** All 6 Night Plan Builder sections (DSO, Planet, Comet, Asteroid, Cosmic)

---

## Problem

The Night Plan Builder currently sorts by priority tier first, then set time within each tier.
The caption did not clearly state this, causing confusion about sort order.
Priority is already visible via color coding — it doesn't need to drive sort order.

---

## Solution

1. Remove priority from sort order — sort purely by time.
2. Add a Set Time / Transit Time radio that drives both the filter threshold and the sort column.
3. Update the caption to state the active sort method.

---

## Changes

### `build_night_plan(df_obs, pri_col, dur_col, sort_by="set")`

- Add `sort_by: str` parameter — `"set"` (default) or `"transit"`
- Remove `_pri_rank` sort key
- Sort by `_set_datetime` (set) or `_transit_datetime` (transit), ascending, NaT last
- Priority color coding in the output table is unchanged

### `_render_night_plan_builder(...)` — Row B

**Before:**
```
[ Sets no earlier than ]  [ Moon Status ]
```

**After:**
```
[ Sort & filter by: ● Set Time  ○ Transit Time ]  [ Sets/Transits no earlier than ]  [ Moon Status ]
```

- Radio (horizontal) in col 1, time input in col 2, Moon Status in col 3
- Radio selection drives:
  - Time input label: "Sets no earlier than" or "Transits no earlier than"
  - Filter column: `_set_datetime` or `_transit_datetime`
  - Sort column passed to `build_night_plan(sort_by=...)`

### Caption

Replaces current priority-sort description text:
- Set Time: `"Plan sorted by **Set Time** — targets that set soonest appear first."`
- Transit Time: `"Plan sorted by **Transit Time** — targets that transit soonest appear first."`

Sections without priority (`pri_col=None`) keep the same caption. Sections with priority keep
their priority multiselect filter and color coding; only the sort changes.

---

## What Does NOT Change

- Priority multiselect filter (still lets user exclude priority levels)
- Priority row color coding in the plan table
- All other filters: magnitude, type, discovery recency, moon status
- PDF and CSV export

---

## Affected Files

- `app.py` — `build_night_plan()` and `_render_night_plan_builder()`
- No other files
