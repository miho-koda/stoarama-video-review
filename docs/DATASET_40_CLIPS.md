# Dataset: 40 accepted camera clips

The dataset contains 40 fixed-camera, daylight social-mixing clips selected by the original Stoarama server workflow. Each accepted clip is 90, 120, or 150 seconds and has a verified Drive upload. The canonical metadata file is [selections_all.final_enriched.csv](https://drive.google.com/open?id=1uUcmEErzjizq30k__nwA6x77Jn0ZGJ6q).

## Geographic coverage

- 24 of 40 clips (60%) are in U.S. states; 26 (65%) are in U.S. jurisdictions when the U.S. Virgin Islands are included.
- 11 clips (27.5%) are in Florida.
- Four clips are in Surat Thani, Thailand.
- Japan, Canada, and the U.S. Virgin Islands have two clips each.
- The remaining clips span the Philippines, Taiwan, the United Kingdom, Spain, Belgium, and the Cayman Islands.

The data are internationally sourced but not geographically balanced. Describe them as a multi-country webcam dataset with substantial U.S./Florida concentration, not as a representative global sample.

## Acceptance policy

Candidates were preferred when they had a stable fixed viewpoint, daylight, 2–30 qualifying people, people at least 60 pixels tall in most qualifying frames, low vehicle dominance, and social/activity signals. Moving/PTZ, night, sparse, excessive-crowd, traffic-dominated, and small-person candidates were rejected. Exact thresholds are in [`config/pipeline_config.json`](../config/pipeline_config.json).
