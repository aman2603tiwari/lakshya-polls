"""
send_polls.py  —  Lakshya JEE 2027 Automation
===============================================
Modes:
  --mode=motivation  (8 AM daily)        → motivation image → all groups
  --mode=quiz        (1 PM Mon-Fri)      → intro + 5 PYQ polls → all groups
  --mode=checkin     (5 PM daily)        → daily checkin / Saturday weekly review
  --mode=solution    (10 PM Mon-Fri)     → 5 solution messages → all groups
  --mode=college     (3 PM Mon-Wed-Fri)  → random IIT campus photo → all groups

GitHub Secrets required:
  PW_TOKEN, GROQ_API_KEY, ALERT_EMAIL, GMAIL_APP_PWD
  GDRIVE_SA_JSON    — full JSON of Google service account key
  GDRIVE_FOLDER_ID  — ID of the Drive folder containing college photos
"""

import os, sys, json, random, time, argparse, smtplib, traceback, tempfile
from datetime import date, datetime
from pathlib import Path
from email.mime.text import MIMEText

import requests
from groq import Groq

# ─── SECRETS ──────────────────────────────────────────────────────────────────

PW_TOKEN         = os.environ["PW_TOKEN"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
ALERT_EMAIL      = os.environ.get("ALERT_EMAIL", "")
GMAIL_APP_PWD    = os.environ.get("GMAIL_APP_PWD", "")
GDRIVE_SA_JSON   = os.environ.get("GDRIVE_SA_JSON", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

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
    ("Physics",   "Physics",   "Chemistry", "Chemistry", "Maths"),
    ("Maths",     "Maths",     "Physics",   "Chemistry", "Chemistry"),
    ("Chemistry", "Chemistry", "Maths",     "Maths",     "Physics"),
    ("Physics",   "Maths",     "Chemistry", "Physics",   "Maths"),
    ("Maths",     "Physics",   "Physics",   "Chemistry", "Maths"),
]

# ─── FILE PATHS ───────────────────────────────────────────────────────────────

HISTORY_FILE      = Path("history.json")
TODAY_Q_FILE      = Path("todays_questions.json")
SENT_PHOTOS_FILE  = Path("sent_photos.json")
PDF_DIR           = Path("pdfs")

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
    path = Path(image_path)
    log(f"Uploading image: {path.name} ({path.stat().st_size // 1024} KB)")
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "image/jpeg")}
        r = requests.post(
            f"{BASE_URL}/v1/files",
            headers=HEADERS,
            files=files,
            timeout=30
        )
    log(f"Upload response: {r.status_code} {r.text[:300]}")
    if r.status_code in (200, 201):
        data     = r.json()
        image_id = (
            data.get("data", {}).get("_id")
            or data.get("data", {}).get("imageId")
            or data.get("_id")
            or data.get("imageId")
        )
        if not image_id:
            raise Exception(f"imageId not found in response: {data}")
        log(f"✅ Image uploaded → imageId: {image_id}")
        return image_id
    else:
        raise Exception(f"Upload failed: {r.status_code} {r.text[:300]}")


def send_message(group, text):
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
    options = question["options"]
    correct = question["correct"]
    payload = {
        "batchId":       BATCH_ID,
        "groupId":       group["groupId"],
        "role":          "Mentor",
        "question":      question["question"],
        "options":       [{"text": opt} for opt in options],
        "correctOption": correct - 1,
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


# ─── GOOGLE DRIVE HELPERS ─────────────────────────────────────────────────────

def get_drive_service():
    """Build and return an authenticated Google Drive service."""
    import json as _json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_info = _json.loads(GDRIVE_SA_JSON)
    creds   = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_drive_photos(service):
    """List all image files in the college photos Drive folder."""
    results = service.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and mimeType contains 'image/' and trashed=false",
        fields="files(id, name)",
        pageSize=500
    ).execute()
    return results.get("files", [])


def download_drive_photo(service, file_id, dest_path):
    """Download a Drive file to dest_path."""
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = service.files().get_media(fileId=file_id)
    fh      = io.FileIO(dest_path, "wb")
    dl      = MediaIoBaseDownload(fh, request)
    done    = False
    while not done:
        _, done = dl.next_chunk()
    fh.close()
    log(f"Downloaded → {dest_path}")


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
- No LaTeX backslashes — plain text math only
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


def generate_college_caption(photo_name):
    """Groq writes a fresh IIT campus photo caption — different every time."""
    prompt = f"""Write a short, punchy caption to send with an IIT campus photo to JEE aspirants.

Photo filename hint: {photo_name}
Seed for variety: {date.today().toordinal()}

RULES:
- 1-3 lines max
- Make the student WANT to be there — create desire and hunger
- Casual, real tone — not corporate
- Can be dreamy, can be challenging, can be darkly motivating
- English only
- No hashtags

GREAT EXAMPLES:
- "This could be your hostel room in 2 years. Or it won't be. Your choice starts today."
- "Every student walking this campus once sat where you're sitting right now. 📚"
- "The people who study here didn't get lucky. They just refused to stop."
- "Imagine walking through this gate with your JEE rank. Now go earn it."
- "This campus doesn't care about your excuses. Neither does the rank list."

Return ONLY the caption text."""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=100,
    )
    return resp.choices[0].message.content.strip()


def generate_daily_checkin_message():
    today_name = date.today().strftime("%A")
    prompt = f"""Write a warm, engaging message to send to JEE aspirants at 5 PM asking:
1. How their day is going
2. Whether they were able to cover their today's study target

Today is {today_name}, {date.today().strftime('%d %B %Y')}.
Seed for variety: {date.today().toordinal()}

RULES:
- Sound like a caring mentor/teacher
- Casual and warm tone
- 4-6 lines max
- English only
- End with invitation to reply

Return ONLY the message text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=200,
    )
    return resp.choices[0].message.content.strip()


def generate_weekly_review_message():
    prompt = f"""Write an engaging message to send to JEE aspirants at 5 PM on Saturday asking them to:
1. Rate their week out of 10
2. Share how their week went

Week ending: {date.today().strftime('%d %B %Y')}
Seed: {date.today().toordinal()}

RULES:
- Warm, reflective tone
- Make students feel safe to share honestly
- 5-7 lines max
- English only
- End with encouragement for next week

Return ONLY the message text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=250,
    )
    return resp.choices[0].message.content.strip()


# ─── MODE: MOTIVATION (8 AM daily) ───────────────────────────────────────────

def run_motivation():
    log("=== MODE: MOTIVATION (8 AM) ===")

    log("Generating quote via Groq...")
    quote = generate_motivation_quote()
    log(f"Quote: {quote[:80]}...")

    log("Rendering motivation image...")
    from generate_motivation import render
    img_path = "todays_motivation.jpg"
    img = render({"quote": quote, "style": "fierce"})
    img.save(img_path, "JPEG", quality=96)
    file_size_kb = Path(img_path).stat().st_size // 1024
    log(f"Image saved → {img_path} ({file_size_kb} KB)")

    log("Uploading image to PW...")
    image_id = upload_image(img_path)

    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_message(group, "🌅 Today's Morning Motivation")
        time.sleep(0.5)
        send_image_message(group, image_id, file_size_kb)

    log("✅ Motivation mode complete.")
    send_alert(
        "✅ Motivation Sent",
        f"Morning motivation image sent to all 5 groups.\nDate: {date.today()}\nQuote: {quote[:100]}..."
    )


# ─── MODE: QUIZ (1 PM Mon-Fri) ───────────────────────────────────────────────

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

    for q in questions:
        qhash = str(hash(q["question"][:50]))
        if qhash not in history["used"]:
            history["used"].append(qhash)
    history["used"] = history["used"][-500:]
    save_json(HISTORY_FILE, history)
    log(f"History updated ({len(history['used'])} entries).")

    log("✅ Quiz mode complete.")
    send_alert(
        "✅ Polls Sent",
        f"5 polls sent to all 5 groups.\nSubjects: {subjects}\nDate: {date.today()}"
    )


# ─── MODE: SOLUTION (10 PM Mon-Fri) ──────────────────────────────────────────

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
    send_alert(
        "✅ Solutions Sent",
        f"5 solutions sent to all 5 groups.\nDate: {date.today()}"
    )


# ─── MODE: CHECKIN (5 PM daily) ───────────────────────────────────────────────

def run_checkin():
    log("=== MODE: CHECKIN (5 PM) ===")

    is_saturday = date.today().weekday() == 5

    if is_saturday:
        log("It's Saturday — generating weekly review message...")
        message       = generate_weekly_review_message()
        header        = "📊 Weekly Review Time!"
        email_subject = "✅ Weekly Review Sent"
        email_body    = f"Saturday weekly review sent to all 5 groups.\nDate: {date.today()}"
    else:
        log("Generating daily checkin message...")
        message       = generate_daily_checkin_message()
        header        = "👋 Evening Check-In"
        email_subject = "✅ Daily Checkin Sent"
        email_body    = f"5 PM checkin sent to all 5 groups.\nDate: {date.today()}"

    log(f"Message: {message[:80]}...")

    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_message(group, header)
        time.sleep(0.5)
        send_message(group, message)

    log("✅ Checkin mode complete.")
    send_alert(email_subject, email_body)


# ─── MODE: COLLEGE (3 PM Mon-Wed-Fri) ────────────────────────────────────────

def run_college():
    log("=== MODE: COLLEGE PHOTO (3 PM) ===")

    if not GDRIVE_SA_JSON or not GDRIVE_FOLDER_ID:
        raise Exception("GDRIVE_SA_JSON or GDRIVE_FOLDER_ID secret is missing.")

    # 1. Connect to Drive
    log("Connecting to Google Drive...")
    service = get_drive_service()

    # 2. List all photos in folder
    all_photos = list_drive_photos(service)
    log(f"Found {len(all_photos)} photos in Drive folder.")

    if not all_photos:
        send_alert(
            "⚠️ College Photo — No photos in Drive",
            "The college_photos Drive folder is empty. Please add photos."
        )
        sys.exit(1)

    # 3. Load sent history
    sent_data  = load_json(SENT_PHOTOS_FILE, {"sent": []})
    sent_ids   = set(sent_data.get("sent", []))

    # 4. Find unsent photos
    all_ids    = {p["id"] for p in all_photos}
    unsent     = [p for p in all_photos if p["id"] not in sent_ids]
    log(f"Unsent photos: {len(unsent)} / {len(all_photos)}")

    # 5. If all sent → alert and reset cycle
    if not unsent:
        send_alert(
            "📸 College Photos — All photos sent! Cycle complete.",
            f"All {len(all_photos)} photos have been sent.\n\n"
            f"Add new photos to the Drive folder to continue, "
            f"or the cycle will restart from the beginning next run.\n\n"
            f"Restarting cycle now..."
        )
        log("All photos sent — resetting cycle and picking from all photos.")
        sent_data["sent"] = []
        sent_ids  = set()
        unsent    = all_photos

    # 6. Pick a random unsent photo
    photo = random.choice(unsent)
    log(f"Selected photo: {photo['name']} (id: {photo['id']})")

    # 7. Download it
    ext      = Path(photo["name"]).suffix or ".jpg"
    tmp_path = f"college_photo{ext}"
    download_drive_photo(service, photo["id"], tmp_path)
    file_size_kb = Path(tmp_path).stat().st_size // 1024
    log(f"Downloaded: {tmp_path} ({file_size_kb} KB)")

    # 8. Generate caption
    log("Generating caption via Groq...")
    caption = generate_college_caption(photo["name"])
    log(f"Caption: {caption}")

    # 9. Upload to PW
    log("Uploading to PW...")
    image_id = upload_image(tmp_path)

    # 10. Send to all groups
    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_image_message(group, image_id, file_size_kb)
        time.sleep(0.5)
        send_message(group, caption)

    # 11. Mark as sent
    sent_data["sent"].append(photo["id"])
    # Remove IDs that no longer exist in Drive (deleted photos)
    sent_data["sent"] = [i for i in sent_data["sent"] if i in all_ids]
    save_json(SENT_PHOTOS_FILE, sent_data)
    log(f"Marked as sent. Total sent: {len(sent_data['sent'])}/{len(all_photos)}")

    # Cleanup
    Path(tmp_path).unlink(missing_ok=True)

    log("✅ College photo mode complete.")
    send_alert(
        "✅ College Photo Sent",
        f"Photo: {photo['name']}\n"
        f"Caption: {caption}\n"
        f"Sent to all 5 groups.\n"
        f"Photos remaining: {len(all_photos) - len(sent_data['sent'])}/{len(all_photos)}\n"
        f"Date: {date.today()}"
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["quiz", "solution", "motivation", "checkin", "college"],
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
        elif args.mode == "checkin":
            run_checkin()
        elif args.mode == "college":
            run_college()
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