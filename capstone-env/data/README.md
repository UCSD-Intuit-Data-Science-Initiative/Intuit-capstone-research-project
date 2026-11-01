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
