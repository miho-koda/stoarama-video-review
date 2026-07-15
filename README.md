# Stoarama video review archive

This repository preserves the server workflow that produced 40 accepted camera clips and the curation tools used to enrich their metadata. It stores no clips, credentials, cookies, or model weights.

The canonical review file is [selections_all.final_enriched.csv](https://drive.google.com/open?id=1uUcmEErzjizq30k__nwA6x77Jn0ZGJ6q).

## Repository map

- `src/stoarama_pipeline/`: reusable catalog, link, media, selection, and validation helpers.
- `scripts/`: discovery, scanning, merging, and metadata-enrichment commands.
- `jobs/`: Slurm launch files for the original GPU-server workflow.
- `config/`: quality thresholds and duration policy.
- `data/curation/`: reviewed locations for the 40 accepted clips.
- `tests/`: regression tests.

## Scope

The server workflow is retained for auditability and possible reproduction; it is not an instruction to start another campaign. Later local VOD, frame-exchange, and remainder-scan experiments were removed.

## Commands

Use Python 3.11+ and install `requirements-server.txt` in a suitable server environment. Run from the repository root:

```bash
python scripts/pipeline.py discover --output work/catalog.csv
python scripts/pipeline.py validate path/to/selections.csv
python scripts/enrich_accepted_metadata.py --help
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests
```

See [Dataset overview](docs/DATASET_40_CLIPS.md), [original workflow](docs/ORIGINAL_SERVER_WORKFLOW.md), [data dictionary](docs/DATA_DICTIONARY.md), [location verification](docs/LOCATION_VERIFICATION.md), and [Drive deliverables](docs/DRIVE_DELIVERABLES.md).
