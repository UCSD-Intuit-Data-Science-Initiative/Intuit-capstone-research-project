# data/

| Folder | What | In git? |
|---|---|---|
| `raw/` | FinQA + ConvFinQA exactly as published, read-only | No (fetched) |
| `interim/` | Outputs of pipeline scripts; always regenerable | No |
| `families/` | Authored case families (decision layer) | Yes |
| `sources.yaml` | Pinned repos, commits, files, git blob ids | Yes |
| `checksums.sha256` | SHA-256 of every file in `raw/` | Yes |
| `profile_expected.yaml` | Nov 1 profile numbers that `make profile` must reproduce | Yes |

## Profile the data (Stage 2)

```bash
make profile   # verify checksums, profile, compare to profile_expected.yaml (exit 1 on any difference)
```

Writes `interim/profile/` (`profile.json` + per-example CSVs) and `docs/profile_report.md`.
Definitions: `docs/reference/profile_nov1.py`, re-implemented and tested in `src/capstone/data/`.

## Select candidates (Stage 3)

```bash
make select    # filter, split by company, check the gate (exit 1 if the gate fails)
```

Writes `interim/stage3/candidates.csv` (every conversation with status, drop reason, metric,
scale, input cells, split), `interim/stage3/selection.json`, and `docs/selection_report.md`.
Held-out companies also get cut versions (`id` ends `|cut@N`, `truncated_at` = N turns kept)
that end in a `difference` metric.

## Build families (Stage 4)

```bash
make family        # draw, build every specs/*.yaml, stop on any broken family rule
make family-check  # rebuild in memory, fail if a generated file differs; writes nothing
```

| Path | What | Written by |
|---|---|---|
| `families/specs/<id>.yaml` | Authored script: source, edits, documents, turns; names its policy, threshold and metric (no gold) | Hand |
| `families/policies/<policy>.yaml` | Shared written policy; `{threshold}` and `{metric}` are filled per family | Hand |
| `families/pilot/<id>/family.json` | What the harness reads: tables, turns, gold, spec and policy sha256 | `make family` |
| `docs/families/<id>.md` | Review file: summary table, then every version | `make family` |
| `families/draw_log.csv` | Every draw: id, pass/fail, reason | `make family` |

Generated files are byte-identical on rerun; never edit them by hand. The family rules are listed in
`src/capstone/data/family.py`. Specs build in file-name order, and each draw skips companies and
source ids used by earlier specs (one family per company).

To add a family: copy `specs/F001.yaml` to the next id, set `prefilter_labels: true`, run
`make family` (it stops and names the candidate the draw picked), set `source_id` to that pick, then
author the edits, threshold, documents and renames for its table and rebuild. Read the review file.

## Get the data

```bash
make data          # download at pinned commits, verify, make read-only (~375 MB)
make data-verify   # offline re-check of data/raw against checksums.sha256
```

`make data` checks each download twice: against the git blob id in
`sources.yaml` (proves it is the pinned commit's file) and against
`checksums.sha256` (proves it matches what the team locked). Any mismatch
stops with an error; existing raw files are never overwritten.
Each run writes `raw/FETCH_LOG.json` (files, bytes, commits, Python version).

## What gets downloaded

| Source | Commit | Files |
|---|---|---|
| [FinQA](https://github.com/czyssrs/FinQA) (MIT) | `0f16e28` (2022-06-05) | `LICENSE`, `README.md`, `dataset/{train,dev,test,private_test}.json` |
| [ConvFinQA](https://github.com/czyssrs/ConvFinQA) (MIT) | `cf3eed2` (2022-11-09) | `LICENSE`, `README.md`, `data.zip` → `data/{train,dev,train_turn,dev_turn,test_private,test_turn_private}.json` |

Upstream model code is never downloaded or run.

## Changing a pin (rare, deliberate)

1. Update `commit`, `git_blob` and `size` in `sources.yaml` (`git ls-tree -l <commit>`).
2. Delete `data/raw/<source>/` (`chmod -R u+w` first) and `checksums.sha256`.
3. `make data-lock`, commit both files, and add a line to the CLAUDE.md decision log.
