#!/usr/bin/env python3
"""Rank historical 150-second YouTube live-DVR windows for social mixing."""
import csv, itertools, math, os, tempfile, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import yt_dlp
from ultralytics import YOLO

from scan import daylight_score, fixed_camera_score

MODEL = Path(__file__).resolve().parent.parent / "models/yolo26n.pt"


class DVR:
    def __init__(self, url):
        self.url = url
        with yt_dlp.YoutubeDL({"quiet": True, "live_from_start": True, "skip_download": True}) as ydl:
            self.info = ydl.extract_info(url, download=False)
        formats = [f for f in self.info["formats"] if f.get("height") == 720 and callable(f.get("fragments"))]
        self.fmt = formats[0]
        self.fragment_factory = self.fmt["fragments"]
        first = next(self.fragment_factory({}))
        self.first_url = first["url"]
        self.fragment_count = int(first["fragment_count"])
        self.current_seq = self.fragment_count - 1
        self.segment_seconds = float(self.fmt.get("target_duration") or 5)
        self.live_utc = datetime.now(timezone.utc)
        self.oldest_utc = self.live_utc - timedelta(hours=120)
        self.headers = self.fmt.get("http_headers", {})
        req = urllib.request.Request(first["url"], headers=self.headers)
        first_bytes = urllib.request.urlopen(req, timeout=12).read()
        moof = first_bytes.find(b"moof")
        self.init_bytes = first_bytes[:max(0, moof - 4)] if moof >= 4 else b""

    def fragment_url(self, at):
        behind = (self.live_utc - at).total_seconds()
        if behind > 120 * 3600:
            raise ValueError("timestamp is outside YouTube's 120-hour DVR window")
        seq = self.current_seq - round(behind / self.segment_seconds)
        parsed = urllib.parse.urlsplit(self.first_url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(k, str(seq) if k == "sq" else v) for k, v in query]
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))

    def frame(self, at, cache):
        key = round(at.timestamp() / self.segment_seconds)
        if key in cache:
            return cache[key]
        try:
            req = urllib.request.Request(self.fragment_url(at), headers=self.headers)
            data = urllib.request.urlopen(req, timeout=12).read()
            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                f.write(data); f.flush()
                cap = cv2.VideoCapture(f.name)
                ok, frame = cap.read(); cap.release()
            cache[key] = frame if ok else None
        except Exception as exc:
            print(f"    fragment_error at={at.isoformat()} error={exc}", flush=True)
            cache[key] = None
        return cache[key]


def detect(model, frame):
    result = model.predict(frame, classes=[0, 2, 3, 5, 7], device=0, verbose=False)[0]
    people, vehicles = [], 0
    for cls, box in zip(result.boxes.cls.tolist(), result.boxes.xyxy.tolist()):
        if int(cls) == 0:
            h = box[3] - box[1]
            people.append((box, h))
        else:
            vehicles += 1
    qualifying = [(b, h) for b, h in people if h >= 60]
    pairs = 0
    for i, (a, ah) in enumerate(qualifying):
        ax, ay = (a[0]+a[2])/2, a[3]
        for b, bh in qualifying[i+1:]:
            bx, by = (b[0]+b[2])/2, b[3]
            scale = (ah+bh)/2
            dist = math.hypot(ax-bx, ay-by) / max(scale, 1)
            if .45 <= dist <= 3.0 and abs(ay-by) <= 1.2*scale:
                pairs += 1
    return {"all_heights": [h for _,h in people], "people": len(qualifying),
            "vehicles": vehicles, "pairs": pairs, "daylight": daylight_score(frame)}


def full_window(dvr, model, start, cache):
    frames, stats = [], []
    for n in range(15):
        frame = dvr.frame(start + timedelta(seconds=n*10), cache)
        if frame is not None:
            frames.append(frame); stats.append(detect(model, frame))
    if len(stats) < 12:
        return None
    counts = [s["people"] for s in stats]
    heights = [h for s in stats for h in s["all_heights"]]
    usable = np.mean([2 <= n <= 30 for n in counts])
    sized = np.mean([h >= 60 for h in heights]) if heights else 0
    daylight = np.mean([s["daylight"] >= .52 for s in stats])
    fixed = fixed_camera_score(frames)
    vehicles = sum(s["vehicles"] for s in stats)
    pairs = float(np.mean([min(s["pairs"], 8)/8 for s in stats]))
    median = float(np.median(counts))
    active = np.mean([5 <= n <= 22 for n in counts])
    passed = usable >= .8 and sized >= .7 and daylight >= .75 and fixed >= .65 and sum(counts) > vehicles
    score = 2*active + pairs + daylight + sized + fixed - abs(median-12)/20 - max(0,max(counts)-25)/10
    return {"passed": passed, "score": score, "people_min": min(counts),
            "people_median": median, "people_max": max(counts),
            "people_ge60_fraction": sized, "daylight_fraction": daylight,
            "fixed_camera_score": fixed, "social_pair_score": pairs,
            "active_density_fraction": active, "vehicles_total": vehicles}


def rank_video(row, model, lookback_hours=119):
    dvr = DVR(row["review_url"]); cache = {}; coarse = []
    utc_offset = {"Thailand": 7, "United States": -7, "Canada": -6}.get(row["country"], 0)
    oldest = dvr.live_utc - timedelta(hours=lookback_hours)
    at = oldest
    while at <= dvr.live_utc - timedelta(minutes=3):
        local_hour = (at + timedelta(hours=utc_offset)).hour
        if not 6 <= local_hour < 19:
            at += timedelta(minutes=30)
            continue
        frame = dvr.frame(at, cache)
        if frame is not None:
            s = detect(model, frame)
            if s["daylight"] >= .46 and 1 <= s["people"] <= 30:
                score = s["daylight"] + min(s["pairs"],8)/8 - abs(s["people"]-12)/20
                coarse.append((score, at))
        at += timedelta(minutes=30)
    print(f"  coarse_candidates={len(coarse)}", flush=True)
    candidates = []; evaluated = []
    top_windows = int(os.environ.get("TOP_WINDOWS", 8))
    for _, start in sorted(coarse, reverse=True)[:top_windows]:
        metrics = full_window(dvr, model, start, cache)
        if metrics:
            evaluated.append((metrics["score"], start, metrics))
        if metrics and metrics["passed"]:
            candidates.append((metrics["score"], start, metrics))
    if not candidates and evaluated:
        _, rejected_at, rejected = max(evaluated, key=lambda x:x[0])
        print(f"  best_rejected={rejected_at.isoformat()} metrics={rejected}", flush=True)
    return sorted(candidates, reverse=True, key=lambda x:x[0]), dvr


def main():
    root=Path(__file__).resolve().parent
    rows=list(csv.DictReader((root/"youtube_review_10.csv").open()))
    row_start=int(os.environ.get("ROW_START", 0))
    rows=rows[row_start:row_start+int(os.environ.get("MAX_ROWS", len(rows)))]
    model=YOLO(str(MODEL)); ranked={}; occurrences=Counter()
    output=[]
    for index,row in enumerate(rows,1):
        vid=urllib.parse.parse_qs(urllib.parse.urlsplit(row["review_url"]).query)["v"][0]
        print(f"[{index}/10] {row['name']}",flush=True)
        if vid not in ranked:
            ranked[vid]=rank_video(row,model)
        candidates,dvr=ranked[vid]; choice=occurrences[vid]; occurrences[vid]+=1
        if choice >= len(candidates):
            print("  no passing window",flush=True); continue
        _,start,metrics=candidates[choice]
        end=start+timedelta(seconds=150)
        link=(f"https://miho-koda.github.io/stoarama-video-review/review-v4.html?"
              f"v={vid}&startUtc={urllib.parse.quote(start.isoformat().replace('+00:00','Z'))}")
        output.append({"row_id":len(output)+1,"name":row["name"],"city":row["city"],
            "region":row["region"],"country":row["country"],"clickable_review_url":link,
            "segment_start_utc":start.isoformat(),"segment_end_utc":end.isoformat(),**metrics})
        print(f"  PASS {start.isoformat()} score={metrics['score']:.3f}",flush=True)
    fields=list(output[0]) if output else []
    output_name=os.environ.get("OUTPUT", "youtube_review_10_redone.csv")
    with (root/output_name).open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(output)
    print(f"accepted={len(output)}",flush=True)

if __name__ == "__main__": main()
