"""
send_polls.py  —  Lakshya JEE 2027 Automation
===============================================
Modes:
  --mode=motivation  (8 AM daily)        → AI-generated motivation image → all groups
  --mode=quiz        (1 PM Mon-Fri)      → intro + 5 PYQ polls → all groups
  --mode=checkin     (5 PM daily)        → daily checkin / Saturday weekly review
  --mode=solution    (10 PM Mon-Fri)     → 5 solution messages → all groups
  --mode=college     (3 PM Mon-Wed-Fri)  → random IIT campus photo → all groups

GitHub Secrets required:
  PW_TOKEN, GROQ_API_KEY, ALERT_EMAIL, GMAIL_APP_PWD
  GDRIVE_SA_JSON    — full JSON of Google service account key
  GDRIVE_FOLDER_ID  — ID of the Drive folder containing college photos
"""

import os, sys, json, random, time, argparse, smtplib, traceback, re
from datetime import date, datetime
from pathlib import Path
from email.mime.text import MIMEText

import requests
from groq import Groq
from generate_motivation import generate_today

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
    "Authorization": f"Bearer {PW_TOKEN}",
    "client-id":     CLIENT_ID,
    "client-type":   "WEB",
    "origin":        "https://www.pw.live",
    "referer":       "https://www.pw.live/",
    "x-sdk-version": "0.0.28",
    "randomid":      "2f81cbed-4d22-4f57-994e-3f78dbf6e309",
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

HISTORY_FILE     = Path("history.json")
TODAY_Q_FILE     = Path("todays_questions.json")
SENT_PHOTOS_FILE = Path("sent_photos.json")
PDF_DIR          = Path("pdfs")

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


# ─── GROQ JSON PARSING ────────────────────────────────────────────────────────

def clean_latex(text: str) -> str:
    text = re.sub(r'\\([a-zA-Z]+)', r' \1 ', text)
    text = re.sub(r'\\(?!["\\/bfnrtu])', r' ', text)
    return text


def extract_questions_from_groq(raw: str) -> list:
    """
    Robustly extract and VALIDATE a list of question dicts from Groq response.
    Handles: plain array, wrapped object, question1/question2 keys, markdown fences.
    """
    # Strip markdown fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                raw = stripped
                break

    raw = clean_latex(raw).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"[WARN] JSON parse failed: {e} — Raw: {raw[:200]}")
        return []

    questions = []

    if isinstance(parsed, list):
        # Plain array — ideal case
        questions = parsed

    elif isinstance(parsed, dict):
        # Case 1: wrapped array e.g. {"questions": [...]}
        for val in parsed.values():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                questions = val
                break

        # Case 2: Groq used "question1", "question2"... keys
        # e.g. {"question1": {...}, "question2": {...}}
        if not questions:
            numbered = []
            for key, val in parsed.items():
                if isinstance(val, dict) and (
                    key.startswith("question") or
                    key.startswith("q") or
                    key[0].isdigit()
                ):
                    numbered.append(val)
            if numbered:
                log(f"[INFO] Detected numbered question keys — extracted {len(numbered)} items")
                questions = numbered

        # Case 3: fallback — collect any dict values that look like questions
        if not questions:
            for val in parsed.values():
                if isinstance(val, dict) and (
                    "question" in val or "question_text" in val
                ):
                    questions.append(val)
            if questions:
                log(f"[INFO] Extracted {len(questions)} questions from dict values")

    # Validate each question — only keep fully valid ones
    valid = []
    for q in questions:
        if not isinstance(q, dict):
            continue

        # Fix alternate key names
        if "question_text" in q and "question" not in q:
            q["question"] = q.pop("question_text")
        if "answer_options" in q and "options" not in q:
            q["options"] = q.pop("answer_options")
        if "answer" in q and "correct" not in q:
            q["correct"] = q.pop("answer")

        # Validate question text
        if not q.get("question") or not str(q["question"]).strip():
            log(f"[WARN] Rejected: missing question text")
            continue

        # Validate options
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < 4:
            log(f"[WARN] Rejected: bad options → {str(q.get('question',''))[:50]}")
            continue
        # Check options are not empty placeholders
        if any(not str(o).strip() for o in opts[:4]):
            log(f"[WARN] Rejected: empty option → {str(q.get('question',''))[:50]}")
            continue

        # Validate correct
        correct = q.get("correct")
        if not isinstance(correct, int) or not (1 <= correct <= 4):
            log(f"[WARN] Rejected: bad correct={correct} → {str(q.get('question',''))[:50]}")
            continue

        # Validate solution
        if not q.get("solution") or not str(q["solution"]).strip():
            q["solution"] = "Refer to standard JEE solution."

        q["options"] = [str(o) for o in opts[:4]]
        valid.append(q)

    return valid


# ─── PW: SEND TEXT MESSAGE ────────────────────────────────────────────────────

def send_message(group, text) -> bool:
    """Returns True if message sent successfully, False otherwise."""
    if not text or not text.strip():
        return False
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
            time.sleep(1)
            return True
        elif r.status_code == 401:
            log(f"  ❌ Token expired → {group['name']} — update PW_TOKEN in GitHub Secrets!")
            time.sleep(1)
            return False
        else:
            log(f"  ⚠️  Message failed → {group['name']}: {r.status_code} {r.text[:150]}")
            time.sleep(1)
            return False
    except Exception as e:
        log(f"  ❌ Message error → {group['name']}: {e}")
        time.sleep(1)
        return False


# ─── PW: UPLOAD + SEND IMAGE ──────────────────────────────────────────────────

def upload_image(image_path: str) -> str:
    path = Path(image_path)
    log(f"Uploading image: {path.name} ({path.stat().st_size // 1024} KB)")
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "image/jpeg")}
        r = requests.post(
            f"{BASE_URL}/v1/files",
            headers=HEADERS, files=files, timeout=30
        )
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
    raise Exception(f"Upload failed: {r.status_code} {r.text[:300]}")


def send_image_message(group, image_id, file_size_kb):
    """Send an uploaded image to a PW group. Returns True on success."""
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
            time.sleep(1)
            return True
        elif r.status_code == 401:
            log(f"  ❌ Token expired → {group['name']} — update PW_TOKEN in GitHub Secrets!")
            time.sleep(1)
            return False
        else:
            log(f"  ⚠️  Image failed → {group['name']}: {r.status_code} {r.text[:150]}")
            time.sleep(1)
            return False
    except Exception as e:
        log(f"  ❌ Image error → {group['name']}: {e}")
        time.sleep(1)
        return False


# ─── PW: SEND POLL (TWO-STEP) ─────────────────────────────────────────────────

def send_poll(group, question):
    options = question.get("options", [])
    correct = question.get("correct")

    # Safety check
    if not options or len(options) < 4 or not correct or not (1 <= correct <= 4):
        log(f"  ⚠️  Skipping malformed poll: {str(question.get('question',''))[:50]}")
        return
    if not question.get("question", "").strip():
        log(f"  ⚠️  Skipping poll with empty question text")
        return

    # ── STEP 1: Create poll → get pollId ──────────────────────
    create_payload = {
        "type":          "SINGLE",
        "entityType":    "mentorship",
        "entityId":      group["groupId"],
        "poll_question": question["question"],
        "correctOption": [correct],
        "pollOptions": [
            {"option_id": i + 1, "option_label": opt, "option_description": opt}
            for i, opt in enumerate(options)
        ],
    }
    try:
        r1 = requests.post(
            f"{BASE_URL}/v2/poll/create-poll",
            headers=JSON_HEADERS, json=create_payload, timeout=15
        )
        if r1.status_code not in (200, 201):
            log(f"  ⚠️  Poll create failed → {group['name']}: {r1.status_code} {r1.text[:200]}")
            return
        poll_data = r1.json().get("data", {})
        poll_id   = poll_data.get("pollId")
        if not poll_id:
            log(f"  ⚠️  No pollId in response → {group['name']}: {r1.text[:150]}")
            return
    except Exception as e:
        log(f"  ❌ Poll create error → {group['name']}: {e}")
        return

    time.sleep(0.5)

    # ── STEP 2: Post poll into group chat ─────────────────────
    poll_options_str = json.dumps({
        "pollId":        poll_id,
        "type":          "SINGLE",
        "pollOptions": [
            {"option_id": i + 1, "option_label": opt, "option_description": opt}
            for i, opt in enumerate(options)
        ],
        "correctOption": [correct],
    })
    chat_payload = {
        "batchId":     BATCH_ID,
        "groupId":     group["groupId"],
        "role":        "Mentor",
        "text":        question["question"],
        "type":        "poll",
        "pollOptions": poll_options_str,
    }
    try:
        r2 = requests.post(
            f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat",
            headers=JSON_HEADERS, json=chat_payload, timeout=15
        )
        if r2.status_code in (200, 201):
            log(f"  ✅ Poll sent → {group['name']}: {question['question'][:55]}...")
        else:
            log(f"  ⚠️  Poll chat failed → {group['name']}: {r2.status_code} {r2.text[:200]}")
    except Exception as e:
        log(f"  ❌ Poll chat error → {group['name']}: {e}")

    time.sleep(1.5)


# ─── GOOGLE DRIVE HELPERS ─────────────────────────────────────────────────────

def get_questions_drive_filename() -> str:
    """Filename includes today's date — e.g. lakshya_questions_2026-08-07.json"""
    return f"lakshya_questions_{date.today()}.json"

def get_drive_service(readonly=True):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sa_info = json.loads(GDRIVE_SA_JSON)
    scope   = "https://www.googleapis.com/auth/drive.readonly" if readonly else "https://www.googleapis.com/auth/drive"
    creds   = service_account.Credentials.from_service_account_info(
        sa_info, scopes=[scope]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_json_to_drive(data: dict):
    """Save questions JSON to a fixed file in Drive — overwrites each day."""
    import io
    from googleapiclient.http import MediaIoBaseUpload

    service  = get_drive_service(readonly=False)
    content  = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    media    = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json")

    # Check if file already exists in the folder
    filename = get_questions_drive_filename()
    results  = service.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and name='{filename}' and trashed=false",
        fields="files(id, name)"
    ).execute()
    existing = results.get("files", [])

    if existing:
        service.files().update(
            fileId=existing[0]["id"],
            media_body=media
        ).execute()
        log(f"✅ Updated {filename} in Drive")
    else:
        metadata = {"name": filename, "parents": [GDRIVE_FOLDER_ID]}
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id"
        ).execute()
        log(f"✅ Created {filename} in Drive")


def download_json_from_drive() -> dict:
    """Read today's questions JSON from Drive."""
    import io

    service = get_drive_service(readonly=True)
    filename = get_questions_drive_filename()
    results  = service.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and name='{filename}' and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])

    if not files:
        raise FileNotFoundError(f"{filename} not found in Drive — did quiz mode run today?")

    from googleapiclient.http import MediaIoBaseDownload
    request  = service.files().get_media(fileId=files[0]["id"])
    fh       = io.BytesIO()
    dl       = MediaIoBaseDownload(fh, request)
    done     = False
    while not done:
        _, done = dl.next_chunk()
    fh.seek(0)
    return json.loads(fh.read().decode("utf-8"))


def list_drive_photos(service):
    results = service.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and mimeType contains 'image/' and trashed=false",
        fields="files(id, name)",
        pageSize=500
    ).execute()
    return results.get("files", [])


def download_drive_photo(service, file_id, dest_path):
    from googleapiclient.http import MediaIoBaseDownload
    import io
    request = service.files().get_media(fileId=file_id)
    fh      = io.FileIO(dest_path, "wb")
    dl      = MediaIoBaseDownload(fh, request)
    done    = False
    while not done:
        _, done = dl.next_chunk()
    fh.close()


# ─── GROQ CLIENT ──────────────────────────────────────────────────────────────

groq_client = Groq(api_key=GROQ_API_KEY)


def sample_pyq_text(subject, chars=600):
    fname = PDF_DIR / f"{subject.lower()}_pyq.txt"
    if not fname.exists():
        return f"[No PYQ file for {subject} — use general JEE knowledge]"
    text  = fname.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= chars:
        return text
    seed  = date.today().toordinal() * 100 + hash(subject) % 100
    random.seed(seed)
    start = random.randint(0, len(text) - chars)
    chunk = text[start: start + chars]
    nl    = chunk.find("\n")
    return chunk[nl:].strip() if nl > 0 else chunk.strip()


# ─── GROQ: GENERATE QUESTIONS ─────────────────────────────────────────────────

def generate_questions(subjects):
    """Generate exactly 5 JEE PYQ-style questions using strict JSON schema."""
    subject_list = "\n".join(
        f"Q{i+1}: {s}" for i, s in enumerate(subjects)
    )

    pyq_samples = {
        s: sample_pyq_text(s, chars=600)
        for s in set(subjects)
    }

    context_block = "\n\n".join(
        f"=== {s} PYQ SAMPLE ===\n{t}"
        for s, t in pyq_samples.items()
    )

    prompt = f"""You are an expert JEE Main and JEE Advanced question setter.

Generate exactly 5 realistic JEE PYQ-style multiple-choice questions.

SUBJECT ASSIGNMENT:
{subject_list}

PYQ MATERIAL:
{context_block}

Use the PYQ material only for style and difficulty. Do NOT copy it verbatim.

RULES:
- Generate exactly 5 questions.
- Follow the subject assignment exactly.
- Each question must have an exam year/session tag.
- Each question must have exactly 4 meaningful options.
- correct must be an integer from 1 to 4.
- Give a concise 2-4 step solution.
- Questions must be solvable using the standard JEE syllabus.
- Do NOT use images, graphs, diagrams, or questions referring to them.
- Do NOT use LaTeX backslashes.
- Use plain text mathematics: x^2, sqrt(x), alpha, sin(theta).
- Avoid ambiguous or incomplete questions.
- Keep questions and solutions concise.
- Return only the requested structured data.
"""

    try:
        log("[INFO] Calling Groq with strict JSON schema...")

        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise JEE question-generation system. Follow the provided output schema exactly."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=3000,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "jee_quiz_questions",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "questions": {
                                "type": "array",
                                "minItems": 5,
                                "maxItems": 5,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "subject": {"type": "string"},
                                        "year_tag": {"type": "string"},
                                        "question": {"type": "string"},
                                        "options": {
                                            "type": "array",
                                            "minItems": 4,
                                            "maxItems": 4,
                                            "items": {"type": "string"}
                                        },
                                        "correct": {
                                            "type": "integer",
                                            "enum": [1, 2, 3, 4]
                                        },
                                        "solution": {"type": "string"}
                                    },
                                    "required": [
                                        "subject", "year_tag", "question",
                                        "options", "correct", "solution"
                                    ],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["questions"],
                        "additionalProperties": False
                    }
                }
            }
        )

        raw = resp.choices[0].message.content.strip()
        log(f"[DEBUG] Groq raw (first 300): {raw[:300]}")
        questions = extract_questions_from_groq(raw)
        log(f"[INFO] Groq generated {len(questions)} valid question(s)")
        return questions

    except Exception as e:
        log(f"[WARN] Groq structured generation failed: {e}")
        return []


# ─── GROQ: INTRO MESSAGE ──────────────────────────────────────────────────────

def generate_intro_message(subjects):
    subject_str = ", ".join(subjects)
    prompt = f"""Write a short energetic motivational message before a JEE daily quiz.

Today's subjects: {subject_str}
Today's date: {date.today().strftime('%A, %d %B %Y')}

Rules:
- 1-2 lines max
- Mention today's subjects naturally
- End with hype to answer the polls
- Sound like a real caring teacher/mentor
- Use Hinglish or English, fresh and different every day
- Don't use any quotes "" or ''

Return ONLY the message text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=150,
    )
    return resp.choices[0].message.content.strip()


# ─── GROQ: MOTIVATION QUOTE ───────────────────────────────────────────────────

def generate_motivation_quote():
    system = """You write deeply authentic motivational quotes for JEE/IIT aspirants in ENGLISH ONLY.

Raw, real — like something a topper or struggling student actually thinks while studying late.

RULES:
- English only
- Specific to JEE: mock ranks, rank drops, late night studying, Kota pressure, PCM, parents sacrifices
- 1-4 lines max. Punchy.
- Make the student FEEL seen, not lectured
- BANNED: "Never give up", "Believe in yourself", "Work hard", any generic cliche
- AVOID these words entirely: doubt, quit, fail, die, kill, blood, 3 AM, midnight, alone, hopeless
- Keep it intense but clean — PW has a content filter

Return ONLY JSON: {"quote": "quote text"}"""

    categories = [
        "discipline_and_consistency", "exam_pressure_and_fear",
        "parents_sacrifice", "comeback_after_failure",
        "late_night_study_grind", "mock_test_mindset",
        "iit_dream_visualization", "competition_mindset",
        "time_management", "mental_toughness",
    ]
    cat = categories[date.today().toordinal() % len(categories)]
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
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


# ─── GROQ: COLLEGE CAPTION ────────────────────────────────────────────────────

def generate_college_caption(photo_name):
    prompt = f"""Write a short punchy caption to send with an IIT campus photo to JEE aspirants.

Photo filename hint: {photo_name}
Seed for variety: {date.today().toordinal()}

Rules:
- 1-2 lines max
- Make the student WANT to be there
- Casual, real tone in Hinglish
- No hashtags

Return ONLY the caption text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=100,
    )
    return resp.choices[0].message.content.strip()


# ─── GROQ: DAILY CHECKIN ──────────────────────────────────────────────────────

def generate_daily_checkin_message():
    prompt = f"""Write a warm engaging message to JEE aspirants at 5 PM asking:
1. How their day is going
2. Whether they covered today's study target

Today is {date.today().strftime('%A, %d %B %Y')}.
Seed: {date.today().toordinal()}

Rules:
- Sound like a caring mentor
- Casual and warm tone
- 1-2 lines max
- Use English or Hinglish
- End with invitation to reply

Return ONLY the message text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=200,
    )
    return resp.choices[0].message.content.strip()


# ─── GROQ: WEEKLY REVIEW ──────────────────────────────────────────────────────

def generate_weekly_review_message():
    prompt = f"""Write an engaging message to JEE aspirants at 5 PM on Saturday asking them to:
1. Rate their week out of 10
2. Share how their week went

Week ending: {date.today().strftime('%d %B %Y')}
Seed: {date.today().toordinal()}

Rules:
- Warm reflective tone
- Make students feel safe to share honestly
- 2-3 lines max
- Use Hinglish or English

Return ONLY the message text."""
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.92,
        max_tokens=250,
    )
    return resp.choices[0].message.content.strip()


# ─── MODE: MOTIVATION (8 AM daily) ───────────────────────────────────────────

def run_motivation():
    log("=== MODE: MOTIVATION IMAGE (8 AM) ===")

    motivation_path = None

    try:
        log("Generating today's motivation image...")
        motivation_path = generate_today(save=True)

        if not motivation_path:
            raise Exception("Motivation generator returned no image path.")

        motivation_path = Path(motivation_path)

        if not motivation_path.exists():
            raise FileNotFoundError(
                f"Generated motivation image not found: {motivation_path}"
            )

        file_size_kb = motivation_path.stat().st_size // 1024
        log(f"✅ Motivation image generated → {motivation_path} ({file_size_kb} KB)")

        log("Uploading motivation image to PW...")
        image_id = upload_image(str(motivation_path))

        if not image_id:
            raise Exception("PW image upload returned no image ID.")

        log(f"✅ Motivation image uploaded → imageId: {image_id}")

        success = 0
        fail = 0

        for group in GROUPS:
            log(f"Sending motivation image → {group['name']}...")
            ok = send_image_message(group, image_id, file_size_kb)
            if ok:
                success += 1
            else:
                fail += 1

        log(f"Motivation image results: {success}/5 sent, {fail}/5 failed")

        if success == 0:
            msg = (
                "❌ Morning Motivation Image FAILED\n\n"
                "Image was generated but could not be sent to any group.\n"
                f"Image: {motivation_path.name}\n"
                "Most likely cause: PW_TOKEN expired."
            )
            log(msg)
            send_alert("❌ Morning Motivation Image FAILED", msg)
            sys.exit(1)

        elif fail > 0:
            send_alert(
                f"⚠️ Morning Motivation Image — {fail} groups failed",
                f"Sent: {success}/5\nFailed: {fail}/5\nImage: {motivation_path.name}\nDate: {date.today()}"
            )
        else:
            log("✅ Motivation image sent to all 5 groups.")
            send_alert(
                "✅ Morning Motivation Image Sent",
                f"Motivation image sent to all 5 groups.\nImage: {motivation_path.name}\nDate: {date.today()}"
            )

    except Exception as e:
        log(f"❌ Motivation image generation/sending failed: {e}")
        send_alert(
            "❌ Morning Motivation Image FAILED",
            f"Error: {e}\n\nDate: {date.today()}"
        )
        sys.exit(1)

    finally:
        if motivation_path:
            try:
                motivation_path = Path(motivation_path)
                if motivation_path.exists():
                    motivation_path.unlink()
                    log(f"🗑️ Removed temporary motivation image: {motivation_path.name}")
            except Exception as e:
                log(f"[WARN] Could not remove motivation image: {e}")


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
        needed = 5 - len(questions)
        log(f"Attempt {attempts}: need {needed} more question(s)...")

        qs = generate_questions(subjects)

        # Add only valid questions not already collected
        existing_texts = {q["question"] for q in questions}
        for q in qs:
            if len(questions) >= 5:
                break
            if q["question"] not in existing_texts:
                questions.append(q)
                existing_texts.add(q["question"])

        log(f"  Got {len(qs)} valid this attempt — total so far: {len(questions)}/5")

        if len(questions) < 5:
            time.sleep(2)

    # Save whatever we have (even partial) so solution mode can still run
    if questions:
        partial = {
            "date": str(date.today()),
            "questions": questions[:5]
        }
        save_json(TODAY_Q_FILE, partial)
        try:
            upload_json_to_drive(partial)
        except Exception:
            pass
        log(f"💾 Saved {min(len(questions),5)} questions")

    if len(questions) < 3:
        msg = f"Quiz failed — only {len(questions)}/5 valid questions after {attempts} attempts."
        log(f"❌ {msg}")
        send_alert("❌ Lakshya Quiz FAILED", msg)
        sys.exit(1)

    if len(questions) < 5:
        log(f"⚠️  Only {len(questions)}/5 questions — proceeding with what we have")

    questions = questions[:5]

    log(f"✅ Got {len(questions)} questions.")
    for i, q in enumerate(questions):
        log(f"   Q{i+1} [{q.get('subject','')}]: {q.get('question','N/A')[:65]}...")

    intro = generate_intro_message(subjects)
    log(f"Intro: {intro[:80]}...")

    for group in GROUPS:
        log(f"\n── {group['name']} ──")
        send_message(group, f"📢 {intro}")
        time.sleep(1)
        for i, q in enumerate(questions):
            log(f"  Poll {i+1}/5 [{q.get('subject','')}]")
            send_poll(group, q)

    # Save questions locally — daily.yml will upload as artifact
    questions_data = {"date": str(date.today()), "questions": questions}
    save_json(TODAY_Q_FILE, questions_data)
    log(f"💾 Questions saved to {TODAY_Q_FILE}")

    # Update history
    for q in questions:
        qhash = str(hash(q.get("question", "")[:50]))
        if qhash not in history["used"]:
            history["used"].append(qhash)
    history["used"] = history["used"][-500:]
    save_json(HISTORY_FILE, history)
    log(f"History updated ({len(history['used'])} entries).")

    log("✅ Quiz mode complete.")
    send_alert(
        "✅ Polls Sent",
        f"5 polls sent to all 5 groups.\nSubjects: {subjects}\nDate: {date.today()}\n\n"
        + "\n".join(
            f"Q{i+1} [{q.get('subject','')}]: {q.get('question','')[:80]}"
            for i, q in enumerate(questions)
        )
    )


# ─── MODE: SOLUTION (10 PM Mon-Fri) ──────────────────────────────────────────

def run_solution():
    log("=== MODE: SOLUTION (10 PM) ===")

    if not TODAY_Q_FILE.exists():
        msg = "todays_questions.json not found — quiz mode may have failed today."
        log(f"❌ {msg}")
        send_alert("❌ Solution mode failed — no questions file", msg)
        sys.exit(1)

    raw_data = load_json(TODAY_Q_FILE, {})

    # Handle both formats: plain list (old) and {date, questions} (new)
    if isinstance(raw_data, list):
        questions = raw_data
    elif isinstance(raw_data, dict):
        questions = raw_data.get("questions", [])
        saved_date = raw_data.get("date", "unknown")
        log(f"Questions from: {saved_date}")
    else:
        questions = []

    if not questions:
        log("❌ Questions file empty or missing.")
        send_alert("❌ Solution mode failed — no questions file",
                   "todays_questions.json missing. Did quiz mode run today?")
        sys.exit(1)

    log(f"Loaded {len(questions)} questions.")
    letters = ["A", "B", "C", "D"]

    success = 0
    fail    = 0

    for group in GROUPS:
        log(f"\n── {group['name']} ──")
        ok = send_message(group, "🎯 Solutions to today's quiz are here! Check your answers 👇")
        if not ok:
            log(f"  ⚠️ Intro failed for {group['name']} — skipping solutions for this group")
            fail += len(questions)
            continue
        time.sleep(1)

        for i, q in enumerate(questions):
            subject        = q.get("subject", "")
            year_tag       = q.get("year_tag", "")
            opts           = q.get("options", [])
            correct        = q.get("correct", 1)
            soln           = q.get("solution", "No solution available.")
            correct_letter = letters[correct - 1] if 1 <= correct <= 4 else "?"
            correct_text   = opts[correct - 1] if opts else ""

            sol_msg = (
                f"Q{i+1} Solution [{subject}] {year_tag}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{q.get('question','')}\n\n"
                f"✅ Correct Answer: ({correct_letter}) {correct_text}\n\n"
                f"📝 Explanation:\n{soln}"
            )
            ok2 = send_message(group, sol_msg)
            if ok2:
                success += 1
            else:
                fail += 1
            time.sleep(1.5)

    total = len(questions) * len(GROUPS)
    log(f"Solution results: {success}/{total} sent, {fail}/{total} failed")

    if success == 0:
        log("❌ All solutions failed — token likely expired")
        send_alert("❌ Solutions FAILED — Token likely expired",
                   f"0 solutions sent.\nFix: refresh PW_TOKEN in GitHub Secrets.\nDate: {date.today()}")
        sys.exit(1)
    elif fail > 0:
        send_alert(f"⚠️ Solutions — {fail} messages failed",
                   f"Sent: {success}/{total}\nFailed: {fail}/{total}\nDate: {date.today()}")
    else:
        log("✅ Solution mode complete.")
        send_alert("✅ Solutions Sent", f"All {success} solutions sent.\nDate: {date.today()}")


# ─── MODE: CHECKIN (5 PM daily) ───────────────────────────────────────────────

def run_checkin():
    log("=== MODE: CHECKIN (5 PM) ===")
    is_saturday = date.today().weekday() == 5

    if is_saturday:
        log("Saturday — generating weekly review...")
        message       = generate_weekly_review_message()
        header        = "📊 Weekly Review Time!"
        email_subject = "✅ Weekly Review Sent"
    else:
        log("Generating daily checkin...")
        message       = generate_daily_checkin_message()
        header        = ""
        email_subject = "✅ Daily Checkin Sent"

    log(f"Message: {message[:80]}...")

    success = 0
    fail    = 0
    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        ok = send_message(group, message)
        if ok:
            success += 1
        else:
            fail += 1

    log(f"Checkin results: {success}/5 sent, {fail}/5 failed")

    if success == 0:
        log("❌ All groups failed — token likely expired")
        send_alert("❌ Checkin FAILED — Token likely expired",
                   f"0/5 groups received checkin.\nFix: refresh PW_TOKEN in GitHub Secrets.\nDate: {date.today()}")
        sys.exit(1)
    elif fail > 0:
        send_alert(f"⚠️ Checkin — {fail} groups failed",
                   f"Sent: {success}/5\nFailed: {fail}/5\nDate: {date.today()}")
    else:
        log("✅ Checkin mode complete.")
        send_alert(email_subject, f"Checkin sent to all 5 groups.\nDate: {date.today()}")


# ─── MODE: COLLEGE (3 PM Mon-Wed-Fri) ────────────────────────────────────────

def run_college():
    log("=== MODE: COLLEGE PHOTO (3 PM) ===")

    if not GDRIVE_SA_JSON or not GDRIVE_FOLDER_ID:
        raise Exception("GDRIVE_SA_JSON or GDRIVE_FOLDER_ID secret is missing.")

    log("Connecting to Google Drive...")
    service    = get_drive_service()
    all_photos = list_drive_photos(service)
    log(f"Found {len(all_photos)} photos in Drive folder.")

    if not all_photos:
        send_alert("⚠️ College Photo — No photos in Drive", "Add photos to the Drive folder.")
        sys.exit(1)

    sent_data = load_json(SENT_PHOTOS_FILE, {"sent": []})
    sent_ids  = set(sent_data.get("sent", []))
    all_ids   = {p["id"] for p in all_photos}
    unsent    = [p for p in all_photos if p["id"] not in sent_ids]
    log(f"Unsent photos: {len(unsent)} / {len(all_photos)}")

    if not unsent:
        log("All photos sent — resetting cycle.")
        send_alert(
            "📸 College Photos — Cycle complete, restarting",
            f"All {len(all_photos)} photos sent. Starting cycle again."
        )
        sent_data["sent"] = []
        unsent = all_photos

    photo = random.choice(unsent)
    log(f"Selected: {photo['name']}")

    ext      = Path(photo["name"]).suffix or ".jpg"
    tmp_path = f"college_photo{ext}"
    download_drive_photo(service, photo["id"], tmp_path)
    file_size_kb = Path(tmp_path).stat().st_size // 1024
    log(f"Downloaded: {tmp_path} ({file_size_kb} KB)")

    log("Generating caption via Groq...")
    caption = generate_college_caption(photo["name"])
    log(f"Caption: {caption}")

    log("Uploading to PW...")
    image_id = upload_image(tmp_path)

    for group in GROUPS:
        log(f"Sending to {group['name']}...")
        send_image_message(group, image_id, file_size_kb)
        time.sleep(0.5)
        send_message(group, caption)

    sent_data["sent"].append(photo["id"])
    sent_data["sent"] = [i for i in sent_data["sent"] if i in all_ids]
    save_json(SENT_PHOTOS_FILE, sent_data)
    log(f"Marked sent. Remaining: {len(all_photos) - len(sent_data['sent'])}/{len(all_photos)}")

    Path(tmp_path).unlink(missing_ok=True)
    log("✅ College photo mode complete.")
    send_alert(
        "✅ College Photo Sent",
        f"Photo: {photo['name']}\nCaption: {caption}\nDate: {date.today()}"
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
