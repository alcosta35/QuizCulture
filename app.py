#!/usr/bin/env python3
"""
Latin America Quiz 2026 — single-file Flask app
------------------------------------------------
Same pattern as WC2026 / GSYFIFA2026: Flask + vanilla JS + JSON file storage.

  * Players register with Name + Email, then answer N random questions
    (default 10) drawn from the question bank (20 seeded).
  * One question per page with an image, A/B/C/D or True/False answers,
    Back / Next buttons and Finish on the last page.
  * Score = 1 point per correct answer; ties broken by fastest time
    (server-side clock, start -> finish).
  * Public ranking page.
  * Admin panel (/admin, PIN protected): add/edit/delete questions,
    upload images, settings, players, reset, CSV export.

Environment variables
  QUIZ_DATA_DIR   data folder               (default /opt/quiz2026/data)
  QUIZ_ADMIN_PIN  admin PIN                 (required in production)
  QUIZ_PORT       listen port for dev run   (default 5052)
  QUIZ_SECRET     Flask session secret      (auto-generated & persisted if unset)
"""
import csv
import io
import json
import os
import random
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, abort, jsonify, request, send_from_directory,
                   session)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

# --------------------------------------------------------------------------
# Paths / config
# --------------------------------------------------------------------------
DATA_DIR = os.environ.get("QUIZ_DATA_DIR", "/opt/quiz2026/data")
IMG_DIR = os.path.join(DATA_DIR, "images")
F_QUESTIONS = os.path.join(DATA_DIR, "questions.json")
F_PLAYERS = os.path.join(DATA_DIR, "players.json")
F_SETTINGS = os.path.join(DATA_DIR, "settings.json")
F_SECRET = os.path.join(DATA_DIR, ".secret")

ADMIN_PIN = os.environ.get("QUIZ_ADMIN_PIN", "")
ALLOWED_IMG = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
MAX_UPLOAD = 5 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_SETTINGS = {
    "title": "Latin America & Caribbean Quiz",
    "subtitle": "10 questions. One shot. Fastest perfect score wins.",
    "questions_per_quiz": 10,
    "quiz_open": True,
    "public_ranking": True,
    "reveal_answers": False,      # show correct answers on the result page
    "shuffle_options": True,      # shuffle A/B/C/D per player (T/F never shuffled)
}

LOCK = threading.RLock()

# --------------------------------------------------------------------------
# Seed question bank (20 questions) + generated SVG illustrations
# --------------------------------------------------------------------------
SEED = [
    # text, options, answer_index, emoji, caption, color1, color2, explanation
    ("Machu Picchu is located in which country?",
     ["Peru", "Bolivia", "Ecuador", "Chile"], 0, "🏔️", "15th-century Inca citadel", "#2E7D32", "#81C784", ""),
    ("What is the capital of Jamaica?",
     ["Kingston", "Montego Bay", "Ocho Rios", "Negril"], 0, "🏙️", "Island capital", "#00796B", "#FFD54F", ""),
    ("What's the main ingredient in guacamole?",
     ["Avocado", "Tomato", "Lime", "Jalapeño"], 0, "🥣", "A classic Mexican dip", "#558B2F", "#DCE775", ""),
    ("Tango originated in Argentina and which other country?",
     ["Uruguay", "Chile", "Paraguay", "Cuba"], 0, "💃", "Born on the Río de la Plata", "#B71C1C", "#FF8A65", ""),
    ("Which music genre was born in Jamaica in the late 1960s?",
     ["Reggae", "Salsa", "Calypso", "Merengue"], 0, "🎶", "Kingston, late 1960s", "#1B5E20", "#FBC02D", ""),
    ("Which city is the highest seat of government in the world?",
     ["La Paz", "Quito", "Bogotá", "Cusco"], 0, "🏛️", "About 3,600 m above sea level", "#4527A0", "#90CAF9", ""),
    ("True or false: Spanish is the official language of every South American country.",
     ["True", "False"], 1, "🗣️", "Languages of South America", "#283593", "#4FC3F7",
     "Brazil, Guyana, Suriname and French Guiana are exceptions."),
    ("Who wrote “One Hundred Years of Solitude”?",
     ["Gabriel García Márquez", "Jorge Luis Borges", "Pablo Neruda", "Mario Vargas Llosa"], 0,
     "📖", "Magical realism, 1967", "#4E342E", "#FFCC80", ""),
    ("What is Jamaica's national dish?",
     ["Ackee and saltfish", "Jerk chicken", "Rice and peas", "Curry goat"], 0, "🍽️", "Island flavours", "#E65100", "#FFE082", ""),
    ("The Day of the Dead (Día de Muertos) is most famously celebrated in:",
     ["Mexico", "Cuba", "Colombia", "Venezuela"], 0, "🕯️", "Nov 1–2, marigolds and altars", "#6A1B9A", "#FF9800", ""),
    ("Which Colombian city is famous for its walled colonial old town on the Caribbean coast?",
     ["Cartagena", "Medellín", "Bogotá", "Cali"], 0, "🏰", "Colonial walls by the sea", "#0277BD", "#FFB74D", ""),
    ("Which Latin American country has won the most FIFA World Cups?",
     ["Brazil", "Argentina", "Uruguay", "Mexico"], 0, "🏆", "The beautiful game", "#1565C0", "#FDD835", ""),
    ("Reggae legend Bob Marley was born in:",
     ["Jamaica", "Trinidad and Tobago", "Barbados", "Haiti"], 0, "🎤", "One love", "#2E7D32", "#E53935", ""),
    ("The Atacama, one of the driest deserts on Earth, is mainly in:",
     ["Chile", "Peru", "Argentina", "Bolivia"], 0, "🏜️", "Almost no rain for centuries", "#BF360C", "#FFCC80", ""),
    ("The steelpan, a drum made from oil barrels, was invented in:",
     ["Trinidad and Tobago", "Jamaica", "Barbados", "Cuba"], 0, "🥁", "Music from oil barrels", "#AD1457", "#FFD54F", ""),
    ("What is the capital of Brazil?",
     ["Brasília", "Rio de Janeiro", "São Paulo", "Salvador"], 0, "🗺️", "A planned capital, 1960", "#00897B", "#FFEE58", ""),
    ("Painter Frida Kahlo was from:",
     ["Mexico", "Colombia", "Argentina", "Cuba"], 0, "🎨", "Self-portraits & bold colour", "#C2185B", "#81D4FA", ""),
    ("Which was the first country in Latin America and the Caribbean to win independence, in 1804?",
     ["Haiti", "Cuba", "Dominican Republic", "Jamaica"], 0, "📜", "1 January 1804", "#1A237E", "#EF5350", ""),
    ("Angel Falls, the world's highest waterfall, is in:",
     ["Venezuela", "Colombia", "Guyana", "Ecuador"], 0, "💧", "979 m free fall", "#01579B", "#80DEEA", ""),
    ("Which is the world's longest continental mountain range?",
     ["Andes", "Sierra Madre", "Blue Mountains", "Sierra Maestra"], 0, "⛰️", "About 7,000 km long", "#37474F", "#B0BEC5", ""),
]


def _svg(emoji, caption, c1, c2, n):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400" width="800" height="400">
<defs>
 <linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>
 <pattern id="p" width="40" height="40" patternUnits="userSpaceOnUse"><circle cx="20" cy="20" r="2" fill="#ffffff" opacity=".18"/></pattern>
</defs>
<rect width="800" height="400" fill="url(#g)"/>
<rect width="800" height="400" fill="url(#p)"/>
<circle cx="400" cy="175" r="115" fill="#ffffff" opacity=".18"/>
<text x="400" y="222" font-size="135" text-anchor="middle" font-family="Apple Color Emoji,Segoe UI Emoji,Noto Color Emoji,sans-serif">{emoji}</text>
<text x="400" y="355" font-size="34" font-weight="700" text-anchor="middle" fill="#ffffff" font-family="Segoe UI,Helvetica,Arial,sans-serif" style="paint-order:stroke" stroke="#000000" stroke-opacity=".25" stroke-width="4">{caption}</text>
</svg>"""


# --------------------------------------------------------------------------
# JSON storage helpers (atomic writes, single lock)
# --------------------------------------------------------------------------
def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else default
    except FileNotFoundError:
        return default


def _write(path, data):
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(_read(F_SETTINGS, {}))
    return s


def load_questions():
    return _read(F_QUESTIONS, [])


def load_players():
    return _read(F_PLAYERS, {})


def init_storage():
    os.makedirs(IMG_DIR, exist_ok=True)
    with LOCK:
        # If questions.json is missing or truncated to 0 bytes -> reseed
        if not load_questions():
            qs = []
            for i, (text, opts, ans, emo, cap, c1, c2, expl) in enumerate(SEED, 1):
                img = f"q{i:02d}.svg"
                with open(os.path.join(IMG_DIR, img), "w", encoding="utf-8") as f:
                    f.write(_svg(emo, cap, c1, c2, i))
                qs.append({
                    "id": f"q{i:02d}",
                    "type": "tf" if opts == ["True", "False"] else "mc",
                    "text": text,
                    "options": opts,
                    "answer": ans,
                    "image": img,
                    "explanation": expl,
                    "active": True,
                })
            _write(F_QUESTIONS, qs)
        if not os.path.exists(F_SETTINGS) or os.path.getsize(F_SETTINGS) == 0:
            _write(F_SETTINGS, DEFAULT_SETTINGS)
        if not os.path.exists(F_PLAYERS) or os.path.getsize(F_PLAYERS) == 0:
            _write(F_PLAYERS, {})


def secret_key():
    env = os.environ.get("QUIZ_SECRET")
    if env:
        return env
    if os.path.exists(F_SECRET):
        with open(F_SECRET) as f:
            k = f.read().strip()
            if k:
                return k
    k = secrets.token_hex(32)
    with open(F_SECRET, "w") as f:
        f.write(k)
    os.chmod(F_SECRET, 0o600)
    return k


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
init_storage()
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = secret_key()
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def now():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def mask_email(e):
    user, _, dom = e.partition("@")
    return (user[:2] + "•••@" + dom) if user else e


def public_q(q, perm):
    """Question as sent to the browser — no answer key."""
    return {
        "id": q["id"],
        "type": q["type"],
        "text": q["text"],
        "options": [q["options"][i] for i in perm],
        "image": f"images/{q['image']}" if q.get("image") else None,
    }


def ranking_rows():
    players = load_players()
    done = [p for p in players.values() if p.get("finished_at")]
    done.sort(key=lambda p: (-p["score"], p["duration"], p["finished_at"]))
    rows, last, rank = [], None, 0
    for i, p in enumerate(done, 1):
        key = (p["score"], round(p["duration"], 1))
        if key != last:
            rank, last = i, key
        rows.append({
            "rank": rank, "name": p["name"], "email": p["email"],
            "score": p["score"], "total": p["total"],
            "duration": round(p["duration"], 1), "finished_at": iso(p["finished_at"]),
        })
    return rows


def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get("admin"):
            return jsonify(error="Admin login required"), 401
        return fn(*a, **kw)
    return wrap


def err(msg, code=400):
    return jsonify(error=msg), code


# ------------------------------- pages ------------------------------------
@app.get("/")
def index():
    return Response(PAGE_HTML, mimetype="text/html")


@app.get("/admin")
def admin_page():
    return Response(ADMIN_HTML, mimetype="text/html")


@app.get("/images/<path:fname>")
def images(fname):
    resp = send_from_directory(IMG_DIR, fname, max_age=3600)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if fname.lower().endswith(".svg"):
        resp.headers["Content-Security-Policy"] = "script-src 'none'"
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------- player API -------------------------------
@app.get("/api/config")
def api_config():
    s = load_settings()
    active = sum(1 for q in load_questions() if q.get("active", True))
    return jsonify(
        title=s["title"], subtitle=s["subtitle"], quiz_open=s["quiz_open"],
        public_ranking=s["public_ranking"],
        questions_per_quiz=min(int(s["questions_per_quiz"]), active),
    )


@app.post("/api/start")
def api_start():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()[:60]
    email = (d.get("email") or "").strip().lower()[:120]
    if len(name) < 2:
        return err("Please enter your name.")
    if not EMAIL_RE.match(email):
        return err("Please enter a valid email address.")
    s = load_settings()
    with LOCK:
        players = load_players()
        p = players.get(email)
        if p and p.get("finished_at"):
            return err("This email has already completed the quiz. Check the ranking!", 409)
        qs = {q["id"]: q for q in load_questions() if q.get("active", True)}
        if p and all(qid in qs for qid in p["question_ids"]):
            # resume an unfinished attempt (same questions, same clock)
            p["name"] = name
            _write(F_PLAYERS, players)
        else:
            if not s["quiz_open"]:
                return err("The quiz is currently closed.", 403)
            n = min(int(s["questions_per_quiz"]), len(qs))
            if n == 0:
                return err("No questions configured yet.", 503)
            picked = random.sample(list(qs.values()), n)
            perms = {}
            for q in picked:
                perm = list(range(len(q["options"])))
                if s["shuffle_options"] and q["type"] == "mc":
                    random.shuffle(perm)
                perms[q["id"]] = perm
            p = {
                "name": name, "email": email, "token": secrets.token_urlsafe(24),
                "question_ids": [q["id"] for q in picked], "perms": perms,
                "started_at": now(), "finished_at": None, "answers": {},
                "score": None, "total": n, "duration": None,
                "ip": request.remote_addr,
            }
            players[email] = p
            _write(F_PLAYERS, players)
    return jsonify(
        token=p["token"], name=p["name"],
        questions=[public_q(qs[i], p["perms"][i]) for i in p["question_ids"]],
    )


@app.post("/api/finish")
def api_finish():
    d = request.get_json(silent=True) or {}
    token = d.get("token") or ""
    answers = d.get("answers") or {}
    if not isinstance(answers, dict):
        answers = {}
    s = load_settings()
    with LOCK:
        players = load_players()
        ok_tok = isinstance(token, str) and token.isascii() and len(token) < 100
        p = next((x for x in players.values() if secrets.compare_digest(x["token"], token)), None) if (token and ok_tok) else None
        if not p:
            return err("Session not found. Please start again.", 404)
        if p.get("finished_at"):
            return err("Quiz already submitted.", 409)
        qs = {q["id"]: q for q in load_questions()}
        score, review, stored = 0, [], {}
        for qid in p["question_ids"]:
            q = qs.get(qid)
            perm = p["perms"][qid]
            shown = answers.get(qid)
            try:
                shown = int(shown)
                orig = perm[shown] if 0 <= shown < len(perm) else None
            except (TypeError, ValueError):
                orig = None
            ok = q is not None and orig is not None and orig == q["answer"]
            score += 1 if ok else 0
            stored[qid] = orig
            if q:
                review.append({
                    "text": q["text"], "correct": ok,
                    "your_answer": q["options"][orig] if orig is not None else None,
                    "right_answer": q["options"][q["answer"]] if s["reveal_answers"] else None,
                    "explanation": q.get("explanation") if s["reveal_answers"] else None,
                })
        t = now()
        p.update(answers=stored, score=score, finished_at=t, duration=t - p["started_at"])
        _write(F_PLAYERS, players)
    rows = ranking_rows()
    rank = next((r["rank"] for r in rows if r["email"] == p["email"]), None)
    return jsonify(score=score, total=p["total"], duration=round(p["duration"], 1),
                   rank=rank, players=len(rows), review=review)


@app.get("/api/ranking")
def api_ranking():
    s = load_settings()
    if not s["public_ranking"] and not session.get("admin"):
        return err("The ranking is not public yet.", 403)
    rows = ranking_rows()
    for r in rows:
        r["email"] = mask_email(r["email"])
    return jsonify(rows=rows)


# ------------------------------- admin API --------------------------------
@app.post("/api/admin/login")
def admin_login():
    d = request.get_json(silent=True) or {}
    pin = str(d.get("pin") or "")
    if not ADMIN_PIN:
        return err("QUIZ_ADMIN_PIN is not configured on the server.", 503)
    time.sleep(0.4)  # slow down brute force
    if not secrets.compare_digest(pin, ADMIN_PIN):
        return err("Wrong PIN.", 401)
    session["admin"] = True
    return jsonify(ok=True)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/admin/state")
@admin_required
def admin_state():
    players = list(load_players().values())
    for p in players:
        p.pop("token", None)
        p.pop("perms", None)
        p["started_at"] = iso(p.get("started_at"))
        p["finished_at"] = iso(p.get("finished_at"))
    return jsonify(settings=load_settings(), questions=load_questions(), players=players)


def _clean_question(d, existing=None):
    qtype = d.get("type")
    if qtype not in ("mc", "tf"):
        raise ValueError("type must be 'mc' or 'tf'")
    text = (d.get("text") or "").strip()
    if not text:
        raise ValueError("Question text is required")
    if qtype == "tf":
        opts = ["True", "False"]
    else:
        opts = [str(o).strip() for o in (d.get("options") or [])]
        if len(opts) != 4 or not all(opts):
            raise ValueError("Multiple choice needs 4 non-empty options (A–D)")
    ans = int(d.get("answer", -1))
    if not 0 <= ans < len(opts):
        raise ValueError("Select the correct answer")
    q = dict(existing or {})
    q.update(type=qtype, text=text, options=opts, answer=ans,
             explanation=(d.get("explanation") or "").strip(),
             active=bool(d.get("active", True)))
    if "image" in d:
        img = d.get("image") or None
        if img and not os.path.exists(os.path.join(IMG_DIR, os.path.basename(img))):
            raise ValueError("Image not found — upload it first")
        q["image"] = os.path.basename(img) if img else None
    return q


@app.post("/api/admin/questions")
@admin_required
def admin_add_q():
    try:
        q = _clean_question(request.get_json(silent=True) or {})
    except (ValueError, TypeError) as e:
        return err(str(e))
    with LOCK:
        qs = load_questions()
        q["id"] = "q" + uuid.uuid4().hex[:8]
        qs.append(q)
        _write(F_QUESTIONS, qs)
    return jsonify(q)


@app.put("/api/admin/questions/<qid>")
@admin_required
def admin_edit_q(qid):
    with LOCK:
        qs = load_questions()
        idx = next((i for i, q in enumerate(qs) if q["id"] == qid), None)
        if idx is None:
            return err("Not found", 404)
        try:
            qs[idx] = _clean_question(request.get_json(silent=True) or {}, qs[idx])
        except (ValueError, TypeError) as e:
            return err(str(e))
        _write(F_QUESTIONS, qs)
        return jsonify(qs[idx])


@app.delete("/api/admin/questions/<qid>")
@admin_required
def admin_del_q(qid):
    with LOCK:
        qs = load_questions()
        new = [q for q in qs if q["id"] != qid]
        if len(new) == len(qs):
            return err("Not found", 404)
        _write(F_QUESTIONS, new)
    return jsonify(ok=True)


@app.post("/api/admin/upload")
@admin_required
def admin_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No file")
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMG:
        return err("Allowed: " + ", ".join(sorted(ALLOWED_IMG)))
    base = secure_filename(f.filename.rsplit(".", 1)[0])[:40] or "img"
    name = f"{base}-{uuid.uuid4().hex[:6]}.{ext}"
    f.save(os.path.join(IMG_DIR, name))
    return jsonify(image=name, url=f"images/{name}")


@app.put("/api/admin/settings")
@admin_required
def admin_settings():
    d = request.get_json(silent=True) or {}
    with LOCK:
        s = load_settings()
        for k in ("title", "subtitle"):
            if k in d:
                s[k] = str(d[k]).strip()[:120] or DEFAULT_SETTINGS[k]
        if "questions_per_quiz" in d:
            s["questions_per_quiz"] = max(1, min(100, int(d["questions_per_quiz"])))
        for k in ("quiz_open", "public_ranking", "reveal_answers", "shuffle_options"):
            if k in d:
                s[k] = bool(d[k])
        _write(F_SETTINGS, s)
    return jsonify(s)


@app.delete("/api/admin/players/<path:email>")
@admin_required
def admin_del_player(email):
    with LOCK:
        players = load_players()
        if players.pop(email.lower(), None) is None:
            return err("Not found", 404)
        _write(F_PLAYERS, players)
    return jsonify(ok=True)


@app.post("/api/admin/reset")
@admin_required
def admin_reset():
    d = request.get_json(silent=True) or {}
    if d.get("confirm") != "RESET":
        return err("Type RESET to confirm")
    with LOCK:
        _write(F_PLAYERS, {})
    return jsonify(ok=True)


@app.get("/api/admin/export.csv")
@admin_required
def admin_export():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "name", "email", "score", "total", "seconds", "finished_at_utc"])
    for r in ranking_rows():
        w.writerow([r["rank"], r["name"], r["email"], r["score"], r["total"], r["duration"], r["finished_at"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=quiz2026_ranking.csv"})


@app.errorhandler(413)
def too_big(_):
    return err("File too large (max 5 MB)", 413)


# ==========================================================================
# Front-end: player page
# ==========================================================================
PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quiz 2026</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌎</text></svg>">
<style>
:root{
  --bg:#FBF6EE; --card:#FFFFFF; --ink:#1F2933; --muted:#6B7280; --line:#E8E1D5;
  --brand:#0E7C7B; --brand-ink:#FFFFFF; --accent:#F26B38; --gold:#F2B705;
  --ok:#2E9E5B; --bad:#D64545; --sel:#E3F4F3; --shadow:0 10px 30px rgba(31,41,51,.08);
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#11171C; --card:#1A232B; --ink:#E9EEF2; --muted:#9AA6B2; --line:#2A3540;
    --brand:#25A3A1; --sel:#16383A; --shadow:0 10px 30px rgba(0,0,0,.35); }
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.45}
button{font:inherit;cursor:pointer}
header{background:linear-gradient(120deg,var(--brand),#0B5E73);color:#fff;}
.bar{max-width:880px;margin:0 auto;padding:14px 16px;display:flex;align-items:center;gap:12px}
.logo{font-weight:800;font-size:1.1rem;letter-spacing:.2px;display:flex;gap:8px;align-items:center;flex:1;min-width:0}
.logo span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav button{background:rgba(255,255,255,.14);color:#fff;border:1px solid rgba(255,255,255,.25);
  padding:7px 14px;border-radius:999px;font-weight:600}
.nav button.on{background:#fff;color:var(--brand)}
main{max-width:880px;margin:0 auto;padding:22px 16px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}
.hero{padding:34px 28px;text-align:center}
.hero h1{margin:6px 0 6px;font-size:clamp(1.6rem,4vw,2.3rem);line-height:1.15}
.hero p{color:var(--muted);margin:0 0 22px}
.chips{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:22px}
.chip{background:var(--sel);color:var(--brand);font-weight:600;font-size:.85rem;padding:5px 12px;border-radius:999px}
form.reg{max-width:420px;margin:0 auto;text-align:left;display:grid;gap:14px}
label{font-weight:600;font-size:.9rem}
input{width:100%;margin-top:6px;padding:12px 14px;border-radius:12px;border:1.5px solid var(--line);
  background:var(--bg);color:var(--ink);font:inherit}
input:focus{outline:none;border-color:var(--brand)}
.btn{border:0;border-radius:12px;padding:12px 20px;font-weight:700;transition:transform .05s,opacity .2s}
.btn:active{transform:translateY(1px)}
.btn-primary{background:var(--accent);color:#fff}
.btn-ghost{background:transparent;color:var(--ink);border:1.5px solid var(--line)}
.btn[disabled]{opacity:.4;cursor:not-allowed}
.btn-finish{background:var(--ok);color:#fff}
.msg{min-height:1.2em;color:var(--bad);font-weight:600;font-size:.92rem;text-align:center}
/* quiz */
.qhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:600;color:var(--muted);font-size:.92rem}
.progress{height:8px;background:var(--line);border-radius:99px;overflow:hidden;margin-bottom:16px}
.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--brand),var(--accent));transition:width .3s}
.qimg{width:100%;aspect-ratio:2/1;object-fit:cover;display:block;border-radius:18px 18px 0 0;background:var(--line)}
.qbody{padding:20px 22px 22px}
.qtype{display:inline-block;font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--accent);margin-bottom:6px}
.qtext{font-size:clamp(1.15rem,2.6vw,1.4rem);font-weight:700;margin:0 0 18px}
.opts{display:grid;gap:10px}
.opts.tf{grid-template-columns:1fr 1fr}
.opt{display:flex;align-items:center;gap:12px;text-align:left;width:100%;padding:13px 14px;border-radius:14px;
  border:1.5px solid var(--line);background:var(--card);color:var(--ink);font-weight:600}
.opt:hover{border-color:var(--brand)}
.opt .k{flex:none;width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--line);font-weight:800}
.opt.sel{border-color:var(--brand);background:var(--sel)}
.opt.sel .k{background:var(--brand);color:#fff}
.opts.tf .opt{justify-content:center;padding:18px}
.nav-q{display:flex;justify-content:space-between;gap:10px;margin-top:18px}
.dots{display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-top:16px}
.dots b{width:10px;height:10px;border-radius:50%;background:var(--line)}
.dots b.a{background:var(--brand)} .dots b.c{outline:2px solid var(--accent);outline-offset:2px}
/* result */
.result{padding:30px 24px;text-align:center}
.ring{--p:0;width:170px;height:170px;border-radius:50%;margin:8px auto 14px;display:grid;place-items:center;
  background:conic-gradient(var(--ok) calc(var(--p)*1%),var(--line) 0)}
.ring div{width:136px;height:136px;border-radius:50%;background:var(--card);display:grid;place-items:center;font-size:2.2rem;font-weight:800}
.stats{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:16px 0 4px}
.stat{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:10px 18px;min-width:120px}
.stat b{display:block;font-size:1.4rem} .stat span{color:var(--muted);font-size:.85rem}
.review{text-align:left;margin-top:22px;display:grid;gap:8px}
.rv{display:flex;gap:10px;padding:10px 12px;border-radius:12px;background:var(--bg);border:1px solid var(--line);font-size:.93rem}
.rv .i{flex:none;font-weight:800} .rv.ok .i{color:var(--ok)} .rv.no .i{color:var(--bad)}
.rv small{display:block;color:var(--muted)}
/* ranking */
.podium{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;align-items:end;margin:6px 0 18px}
.pod{text-align:center;padding:14px 8px;border-radius:16px 16px 6px 6px;color:#fff;font-weight:700;overflow:hidden}
.pod .n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:.95rem}
.pod .m{font-size:1.8rem}
.pod.p1{background:linear-gradient(#F2B705,#D99A00);min-height:170px}
.pod.p2{background:linear-gradient(#9AA5B1,#7B8794);min-height:140px}
.pod.p3{background:linear-gradient(#D08A4E,#A9652F);min-height:118px}
.pod small{display:block;font-weight:600;opacity:.92}
table{width:100%;border-collapse:collapse}
th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:.93rem}
th{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.5px}
td.r,th.r{text-align:right}
tr.me td{background:var(--sel);font-weight:700}
.empty{padding:40px;text-align:center;color:var(--muted)}
.hide{display:none!important}
@media (max-width:560px){
  .opts.tf{grid-template-columns:1fr}
  .hero{padding:26px 18px} .qbody{padding:16px}
  th:nth-child(3),td:nth-child(3){display:none}
}
</style>
</head>
<body>
<header><div class="bar">
  <div class="logo">🌎 <span id="brand">Quiz 2026</span></div>
  <div class="nav"><button id="navPlay" class="on">Play</button> <button id="navRank">Ranking</button></div>
</div></header>
<main id="app"></main>

<script>
const $ = (s,el=document)=>el.querySelector(s);
const app = $('#app');
const LS = 'quiz2026_state';
let CFG = null;
let S = load();           // {token,name,email,questions,answers,idx,t0} | {result,...}

function load(){ try{ return JSON.parse(localStorage.getItem(LS)) || {}; }catch(e){ return {}; } }
function save(){ try{ localStorage.setItem(LS, JSON.stringify(S)); }catch(e){} }
function esc(s){ return String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtTime(sec){ sec=Math.max(0,Math.round(sec)); const m=Math.floor(sec/60), s=sec%60; return m+':'+String(s).padStart(2,'0'); }
function maskEmail(e){ const [u,d]=(e||'').split('@'); return u ? u.slice(0,2)+'•••@'+d : e; }
async function api(path, body){
  const r = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const j = await r.json().catch(()=>({error:'Server error'}));
  if(!r.ok) throw new Error(j.error || 'Request failed');
  return j;
}
function setNav(which){ $('#navPlay').classList.toggle('on',which==='play'); $('#navRank').classList.toggle('on',which==='rank'); }

let timerId=null;
function stopTimer(){ if(timerId){ clearInterval(timerId); timerId=null; } }

// ---------------- views ----------------
function viewRegister(errMsg=''){
  stopTimer(); setNav('play');
  const n = CFG ? CFG.questions_per_quiz : 10;
  app.innerHTML = `
  <section class="card hero">
    <div style="font-size:3rem">🌎</div>
    <h1>${esc(CFG?.title || 'Quiz')}</h1>
    <p>${esc(CFG?.subtitle || '')}</p>
    <div class="chips"><span class="chip">${n} questions</span><span class="chip">A/B/C/D & True/False</span><span class="chip">⏱ Fastest wins ties</span></div>
    ${CFG && !CFG.quiz_open ? '<p class="msg">The quiz is currently closed.</p>' : ''}
    <form class="reg" id="reg" novalidate>
      <label>Your name<input id="name" autocomplete="name" maxlength="60" value="${esc(S.lastName||'')}" required></label>
      <label>Email<input id="email" type="email" autocomplete="email" maxlength="120" value="${esc(S.lastEmail||'')}" required></label>
      <button class="btn btn-primary" id="go">Start the quiz →</button>
      <div class="msg" id="msg">${esc(errMsg)}</div>
    </form>
  </section>`;
  $('#reg').onsubmit = async (e)=>{
    e.preventDefault();
    const name=$('#name').value.trim(), email=$('#email').value.trim();
    const btn=$('#go'); btn.disabled=true; $('#msg').textContent='';
    try{
      const j = await api('api/start',{name,email});
      const prev = (S.token===j.token) ? S : null;     // resuming on same browser
      S = { token:j.token, name:j.name, email:email.toLowerCase(), questions:j.questions,
            answers: prev ? prev.answers : {}, idx: prev ? prev.idx : 0, t0: prev ? prev.t0 : Date.now(),
            lastName:name, lastEmail:email };
      save(); viewQuestion();
    }catch(err){ $('#msg').textContent = err.message; btn.disabled=false; }
  };
}

function viewQuestion(){
  setNav('play');
  const qs=S.questions, i=S.idx, q=qs[i], n=qs.length, last = i===n-1;
  const chosen = S.answers[q.id];
  const letters = ['A','B','C','D','E','F'];
  app.innerHTML = `
  <div class="qhead"><span>Question ${i+1} of ${n}</span><span>⏱ <span id="clock">0:00</span></span></div>
  <div class="progress"><i style="width:${(Object.keys(S.answers).length/n)*100}%"></i></div>
  <article class="card">
    ${q.image ? `<img class="qimg" src="${esc(q.image)}" alt="">` : ''}
    <div class="qbody">
      <span class="qtype">${q.type==='tf' ? 'True or False' : 'Multiple choice'}</span>
      <h2 class="qtext">${esc(q.text)}</h2>
      <div class="opts ${q.type==='tf'?'tf':''}" role="radiogroup">
        ${q.options.map((o,k)=>`<button class="opt ${chosen===k?'sel':''}" data-k="${k}" role="radio" aria-checked="${chosen===k}">
            ${q.type==='tf' ? '' : `<span class="k">${letters[k]}</span>`}<span>${q.type==='tf' ? (k===0?'✔ ':'✘ ') : ''}${esc(o)}</span></button>`).join('')}
      </div>
      <div class="nav-q">
        <button class="btn btn-ghost" id="back" ${i===0?'disabled':''}>← Back</button>
        ${last ? `<button class="btn btn-finish" id="next" ${chosen===undefined?'disabled':''}>Finish ✓</button>`
               : `<button class="btn btn-primary" id="next" ${chosen===undefined?'disabled':''}>Next →</button>`}
      </div>
      <div class="msg" id="msg"></div>
    </div>
  </article>
  <div class="dots">${qs.map((x,k)=>`<b class="${S.answers[x.id]!==undefined?'a':''} ${k===i?'c':''}"></b>`).join('')}</div>`;

  app.querySelectorAll('.opt').forEach(b=>b.onclick=()=>{
    S.answers[q.id]=+b.dataset.k; save(); viewQuestion();
  });
  $('#back').onclick=()=>{ if(S.idx>0){ S.idx--; save(); viewQuestion(); } };
  $('#next').onclick= last ? finish : ()=>{ S.idx++; save(); viewQuestion(); };
  const tick=()=>{ const c=$('#clock'); if(c) c.textContent=fmtTime((Date.now()-S.t0)/1000); };
  stopTimer(); tick(); timerId=setInterval(tick,1000);
  window.scrollTo({top:0});
}

async function finish(){
  const btn=$('#next'); btn.disabled=true; btn.textContent='Submitting…';
  const missing = S.questions.filter(q=>S.answers[q.id]===undefined);
  if(missing.length){ $('#msg').textContent='Please answer all questions.'; btn.disabled=false; btn.textContent='Finish ✓'; return; }
  try{
    const r = await api('api/finish',{token:S.token, answers:S.answers});
    S = { result:r, name:S.name, email:S.email, lastName:S.lastName, lastEmail:S.lastEmail };
    save(); viewResult();
  }catch(err){
    $('#msg').textContent=err.message; btn.disabled=false; btn.textContent='Finish ✓';
  }
}

function viewResult(){
  stopTimer(); setNav('play');
  const r=S.result, pct=Math.round(r.score/r.total*100);
  const msg = pct===100?'Perfect score! 🏆': pct>=80?'Excellent! 🎉': pct>=50?'Nice job! 👏':'Thanks for playing! 🙌';
  app.innerHTML = `
  <section class="card result">
    <h1 style="margin:0">${msg}</h1>
    <p style="color:var(--muted);margin:4px 0 0">${esc(S.name)}</p>
    <div class="ring" style="--p:${pct}"><div>${r.score}/${r.total}</div></div>
    <div class="stats">
      <div class="stat"><b>#${r.rank ?? '-'}</b><span>of ${r.players} players</span></div>
      <div class="stat"><b>${fmtTime(r.duration)}</b><span>total time</span></div>
      <div class="stat"><b>${pct}%</b><span>correct</span></div>
    </div>
    <div class="review">${(r.review||[]).map((x,k)=>`
      <div class="rv ${x.correct?'ok':'no'}"><span class="i">${x.correct?'✔':'✘'}</span>
        <div>${k+1}. ${esc(x.text)}
          <small>Your answer: ${esc(x.your_answer ?? '—')}${x.right_answer && !x.correct ? ' · Correct: <b>'+esc(x.right_answer)+'</b>' : ''}</small>
          ${x.explanation ? `<small>${esc(x.explanation)}</small>` : ''}
        </div></div>`).join('')}</div>
    <div style="margin-top:22px"><button class="btn btn-primary" id="seeRank">See the ranking →</button>
      <button class="btn btn-ghost" id="newP">New player</button></div>
  </section>`;
  $('#seeRank').onclick=()=>{ location.hash='#ranking'; };
  $('#newP').onclick=()=>{ S={}; save(); viewRegister(); };
}

async function viewRanking(){
  stopTimer(); setNav('rank');
  app.innerHTML = `<section class="card" style="padding:22px"><h1 style="margin:0 0 14px">🏆 Ranking</h1><div id="rk" class="empty">Loading…</div></section>`;
  let rows;
  try{ rows = (await api('api/ranking')).rows; }
  catch(err){ $('#rk').textContent = err.message; return; }
  if(!rows.length){ $('#rk').textContent='No one has finished yet — be the first!'; return; }
  const me = S.email ? maskEmail(S.email) : null;
  const pod = [rows[1],rows[0],rows[2]];
  const medal = {1:'🥇',2:'🥈',3:'🥉'};
  $('#rk').className='';
  $('#rk').innerHTML = `
   <div class="podium">${pod.map((p,k)=> p ? `<div class="pod p${[2,1,3][k]}"><div class="m">${medal[[2,1,3][k]]}</div>
      <div class="n">${esc(p.name)}</div><small>${p.score}/${p.total} · ${fmtTime(p.duration)}</small></div>` : '<div></div>').join('')}</div>
   <div style="overflow-x:auto"><table>
    <thead><tr><th>#</th><th>Name</th><th>Email</th><th class="r">Score</th><th class="r">Time</th></tr></thead>
    <tbody>${rows.map(r=>`<tr class="${me && r.email===me && r.name===S.name ? 'me':''}">
      <td>${medal[r.rank]||r.rank}</td><td>${esc(r.name)}</td><td>${esc(r.email)}</td>
      <td class="r">${r.score}/${r.total}</td><td class="r">${fmtTime(r.duration)}</td></tr>`).join('')}</tbody>
   </table></div>`;
}

function route(){
  if(location.hash==='#ranking') return viewRanking();
  if(S.token && S.questions) return viewQuestion();
  if(S.result) return viewResult();
  return viewRegister();
}
$('#navRank').onclick=()=>{ location.hash='#ranking'; };
$('#navPlay').onclick=()=>{ if(location.hash){ history.pushState('',document.title,location.pathname); } route(); };
window.addEventListener('hashchange', route);

(async()=>{
  try{ CFG = await api('api/config'); $('#brand').textContent=CFG.title; document.title=CFG.title;
       if(!CFG.public_ranking) $('#navRank').classList.add('hide'); }catch(e){}
  route();
})();
</script>
</body>
</html>
"""


# ==========================================================================
# Front-end: admin page
# ==========================================================================
ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Quiz Admin</title>
<style>
:root{--bg:#F4F6F8;--card:#fff;--ink:#1F2933;--muted:#6B7280;--line:#E2E8F0;--brand:#0E7C7B;--accent:#F26B38;--ok:#2E9E5B;--bad:#D64545;--sel:#E3F4F3}
@media (prefers-color-scheme:dark){:root{--bg:#11171C;--card:#1A232B;--ink:#E9EEF2;--muted:#9AA6B2;--line:#2A3540;--brand:#25A3A1;--sel:#16383A}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
button,input,select,textarea{font:inherit;color:inherit}
header{background:#0B3B47;color:#fff}
.bar{max-width:1100px;margin:0 auto;padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bar b{flex:1}
.tabs button{background:transparent;border:1px solid rgba(255,255,255,.3);color:#fff;border-radius:999px;padding:6px 14px;cursor:pointer}
.tabs button.on{background:#fff;color:#0B3B47}
main{max-width:1100px;margin:0 auto;padding:18px 16px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.btn{border:0;border-radius:10px;padding:9px 14px;font-weight:700;cursor:pointer;background:var(--brand);color:#fff}
.btn.sec{background:transparent;border:1.5px solid var(--line);color:var(--ink)}
.btn.danger{background:var(--bad)}
.btn.sm{padding:5px 10px;font-size:.85rem}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;padding:9px 11px;border:1.5px solid var(--line);border-radius:10px;background:var(--bg)}
textarea{min-height:70px}
label{display:block;font-weight:600;font-size:.88rem;margin:10px 0 4px}
.grid{display:grid;gap:12px}
.qrow{display:grid;grid-template-columns:96px 1fr auto;gap:14px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.qrow img{width:96px;height:54px;object-fit:cover;border-radius:8px;background:var(--line)}
.qrow .t{font-weight:600}
.qrow small{color:var(--muted)}
.tag{font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:99px;background:var(--sel);color:var(--brand);margin-right:6px}
.tag.off{background:#FDE2E2;color:var(--bad)}
.optrow{display:grid;grid-template-columns:28px 34px 1fr;gap:8px;align-items:center;margin:6px 0}
.preview{width:100%;max-width:360px;aspect-ratio:2/1;object-fit:cover;border-radius:10px;border:1px solid var(--line);background:var(--line);display:block}
table{width:100%;border-collapse:collapse}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:.9rem}
th{color:var(--muted);font-size:.75rem;text-transform:uppercase}
.msg{font-weight:600;min-height:1.2em;margin-top:8px}
.msg.err{color:var(--bad)} .msg.ok{color:var(--ok)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.sw{display:flex;gap:10px;align-items:center;margin:10px 0;font-weight:600}
.hide{display:none!important}
dialog{border:0;border-radius:16px;padding:0;max-width:720px;width:calc(100% - 24px);background:var(--card);color:var(--ink)}
dialog::backdrop{background:rgba(0,0,0,.45)}
@media (max-width:600px){.qrow{grid-template-columns:64px 1fr}.qrow img{width:64px;height:36px}.qrow .act{grid-column:1/-1}}
</style>
</head>
<body>
<header><div class="bar"><b>🌎 Quiz Admin</b>
  <div class="tabs hide" id="tabs">
    <button data-t="questions" class="on">Questions</button>
    <button data-t="players">Players</button>
    <button data-t="settings">Settings</button>
    <button id="logout">Logout</button>
  </div>
  <a href="./" style="color:#fff">Open quiz ↗</a>
</div></header>
<main>
  <section class="card" id="login" style="max-width:380px;margin:40px auto">
    <h2 style="margin-top:0">Admin login</h2>
    <form id="lf"><label>PIN<input type="password" id="pin" autocomplete="current-password"></label>
    <div style="margin-top:12px"><button class="btn">Login</button></div><div class="msg err" id="lmsg"></div></form>
  </section>

  <section id="v-questions" class="hide">
    <div class="card">
      <div class="row" style="justify-content:space-between"><h2 style="margin:0">Question bank <small id="qcount" style="color:var(--muted);font-weight:500"></small></h2>
      <button class="btn" id="addQ">+ Add question</button></div>
      <div id="qlist"></div>
    </div>
  </section>

  <section id="v-players" class="hide">
    <div class="card">
      <div class="row" style="justify-content:space-between"><h2 style="margin:0">Players</h2>
        <div class="row"><a class="btn sec" href="api/admin/export.csv">Export CSV</a><button class="btn danger" id="reset">Reset all players</button></div></div>
      <div style="overflow-x:auto"><table><thead><tr><th>Name</th><th>Email</th><th>Status</th><th>Score</th><th>Time (s)</th><th>Started (UTC)</th><th></th></tr></thead><tbody id="plist"></tbody></table></div>
    </div>
  </section>

  <section id="v-settings" class="hide">
    <div class="card" style="max-width:620px">
      <h2 style="margin-top:0">Settings</h2>
      <label>Title<input type="text" id="s_title"></label>
      <label>Subtitle<input type="text" id="s_subtitle"></label>
      <label>Questions per quiz<input type="number" id="s_n" min="1" max="100"></label>
      <div class="sw"><input type="checkbox" id="s_open"> Quiz open for new players</div>
      <div class="sw"><input type="checkbox" id="s_rank"> Ranking is public</div>
      <div class="sw"><input type="checkbox" id="s_reveal"> Reveal correct answers on result page</div>
      <div class="sw"><input type="checkbox" id="s_shuffle"> Shuffle A/B/C/D order per player</div>
      <button class="btn" id="saveS">Save settings</button><div class="msg" id="smsg"></div>
    </div>
  </section>
</main>

<dialog id="dlg"><form method="dialog" id="qf" style="padding:20px">
  <h2 style="margin-top:0" id="dlgT">Edit question</h2>
  <div class="row"><div style="flex:1;min-width:160px"><label>Type<select id="f_type"><option value="mc">Multiple choice (A/B/C/D)</option><option value="tf">True / False</option></select></label></div>
  <div class="sw" style="margin-top:30px"><input type="checkbox" id="f_active" checked> Active</div></div>
  <label>Question<textarea id="f_text" required></textarea></label>
  <label>Answers <small style="font-weight:400;color:var(--muted)">(select the correct one)</small></label>
  <div id="f_opts"></div>
  <label>Explanation (optional, shown if “reveal answers” is on)<input type="text" id="f_expl"></label>
  <label>Image</label>
  <img id="f_prev" class="preview" alt="">
  <div class="row" style="margin-top:8px"><input type="file" id="f_file" accept=".png,.jpg,.jpeg,.gif,.webp,.svg"><button type="button" class="btn sec sm" id="f_noimg">Remove image</button></div>
  <div class="msg err" id="fmsg"></div>
  <div class="row" style="justify-content:flex-end;margin-top:12px">
    <button type="button" class="btn sec" id="f_cancel">Cancel</button><button type="button" class="btn" id="f_save">Save</button></div>
</form></dialog>

<script>
const $=(s)=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let ST=null, editing=null, curImg=null;
async function api(path,method='GET',body){
  const o={method,headers:{}};
  if(body instanceof FormData){o.body=body}else if(body){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  const r=await fetch(path,o); const j=await r.json().catch(()=>({error:'Server error'}));
  if(r.status===401 && path!=='api/admin/login'){showLogin();}
  if(!r.ok) throw new Error(j.error||'Failed'); return j;
}
function showLogin(){ $('#login').classList.remove('hide'); $('#tabs').classList.add('hide'); ['questions','players','settings'].forEach(t=>$('#v-'+t).classList.add('hide')); }
function tab(t){ document.querySelectorAll('#tabs button[data-t]').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
  ['questions','players','settings'].forEach(x=>$('#v-'+x).classList.toggle('hide',x!==t)); }
async function refresh(){ ST=await api('api/admin/state'); $('#login').classList.add('hide'); $('#tabs').classList.remove('hide');
  renderQ(); renderP(); renderS(); }

function renderQ(){
  const act=ST.questions.filter(q=>q.active!==false).length;
  $('#qcount').textContent=`· ${ST.questions.length} total, ${act} active, ${ST.settings.questions_per_quiz} per player`;
  $('#qlist').innerHTML=ST.questions.map((q,i)=>`<div class="qrow">
    ${q.image?`<img src="images/${esc(q.image)}" alt="">`:'<img alt="">'}
    <div><div class="t">${i+1}. ${esc(q.text)}</div>
      <small><span class="tag">${q.type==='tf'?'True/False':'A/B/C/D'}</span>${q.active===false?'<span class="tag off">inactive</span>':''}
      ✅ ${esc(q.options[q.answer])}</small></div>
    <div class="act row"><button class="btn sec sm" data-e="${q.id}">Edit</button><button class="btn danger sm" data-d="${q.id}">Delete</button></div></div>`).join('');
  document.querySelectorAll('[data-e]').forEach(b=>b.onclick=()=>openQ(ST.questions.find(q=>q.id===b.dataset.e)));
  document.querySelectorAll('[data-d]').forEach(b=>b.onclick=async()=>{ if(!confirm('Delete this question?'))return; await api('api/admin/questions/'+b.dataset.d,'DELETE'); refresh(); });
}
function renderP(){
  const ps=[...ST.players].sort((a,b)=>(b.score??-1)-(a.score??-1)||(a.duration??1e9)-(b.duration??1e9));
  $('#plist').innerHTML=ps.length?ps.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.email)}</td>
    <td>${p.finished_at?'✅ finished':'⏳ in progress'}</td><td>${p.score??'-'}/${p.total}</td>
    <td>${p.duration?p.duration.toFixed(1):'-'}</td><td>${esc((p.started_at||'').slice(0,19).replace('T',' '))}</td>
    <td><button class="btn danger sm" data-p="${esc(p.email)}">Delete</button></td></tr>`).join(''):'<tr><td colspan="7" style="color:var(--muted)">No players yet.</td></tr>';
  document.querySelectorAll('[data-p]').forEach(b=>b.onclick=async()=>{ if(!confirm('Delete '+b.dataset.p+'? They will be able to play again.'))return;
    await api('api/admin/players/'+encodeURIComponent(b.dataset.p),'DELETE'); refresh(); });
}
function renderS(){ const s=ST.settings;
  $('#s_title').value=s.title; $('#s_subtitle').value=s.subtitle; $('#s_n').value=s.questions_per_quiz;
  $('#s_open').checked=s.quiz_open; $('#s_rank').checked=s.public_ranking; $('#s_reveal').checked=s.reveal_answers; $('#s_shuffle').checked=s.shuffle_options; }

function drawOpts(type, opts, ans){
  const L=['A','B','C','D'];
  $('#f_opts').innerHTML = type==='tf'
   ? ['True','False'].map((o,k)=>`<div class="optrow"><input type="radio" name="ans" value="${k}" ${ans===k?'checked':''}><b>${k?'F':'T'}</b><span>${o}</span></div>`).join('')
   : L.map((l,k)=>`<div class="optrow"><input type="radio" name="ans" value="${k}" ${ans===k?'checked':''}><b>${l}</b><input type="text" class="optin" value="${esc(opts[k]||'')}"></div>`).join('');
}
function setPrev(img){ curImg=img; const p=$('#f_prev'); if(img){p.src='images/'+img;p.classList.remove('hide')}else{p.removeAttribute('src');p.classList.add('hide')} }
function openQ(q){
  editing=q||null; $('#dlgT').textContent=q?'Edit question':'Add question'; $('#fmsg').textContent='';
  $('#f_type').value=q?q.type:'mc'; $('#f_text').value=q?q.text:''; $('#f_expl').value=q?.explanation||'';
  $('#f_active').checked=q?q.active!==false:true; $('#f_file').value='';
  drawOpts($('#f_type').value, q&&q.type==='mc'?q.options:[], q?q.answer:-1); setPrev(q?.image||null);
  $('#dlg').showModal();
}
$('#f_type').onchange=()=>{ const cur=[...document.querySelectorAll('.optin')].map(i=>i.value); drawOpts($('#f_type').value,cur,-1); };
$('#f_file').onchange=async()=>{ const f=$('#f_file').files[0]; if(!f)return; const fd=new FormData(); fd.append('file',f);
  try{ const j=await api('api/admin/upload','POST',fd); setPrev(j.image); $('#fmsg').textContent=''; }catch(e){ $('#fmsg').textContent=e.message; } };
$('#f_noimg').onclick=()=>{ setPrev(null); $('#f_file').value=''; };
$('#f_cancel').onclick=()=>$('#dlg').close();
$('#f_save').onclick=async()=>{
  const a=document.querySelector('input[name=ans]:checked');
  const body={type:$('#f_type').value,text:$('#f_text').value,explanation:$('#f_expl').value,active:$('#f_active').checked,
    options:[...document.querySelectorAll('.optin')].map(i=>i.value), answer:a?+a.value:-1, image:curImg};
  try{ if(editing) await api('api/admin/questions/'+editing.id,'PUT',body); else await api('api/admin/questions','POST',body);
    $('#dlg').close(); refresh(); }catch(e){ $('#fmsg').textContent=e.message; }
};
$('#addQ').onclick=()=>openQ(null);
$('#saveS').onclick=async()=>{ const m=$('#smsg');
  try{ await api('api/admin/settings','PUT',{title:$('#s_title').value,subtitle:$('#s_subtitle').value,questions_per_quiz:+$('#s_n').value,
    quiz_open:$('#s_open').checked,public_ranking:$('#s_rank').checked,reveal_answers:$('#s_reveal').checked,shuffle_options:$('#s_shuffle').checked});
    m.className='msg ok'; m.textContent='Saved.'; refresh(); }catch(e){ m.className='msg err'; m.textContent=e.message; } };
$('#reset').onclick=async()=>{ const c=prompt('This deletes ALL players and the ranking. Type RESET to confirm:'); if(c!=='RESET')return;
  try{ await api('api/admin/reset','POST',{confirm:'RESET'}); refresh(); }catch(e){ alert(e.message); } };
document.querySelectorAll('#tabs button[data-t]').forEach(b=>b.onclick=()=>tab(b.dataset.t));
$('#logout').onclick=async()=>{ await api('api/admin/logout','POST'); showLogin(); };
$('#lf').onsubmit=async e=>{ e.preventDefault(); $('#lmsg').textContent='';
  try{ await api('api/admin/login','POST',{pin:$('#pin').value}); $('#pin').value=''; await refresh(); tab('questions'); }catch(err){ $('#lmsg').textContent=err.message; } };
refresh().then(()=>tab('questions')).catch(()=>showLogin());
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("QUIZ_PORT", "5052")), debug=False)
