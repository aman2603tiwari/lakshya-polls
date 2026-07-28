"""
send_polls.py  —  Lakshya JEE 2027 Automation
===============================================
Modes:
  --mode=quiz        (1 PM)  → motivational intro + 5 PYQ polls → all groups
  --mode=solution    (10 PM) → 5 solution messages → all groups
  --mode=motivation  (8 AM)  → daily quote message + saves image → all groups

GitHub Secrets required:
  PW_TOKEN      — Bearer token from pw.live Network tab (expires ~7 days)
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

BASE_URL  = "https://api.pw.live"
CLIENT_ID = "5eb393ee95fab7468a79d189"
BATCH_ID  = "6779345c20fa0756e4a7fd08"

HEADERS = {
    "Authorization": f"Bearer {PW_TOKEN}",
    "Content-Type":  "application/json",
    "client-id":     CLIENT_ID,
}

# ─── GROUPS ───────────────────────────────────────────────────────────────────

GROUPS = [
    {"name": "Group 1", "groupId": "69cb7c5e4a6bd7893a91aa22", "conversationId": "69ce5c7c8a5087b50b14c482"},
    {"name": "Group 2", "groupId": "69cb7c67e223436a272111c9", "conversationId": "69ce5d26b3e8f731557c9116"},
    {"name": "Group 3", "groupId": "69cb7c6cd6e4a400b77ebccc", "conversationId": "69ce5f7b369fd47f199d51a8"},
    {"name": "Group 4", "groupId": "69cb7c7036b09e3dea135a30", "conversationId": "69ce60754bd6bffed97b9eeb"},
    {"name": "Group 5", "groupId": "69cb7c7426c54583a30f3039", "conversationId": "69ce60a65155c4ac4c289fea"},
]

# ─── SUBJECT ROTATION ─────────────────────────────────────────────────────────
# Each tuple = (Physics count, Chemistry count, Maths count)
# Rotates by day-of-week so every day has a different mix

SUBJECT_MIXES = [
    ("Physics", "Physics", "Chemistry", "Chemistry", "Maths"),    # Mon
    ("Maths",   "Maths",   "Physics",   "Chemistry", "Chemistry"),# Tue
    ("Chemistry","Chemistry","Maths",   "Maths",     "Physics"),  # Wed
    ("Physics", "Maths",   "Chemistry", "Physics",   "Maths"),    # Thu
    ("Maths",   "Physics", "Physics",   "Chemistry", "Maths"),    # Fri
]

# ─── FILE PATHS ───────────────────────────────────────────────────────────────

HISTORY_FILE   = Path("history.json")
TODAY_Q_FILE   = Path("todays_questions.json")
PDF_DIR        = Path("pdfs")

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def send_alert(subject, body):
    """Send Gmail alert on failure."""
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
    """Verify PW token is still valid by hitting the batch info endpoint."""
    try:
        r = requests.get(
            f"{BASE_URL}/v2/batch/{BATCH_ID}",
            headers=HEADERS, timeout=10
        )
        if r.status_code in (200, 201):
            log("✅ Token valid.")
            return True
        elif r.status_code == 401:
            log(f"❌ Token expired/invalid: {r.status_code}")
            return False
        else:
            # Any other status (403, 404 etc) — token format is fine,
            # endpoint may just not exist. Don't block execution.
            log(f"[WARN] Token check got {r.status_code} — continuing anyway.")
            return True
    except Exception as e:
        log(f"[WARN] Token check error: {e} — continuing anyway.")
        return True

def send_message(group, text):
    """Send a plain text message to a group."""
    payload = {
        "groupId":        group["groupId"],
        "conversationId": group["conversationId"],
        "batchId":        BATCH_ID,
        "messageType":    "text",
        "message":        text,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v2/messaging/send-message",
            headers=HEADERS, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Message → {group['name']}")
        else:
            log(f"  ⚠️  Message failed → {group['name']}: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"  ❌ Message error → {group['name']}: {e}")
    time.sleep(1)


def send_poll(group, question):
    """Send a single poll to a group."""
    options = question["options"]   # list of 4 strings
    correct = question["correct"]   # 1-indexed int

    payload = {
        "groupId":        group["groupId"],
        "conversationId": group["conversationId"],
        "batchId":        BATCH_ID,
        "question":       question["question"],
        "options":        [{"text": opt} for opt in options],
        "correctOption":  correct - 1,   # 0-indexed for API
        "duration":       86400,         # poll open for 24 hours
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v2/poll/create-poll",
            headers=HEADERS, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Poll sent → {group['name']}: {question['question'][:50]}...")
        else:
            log(f"  ⚠️  Poll failed → {group['name']}: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"  ❌ Poll error → {group['name']}: {e}")
    time.sleep(1.5)


# ─── GROQ HELPERS ─────────────────────────────────────────────────────────────

groq_client = Groq(api_key=GROQ_API_KEY)


def sample_pyq_text(subject, chars=3000):
    """Sample a random chunk from the subject's PYQ text file."""
    fname = PDF_DIR / f"{subject.lower()}_pyq.txt"
    if not fname.exists():
        return f"[No PYQ file found for {subject} — using general JEE knowledge]"
    text = fname.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= chars:
        return text
    start = random.randint(0, len(text) - chars)
    return text[start: start + chars]


def generate_questions(subjects):
    """
    Ask Groq to generate 5 JEE PYQ questions (one per subject slot).
    Returns list of dicts: {subject, question, options[4], correct(1-4),
                             solution, year_tag}
    """
    subject_list = "\n".join(
        f"Q{i+1}: {subj}" for i, subj in enumerate(subjects)
    )

    pyq_samples = {}
    for subj in set(subjects):
        pyq_samples[subj] = sample_pyq_text(subj)

    context_block = "\n\n".join(
        f"=== {subj} PYQ SAMPLE ===\n{text}"
        for subj, text in pyq_samples.items()
    )

    prompt = f"""You are a JEE question expert. Generate exactly 5 JEE PYQ (previous year questions) based on the sample material below.

Subject assignment for each question:
{subject_list}

PYQ MATERIAL:
{context_block}

STRICT RULES:
- Each question MUST include the year and session tag, e.g. [JEE Main 2022 June S1] or [JEE Adv 2019 P2]
- 4 options per question (A, B, C, D)
- correct is 1-4 (1=A, 2=B, 3=C, 4=D)
- solution must have step-by-step working (3-5 steps)
- No LaTeX backslashes — write math in plain text (e.g. "v^2 = u^2 + 2as" not "\\v^2")
- Questions must be from actual JEE papers, not invented

Return ONLY a JSON array, no markdown, no explanation:
[
  {{
    "subject": "Physics",
    "year_tag": "[JEE Main 2023 Jan S2]",
    "question": "full question text with year tag at start",
    "options": ["option A text", "option B text", "option C text", "option D text"],
    "correct": 2,
    "solution": "Step 1: ...\\nStep 2: ...\\nStep 3: ..."
  }},
  ...5 items total
]"""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content.strip()

    # Handle both array and wrapped object responses
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        # Groq sometimes wraps in {"questions": [...]}
        for key in parsed:
            if isinstance(parsed[key], list):
                return parsed[key]
    except Exception as e:
        log(f"[WARN] Question JSON parse error: {e}")

    return []


def generate_intro_message(subjects):
    """Ask Groq to write a fresh daily motivational intro message."""
    subject_str = ", ".join(subjects)
    prompt = f"""Write a short, energetic motivational message to send to JEE aspirants before their daily quiz.

Today's subjects: {subject_str}
Today's date: {date.today().strftime('%A, %d %B %Y')}

Rules:
- 3-5 lines max
- Mention the subjects naturally
- End with hype to answer the polls ("Drop your answers below!", "Let's see how many you get right!" etc.)
- Sound like a real teacher/mentor, not corporate motivation
- Different every day — be creative
- English only

Return ONLY the message text, no quotes, no explanation."""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=150,
    )
    return resp.choices[0].message.content.strip()


def generate_motivation_quote():
    """Generate a high-quality JEE motivation quote (English only)."""
    system = """You write deeply authentic motivational quotes for JEE/IIT aspirants in ENGLISH ONLY.

Your quotes must feel RAW and REAL — like something a JEE topper or a struggling-but-hungry student actually thinks at 2 AM, NOT a LinkedIn post.

STRICT RULES:
✅ English only
✅ Specific to JEE reality: mock ranks, rank drops, 3 AM studying, Kota pressure, PCM, parents sacrifices
✅ 1-4 lines max. Punchy. No essays.
✅ Emotion first — the student must FEEL seen, not lectured

BANNED: "Never give up", "Believe in yourself", "Work hard", any generic cliché

GREAT EXAMPLES:
- "Your rank dropped 3000. Your parents said nothing. That silence is the heaviest weight you'll carry into that exam room."
- "The integration you couldn't solve at midnight — that's the one on JEE paper. Sit back down."
- "Every topper in that rank list had a night they wanted to quit. You're in that night right now. Stay."
- "The student who scores 99 percentile doesn't work harder than you. They waste less."

Return ONLY a JSON object:
{"quote": "the quote text, use \\n for line breaks between lines"}"""

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

    log("Generating quote via Groq...")
    quote = generate_motivation_quote()
    log(f"Quote: {quote[:80]}...")

    # Format the morning message
    morning_msg = (
        f"🌅 Good Morning, Lakshya JEE 2027!\n\n"
        f'"{quote}"\n\n'
        f"📚 Today's polls drop at 1 PM — be ready!"
    )

    # Send to all groups
    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_message(group, morning_msg)

    # Also generate and save the image (for future PW media upload)
    try:
        from generate_motivation import render
        from PIL import Image
        quote_data = {"quote": quote, "style": "fierce"}
        img = render(quote_data)
        img.save("todays_motivation.jpg", "JPEG", quality=96)
        log("Motivation image saved → todays_motivation.jpg")
    except Exception as e:
        log(f"[WARN] Image generation skipped: {e}")

    log("✅ Motivation mode complete.")


# ─── MODE: QUIZ (1 PM) ────────────────────────────────────────────────────────

def run_quiz():
    log("=== MODE: QUIZ (1 PM) ===")

    # Load history (tracks used question hashes)
    history = load_json(HISTORY_FILE, {"used": []})

    # Pick today's subject mix by weekday (Mon=0 ... Fri=4)
    weekday = date.today().weekday()
    subjects = list(SUBJECT_MIXES[weekday % len(SUBJECT_MIXES)])
    log(f"Today's subjects: {subjects}")

    # Generate questions
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
        msg = f"Quiz mode failed — only got {len(questions)}/5 questions after {attempts} attempts."
        log(f"❌ {msg}")
        send_alert("❌ Lakshya Poll Automation FAILED", msg)
        sys.exit(1)

    log(f"✅ Got {len(questions)} questions.")

    # Generate intro message
    log("Generating intro message...")
    intro = generate_intro_message(subjects)
    log(f"Intro: {intro[:80]}...")

    # Send to all groups
    for group in GROUPS:
        log(f"\n── Sending to {group['name']} ──")

        # 1. Intro message
        send_message(group, f"📢 {intro}")
        time.sleep(1)

        # 2. Five polls
        for i, q in enumerate(questions):
            log(f"  Poll {i+1}/5: [{q.get('subject','')}] {q['question'][:50]}...")
            send_poll(group, q)
            time.sleep(1)

    # Save today's questions for 10 PM solution mode
    save_json(TODAY_Q_FILE, questions)
    log(f"Saved → {TODAY_Q_FILE}")

    # Update history
    for q in questions:
        qhash = str(hash(q["question"][:50]))
        if qhash not in history["used"]:
            history["used"].append(qhash)
    # Keep only last 500 to avoid file bloat
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

    for group in GROUPS:
        log(f"\n── Sending solutions to {group['name']} ──")

        # Opening line
        send_message(group, "🎯 Today's Poll Solutions — Check how you did!")
        time.sleep(1)

        for i, q in enumerate(questions):
            subject = q.get("subject", "")
            opts    = q.get("options", [])
            correct = q.get("correct", 1)   # 1-indexed
            soln    = q.get("solution", "No solution available.")
            year_tag= q.get("year_tag", "")

            # Build correct answer label
            letters = ["A", "B", "C", "D"]
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
        help="Which mode to run"
    )
    args = parser.parse_args()

    log(f"Starting in mode: {args.mode.upper()}")

    # Validate token for all modes
    if not check_token():
        msg = (
            f"PW_TOKEN is invalid or expired!\n\n"
            f"To refresh:\n"
            f"1. Open pw.live → go to any group\n"
            f"2. Create a test poll manually\n"
            f"3. F12 → Network → find POST to v2/poll/create-poll\n"
            f"4. Copy Authorization header value\n"
            f"5. Update PW_TOKEN secret in GitHub repo settings"
        )
        send_alert("⚠️ Lakshya Automation — Token Expired", msg)
        sys.exit(1)

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