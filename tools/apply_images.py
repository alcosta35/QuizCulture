#!/usr/bin/env python3
"""
Run on serverlab after `git pull` to switch the quiz to the downloaded photos:

    sudo python3 tools/apply_images.py

Copies question-images/qNN.(jpg|png|...) into /opt/quiz2026/data/images and
points question qNN at it in questions.json. Questions without a photo keep
their current image. Safe to re-run. No restart needed.
"""
import glob
import json
import os
import shutil
import sys

DATA = os.environ.get("QUIZ_DATA_DIR", "/opt/quiz2026/data")
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "question-images")
QFILE = os.path.join(DATA, "questions.json")
IMG = os.path.join(DATA, "images")

if not os.path.exists(QFILE):
    sys.exit(f"{QFILE} not found - is the quiz installed?")

photos = {}
for p in glob.glob(os.path.join(SRC, "q[0-9][0-9].*")):
    qid, ext = os.path.splitext(os.path.basename(p))
    if ext.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        photos[qid] = p

with open(QFILE, encoding="utf-8") as f:
    questions = json.load(f)

shutil.copy2(QFILE, QFILE + ".bak")
changed = 0
for q in questions:
    src = photos.get(q["id"])
    if not src:
        continue
    name = q["id"] + "-photo" + os.path.splitext(src)[1].lower()
    shutil.copy2(src, os.path.join(IMG, name))
    q["image"] = name
    changed += 1
    print(f"{q['id']}: {name}")

tmp = QFILE + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(questions, f, ensure_ascii=False, indent=2)
os.replace(tmp, QFILE)

# keep files owned by the service user
os.system(f"chown -R quiz:quiz {DATA} 2>/dev/null")
print(f"\n{changed} questions updated (backup: {QFILE}.bak). Refresh the quiz page.")
