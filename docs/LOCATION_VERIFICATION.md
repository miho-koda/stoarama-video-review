# Location verification

`data/curation/location_reviews.csv` contains one reviewed location record per accepted clip. Applying it through `scripts/enrich_accepted_metadata.py --location-review ...` updates only reviewed location fields; original Stoarama fields remain unchanged.

- **High confidence — 25 clips:** operator/source page explicitly names the location.
- **Medium confidence — 15 clips:** public YouTube title explicitly names the location.

Every row records evidence URL, method, reviewer, review time, and notes. No correction was made by guessing from an image alone.
