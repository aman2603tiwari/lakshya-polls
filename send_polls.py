"""
send_polls.py  —  Lakshya JEE 2027 Automation
===============================================
Modes:
  --mode=motivation  (8 AM)  → generate image → upload → send to all groups
  --mode=quiz        (1 PM)  → intro message + 5 PYQ polls → all groups
  --mode=solution    (10 PM) → 5 solution messages → all groups

GitHub Secrets required:
  PW_TOKEN      — Bearer token (expires ~7 days, refresh from Network tab)
  GROQ_API_KEY  — from console.groq.com
  ALERT_EMAIL   — your email for failure alerts
  GMAIL_APP_PWD — 16-char Gmail App Password
"""

import os, sys, json, random, time, argparse, smtplib, traceback
from datetime import date, datetime
from pathlib import Path
from email.mime.text import MIMEText

import requests
from groq import Groq

# ─── SECRETS ──────────────────────────────────────────────────────────────────

PW_TOKEN      = os.environ["PW_TOKEN"]
GROQ_API_KEY  = os.environ["GROQ_API_KEY"]
ALERT_EMAIL   = os.environ.get("ALERT_EMAIL", "")
GMAIL_APP_PWD = os.environ.get("GMAIL_APP_PWD", "")

# ─── PW API CONFIG ────────────────────────────────────────────────────────────

BASE_URL  = "https://api.penpencil.co"
CLIENT_ID = "5eb393ee95fab7468a79d189"
BATCH_ID  = "6779345c20fa0756e4a7fd08"

HEADERS = {
    "Authorization":  f"Bearer {PW_TOKEN}",
    "client-id":      CLIENT_ID,
    "client-type":    "WEB",
    "origin":         "https://www.pw.live",
    "referer":        "https://www.pw.live/",
    "x-sdk-version":  "0.0.28",
    "randomid":       "2f81cbed-4d22-4f57-994e-3f78dbf6e309",
}

JSON_HEADERS = {**HEADERS, "Content-Type": "application/json"}

# ─── GROUPS ───────────────────────────────────────────────────────────────────

GROUPS = [
    {"name": "Group 1", "groupId": "69cb7c5e4a6bd7893a91aa22", "conversationId": "69ce5c7c8a5087b50b14c482"},
    {"name": "Group 2", "groupId": "69cb7c67e223436a272111c9", "conversationId": "69ce5d26b3e8f731557c9116"},
    {"name": "Group 3", "groupId": "69cb7c6cd6e4a400b77ebccc", "conversationId": "69ce5f7b369fd47f199d51a8"},
    {"name": "Group 4", "groupId": "69cb7c7036b09e3dea135a30", "conversationId": "69ce60754bd6bffed97b9eeb"},
    {"name": "Group 5", "groupId": "69cb7c7426c54583a30f3039", "conversationId": "69ce60a65155c4ac4c289fea"},
]

# ─── SUBJECT ROTATION ─────────────────────────────────────────────────────────

SUBJECT_MIXES = [
    ("Physics",   "Physics",   "Chemistry", "Chemistry", "Maths"),     # Mon
    ("Maths",     "Maths",     "Physics",   "Chemistry", "Chemistry"), # Tue
    ("Chemistry", "Chemistry", "Maths",     "Maths",     "Physics"),   # Wed
    ("Physics",   "Maths",     "Chemistry", "Physics",   "Maths"),     # Thu
    ("Maths",     "Physics",   "Physics",   "Chemistry", "Maths"),     # Fri
]

# ─── FILE PATHS ───────────────────────────────────────────────────────────────

HISTORY_FILE = Path("history.json")
TODAY_Q_FILE = Path("todays_questions.json")
PDF_DIR      = Path("pdfs")

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def send_alert(subject, body):
    if not ALERT_EMAIL or not GMAIL_APP_PWD:
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = ALERT_EMAIL
        msg["To"]      = ALERT_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(ALERT_EMAIL, GMAIL_APP_PWD)
            s.send_message(msg)
        log("Alert email sent.")
    except Exception as e:
        log(f"[WARN] Email alert failed: {e}")


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ─── PW API CALLS ─────────────────────────────────────────────────────────────

def check_token():
    log("Token check skipped — validated by API calls.")
    return True


def upload_image(image_path: str) -> str:
    """
    Upload image to PW → return imageId.
    POST /v1/files  (multipart/form-data)
    """
    path = Path(image_path)
    log(f"Uploading image: {path.name} ({path.stat().st_size // 1024} KB)")

    with open(path, "rb") as f:
        files = {"file": (path.name, f, "image/jpeg")}
        r = requests.post(
            f"{BASE_URL}/v1/files",
            headers=HEADERS,   # no Content-Type — requests sets multipart boundary automatically
            files=files,
            timeout=30
        )

    log(f"Upload response: {r.status_code} {r.text[:300]}")

    if r.status_code in (200, 201):
        data = r.json()
        image_id = (
            data.get("data", {}).get("_id")
            or data.get("data", {}).get("imageId")
            or data.get("_id")
            or data.get("imageId")
        )
        if not image_id:
            raise Exception(f"imageId not found in upload response: {data}")
        log(f"✅ Image uploaded → imageId: {image_id}")
        return image_id
    else:
        raise Exception(f"Upload failed: {r.status_code} {r.text[:300]}")


def send_message(group, text):
    """Send a plain text message to a group."""
    payload = {
        "batchId":   BATCH_ID,
        "groupId":   group["groupId"],
        "role":      "Mentor",
        "type":      "text",
        "text":      text,
        "filePages": 0,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat",
            headers=JSON_HEADERS, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Message → {group['name']}")
        else:
            log(f"  ⚠️  Message failed → {group['name']}: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"  ❌ Message error → {group['name']}: {e}")
    time.sleep(1)


def send_image_message(group, image_id, file_size_kb):
    """Send an image message using an already-uploaded imageId."""
    payload = {
        "batchId":   BATCH_ID,
        "groupId":   group["groupId"],
        "role":      "Mentor",
        "type":      "image",
        "imageId":   image_id,
        "filePages": 0,
        "fileSize":  file_size_kb,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat",
            headers=JSON_HEADERS, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Image sent → {group['name']}")
        else:
            log(f"  ⚠️  Image failed → {group['name']}: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"  ❌ Image error → {group['name']}: {e}")
    time.sleep(1)


def send_poll(group, question):
    """Send a single poll to a group."""
    options = question["options"]
    correct = question["correct"]   # 1-indexed

    payload = {
        "batchId":       BATCH_ID,
        "groupId":       group["groupId"],
        "role":          "Mentor",
        "question":      question["question"],
        "options":       [{"text": opt} for opt in options],
        "correctOption": correct - 1,   # 0-indexed for API
        "duration":      86400,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v2/poll/create-poll",
            headers=JSON_HEADERS, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Poll → {group['name']}: {question['question'][:50]}...")
        else:
            log(f"  ⚠️  Poll failed → {group['name']}: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"  ❌ Poll error → {group['name']}: {e}")
    time.sleep(1.5)


# ─── GROQ HELPERS ─────────────────────────────────────────────────────────────

groq_client = Groq(api_key=GROQ_API_KEY)


def sample_pyq_text(subject, chars=3000):
    fname = PDF_DIR / f"{subject.lower()}_pyq.txt"
    if not fname.exists():
        return f"[No PYQ file for {subject} — use general JEE knowledge]"
    text = fname.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= chars:
        return text
    start = random.randint(0, len(text) - chars)
    return text[start: start + chars]


def generate_questions(subjects):
    subject_list  = "\n".join(f"Q{i+1}: {s}" for i, s in enumerate(subjects))
    pyq_samples   = {s: sample_pyq_text(s) for s in set(subjects)}
    context_block = "\n\n".join(
        f"=== {s} PYQ SAMPLE ===\n{t}" for s, t in pyq_samples.items()
    )

    prompt = f"""You are a JEE question expert. Generate exactly 5 JEE PYQ questions.

Subject assignment:
{subject_list}

PYQ MATERIAL:
{context_block}

RULES:
- Each question MUST start with year/session tag e.g. [JEE Main 2022 June S1]
- 4 options per question (A B C D)
- correct is 1-4 (1=A, 2=B, 3=C, 4=D)
- solution: 3-5 step working
- No LaTeX backslashes — plain text math only (e.g. v^2 = u^2 + 2as)
- Real JEE questions only

Return ONLY a JSON array:
[
  {{
    "subject": "Physics",
    "year_tag": "[JEE Main 2023 Jan S2]",
    "question": "full question text",
    "options": ["A text", "B text", "C text", "D text"],
    "correct": 2,
    "solution": "Step 1: ...\\nStep 2: ...\\nStep 3: ..."
  }}
]"""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        for key in parsed:
            if isinstance(parsed[key], list):
                return parsed[key]
    except Exception as e:
        log(f"[WARN] Question JSON parse error: {e}")
    return []


def generate_intro_message(subjects):
    subject_str = ", ".join(subjects)
    prompt = f"""Write a short energetic motivational message before a JEE daily quiz.

Today's subjects: {subject_str}
Today's date: {date.today().strftime('%A, %d %B %Y')}

Rules:
- 3-5 lines max
- Mention today's subjects naturally
- End with hype to answer polls
- Sound like a real teacher/mentor
- English only, fresh every day

Return ONLY the message text."""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=150,
    )
    return resp.choices[0].message.content.strip()


def generate_motivation_quote():
    system = """You write deeply authentic motivational quotes for JEE/IIT aspirants in ENGLISH ONLY.

Raw, real — like something a topper or struggling student actually thinks at 2 AM.

RULES:
✅ English only
✅ Specific to JEE: mock ranks, rank drops, 3 AM studying, Kota pressure, PCM, parents sacrifices
✅ 1-4 lines max. Punchy.
✅ Make the student FEEL seen, not lectured
BANNED: "Never give up", "Believe in yourself", "Work hard", any generic cliche

GREAT EXAMPLES:
- "Your rank dropped 3000. Your parents said nothing. That silence is the heaviest weight you'll carry into that exam room."
- "The integration you couldn't solve at midnight — that's the one on JEE paper. Sit back down."
- "Every topper in that rank list had a night they wanted to quit. You're in that night right now. Stay."
- "The student who scores 99 percentile doesn't work harder than you. They waste less."
- "Your mock test is a mirror. You don't break the mirror because you don't like what you see."
- "Kota didn't break you. The idea of going home empty-handed will."

Return ONLY JSON: {"quote": "quote text, use \\n for line breaks"}"""

    categories = [
        "discipline_and_consistency", "exam_pressure_and_fear",
        "parents_sacrifice", "comeback_after_failure",
        "late_night_study_grind", "mock_test_mindset",
        "iit_dream_visualization", "competition_mindset",
        "time_management", "mental_toughness",
    ]
    cat = categories[date.today().toordinal() % len(categories)]

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": f"Category: {cat}\nSeed: {date.today().toordinal()}"},
        ],
        temperature=0.88,
        max_tokens=200,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content.strip()
    try:
        return json.loads(raw).get("quote", raw)
    except Exception:
        return raw


# ─── MODE: MOTIVATION (8 AM) ──────────────────────────────────────────────────

def run_motivation():
    log("=== MODE: MOTIVATION (8 AM) ===")

    # 1. Generate quote
    log("Generating quote via Groq...")
    quote = generate_motivation_quote()
    log(f"Quote: {quote[:80]}...")

    # 2. Render image
    log("Rendering motivation image...")
    from generate_motivation import render
    img_path = "todays_motivation.jpg"
    img = render({"quote": quote, "style": "fierce"})
    img.save(img_path, "JPEG", quality=96)
    file_size_kb = Path(img_path).stat().st_size // 1024
    log(f"Image saved → {img_path} ({file_size_kb} KB)")

    # 3. Upload image ONCE — reuse imageId for all 5 groups
    log("Uploading image to PW...")
    image_id = upload_image(img_path)

    # 4. Send to all groups
    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_message(group, "🌅 Today's Morning Motivation")
        time.sleep(0.5)
        send_image_message(group, image_id, file_size_kb)

    log("✅ Motivation mode complete.")


# ─── MODE: QUIZ (1 PM) ────────────────────────────────────────────────────────

def run_quiz():
    log("=== MODE: QUIZ (1 PM) ===")

    history  = load_json(HISTORY_FILE, {"used": []})
    weekday  = date.today().weekday()
    subjects = list(SUBJECT_MIXES[weekday % len(SUBJECT_MIXES)])
    log(f"Today's subjects: {subjects}")

    log("Generating questions via Groq...")
    questions = []
    attempts  = 0
    while len(questions) < 5 and attempts < 3:
        attempts += 1
        qs = generate_questions(subjects)
        if qs and len(qs) >= 5:
            questions = qs[:5]
            break
        log(f"[WARN] Attempt {attempts}: got {len(qs)} questions, retrying...")
        time.sleep(2)

    if len(questions) < 5:
        msg = f"Quiz failed — only {len(questions)}/5 questions after {attempts} attempts."
        log(f"❌ {msg}")
        send_alert("❌ Lakshya Poll Automation FAILED", msg)
        sys.exit(1)

    log(f"✅ Got {len(questions)} questions.")

    log("Generating intro message...")
    intro = generate_intro_message(subjects)
    log(f"Intro: {intro[:80]}...")

    for group in GROUPS:
        log(f"\n── {group['name']} ──")
        send_message(group, f"📢 {intro}")
        time.sleep(1)
        for i, q in enumerate(questions):
            log(f"  Poll {i+1}/5 [{q.get('subject','')}]")
            send_poll(group, q)
            time.sleep(1)

    save_json(TODAY_Q_FILE, questions)
    log(f"Saved → {TODAY_Q_FILE}")

    for q in questions:
        qhash = str(hash(q["question"][:50]))
        if qhash not in history["used"]:
            history["used"].append(qhash)
    history["used"] = history["used"][-500:]
    save_json(HISTORY_FILE, history)
    log(f"History updated ({len(history['used'])} entries).")
    log("✅ Quiz mode complete.")


# ─── MODE: SOLUTION (10 PM) ───────────────────────────────────────────────────

def run_solution():
    log("=== MODE: SOLUTION (10 PM) ===")

    if not TODAY_Q_FILE.exists():
        msg = "todays_questions.json not found — quiz mode may have failed today."
        log(f"❌ {msg}")
        send_alert("❌ Solution mode failed — no questions file", msg)
        sys.exit(1)

    questions = load_json(TODAY_Q_FILE, [])
    if not questions:
        log("❌ Questions file is empty.")
        sys.exit(1)

    log(f"Loaded {len(questions)} questions.")
    letters = ["A", "B", "C", "D"]

    for group in GROUPS:
        log(f"\n── {group['name']} ──")
        send_message(group, "🎯 Today's Poll Solutions — Check how you did!")
        time.sleep(1)

        for i, q in enumerate(questions):
            subject        = q.get("subject", "")
            opts           = q.get("options", [])
            correct        = q.get("correct", 1)
            soln           = q.get("solution", "No solution available.")
            year_tag       = q.get("year_tag", "")
            correct_letter = letters[correct - 1] if 1 <= correct <= 4 else "?"
            correct_text   = opts[correct - 1] if opts else ""

            sol_msg = (
                f"Q{i+1} Solution [{subject}] {year_tag}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{q['question']}\n\n"
                f"✅ Correct Answer: ({correct_letter}) {correct_text}\n\n"
                f"📝 Explanation:\n{soln}"
            )
            send_message(group, sol_msg)
            time.sleep(1.5)

    log("✅ Solution mode complete.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["quiz", "solution", "motivation"],
        required=True,
    )
    args = parser.parse_args()
    log(f"Starting in mode: {args.mode.upper()}")
    check_token()

    try:
        if args.mode == "motivation":
            run_motivation()
        elif args.mode == "quiz":
            run_quiz()
        elif args.mode == "solution":
            run_solution()
    except Exception as e:
        err = traceback.format_exc()
        log(f"❌ FATAL ERROR:\n{err}")
        send_alert(
            f"❌ Lakshya Automation CRASHED ({args.mode} mode)",
            f"Error: {e}\n\nTraceback:\n{err}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()