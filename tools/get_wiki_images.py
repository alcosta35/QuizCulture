#!/usr/bin/env python3
"""
Download a freely-licensed Wikipedia/Wikimedia Commons image for each of the
20 QuizCulture questions, and write CREDITS.md with author + license.

  Windows:  py tools\get_wiki_images.py
  Linux:    python3 tools/get_wiki_images.py

Output: question-images\q01.jpg ... q20.jpg (+ CREDITS.md, credits.csv)
Only standard library needed. Non-free (fair-use) images are skipped
automatically and the next fallback article is tried.

Images were chosen so they illustrate the topic WITHOUT giving away the answer
(e.g. a molcajete instead of an avocado for the guacamole question).
"""
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "QuizCulture/1.0 (GoEasy cultural fair quiz; contact: andre.costa@ancotechnology.com)"
API = "https://en.wikipedia.org/w/api.php"
WIDTH = 1280
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "question-images")

# question id -> (short topic, [Wikipedia article titles to try, in order])
PLAN = {
    "q01": ("Machu Picchu",             ["Machu Picchu"]),
    "q02": ("Capital of Jamaica",       ["Blue Mountains (Jamaica)", "Jamaica"]),
    "q03": ("Guacamole",                ["Molcajete", "Mexican cuisine"]),
    "q04": ("Tango",                    ["Tango", "Argentine tango"]),
    "q05": ("Reggae",                   ["Sound system (Jamaican)", "Phonograph record"]),
    "q06": ("Highest seat of government", ["Illimani", "Altiplano"]),
    "q07": ("Languages of South America", ["South America"]),
    "q08": ("One Hundred Years of Solitude", ["Aracataca", "Typewriter"]),
    "q09": ("Jamaica's national dish",  ["Scotch bonnet", "Allspice"]),
    "q10": ("Day of the Dead",          ["Tagetes erecta", "Sugar skull", "Day of the Dead"]),
    "q11": ("Cartagena",                ["Walls of Cartagena", "Cartagena, Colombia"]),
    "q12": ("FIFA World Cups",          ["Ball (association football)", "Association football"]),
    "q13": ("Bob Marley",               ["Bob Marley", "Microphone"]),
    "q14": ("Atacama Desert",           ["Atacama Desert"]),
    "q15": ("Steelpan",                 ["Steelpan"]),
    "q16": ("Capital of Brazil",        ["Flag of Brazil"]),
    "q17": ("Frida Kahlo",              ["Frida Kahlo", "Mexican muralism"]),
    "q18": ("Independence 1804",        ["Haitian Revolution", "Toussaint Louverture"]),
    "q19": ("Angel Falls",              ["Angel Falls"]),
    "q20": ("Longest mountain range",   ["Andes", "Aconcagua"]),
}


def get_json(params):
    params = dict(params, format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def lead_image(title):
    """Return (file_name, thumb_url) of the article's lead image, or None."""
    d = get_json({"action": "query", "prop": "pageimages", "titles": title,
                  "piprop": "name|thumbnail", "pithumbsize": WIDTH, "redirects": 1})
    pages = d.get("query", {}).get("pages", [])
    if not pages or "pageimage" not in pages[0] or "thumbnail" not in pages[0]:
        return None
    return pages[0]["pageimage"], pages[0]["thumbnail"]["source"]


def license_info(file_name):
    d = get_json({"action": "query", "titles": "File:" + file_name, "prop": "imageinfo",
                  "iiprop": "extmetadata|url"})
    page = d["query"]["pages"][0]
    ii = (page.get("imageinfo") or [{}])[0]
    md = ii.get("extmetadata", {})
    val = lambda k: strip_tags(md.get(k, {}).get("value", ""))
    return {
        "license": val("LicenseShortName") or "unknown",
        "nonfree": val("NonFree").lower() in ("true", "1", "yes"),
        "artist": val("Artist") or "unknown",
        "page": ii.get("descriptionurl", ""),
    }


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        f.write(r.read())


def main():
    os.makedirs(OUT, exist_ok=True)
    credits = []
    for qid, (topic, titles) in PLAN.items():
        done = False
        for title in titles:
            try:
                li = lead_image(title)
                if not li:
                    print(f"{qid}: no lead image on '{title}', trying next")
                    continue
                fname, thumb = li
                info = license_info(fname)
                lic = info["license"].lower()
                if info["nonfree"] or "fair use" in lic or "non-free" in lic or lic == "unknown":
                    print(f"{qid}: '{fname}' is {info['license']} (not free) - skipping")
                    continue
                ext = os.path.splitext(urllib.parse.urlparse(thumb).path)[1].lower() or ".jpg"
                if ext == ".jpeg":
                    ext = ".jpg"
                out = os.path.join(OUT, qid + ext)
                download(thumb, out)
                credits.append({"question": qid, "topic": topic, "article": title, "file": os.path.basename(out),
                                "source_file": fname, "author": info["artist"], "license": info["license"],
                                "url": info["page"]})
                print(f"{qid}: OK  {os.path.basename(out):8}  <- {fname}  [{info['license']}]")
                done = True
                break
            except Exception as e:  # network hiccup, odd page, etc.
                print(f"{qid}: error on '{title}': {e}")
            finally:
                time.sleep(0.5)  # be polite to the API
        if not done:
            print(f"{qid}: !! no free image found - keep the built-in SVG or upload one manually")

    with open(os.path.join(OUT, "credits.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(credits[0].keys()) if credits else ["question"])
        w.writeheader()
        w.writerows(credits)
    with open(os.path.join(OUT, "CREDITS.md"), "w", encoding="utf-8") as f:
        f.write("# Question image credits\n\nImages from Wikimedia Commons / Wikipedia, used under their free licenses.\n\n")
        f.write("| Q | Image | Author | License | Source |\n|---|---|---|---|---|\n")
        for c in credits:
            f.write(f"| {c['question']} | {c['file']} | {c['author'].replace('|', '/')} | {c['license']} | [{c['source_file']}]({c['url']}) |\n")
    print(f"\n{len(credits)}/{len(PLAN)} images saved to {os.path.abspath(OUT)}")


if __name__ == "__main__":
    sys.exit(main())
