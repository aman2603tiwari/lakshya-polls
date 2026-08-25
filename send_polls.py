"""
send_polls.py — Lakshya JEE 2027 Automation
============================================

Modes:
  --mode=motivation  (8 AM daily)        -> motivation text -> all groups
  --mode=quiz        (1 PM Mon-Fri)      -> intro + 5 generated PYQ-style polls -> all groups
  --mode=checkin     (5 PM daily)        -> daily checkin / Saturday weekly review
  --mode=solution    (10 PM Mon-Fri)     -> 5 solution messages -> all groups
  --mode=college     (3 PM Mon-Wed-Fri)  -> IIT campus photo + caption -> all groups

Important reliability rules:
- A message is reported SUCCESS only when PW returns HTTP 200/201 and the response
  is not explicitly unsuccessful.
- Network timeouts / connection resets are reported UNKNOWN, not FAILED, because
  the server may have accepted the request even if the client did not receive it.
- Quiz/solution/college modes report exact per-group/per-message accounting.
- Quiz succeeds only when all 25 polls are confirmed.
- Solution succeeds only when all 25 solution messages are confirmed.
- College succeeds only when both image and caption are confirmed for all 5 groups.
- Persistent state prevents a successful/unknown write from being blindly duplicated
  on an automatic rerun.
- Dates/times use Asia/Kolkata explicitly, not the GitHub runner's local timezone.

GitHub Secrets:
  PW_TOKEN
  GROQ_API_KEY
  ALERT_EMAIL
  GMAIL_APP_PWD
  GDRIVE_SA_JSON
  GDRIVE_FOLDER_ID
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import smtplib
import sys
import time
import traceback
import uuid
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import requests
from groq import Groq


# =============================================================================
# CONFIG / SECRETS
# =============================================================================

IST = ZoneInfo("Asia/Kolkata")

RAW_PW_TOKEN = os.environ.get("PW_TOKEN", "").strip()
if RAW_PW_TOKEN.lower().startswith("bearer "):
    PW_TOKEN = RAW_PW_TOKEN[7:].strip()
    PW_TOKEN_FORMAT = "Bearer <token> (normalized)"
else:
    PW_TOKEN = RAW_PW_TOKEN
    PW_TOKEN_FORMAT = "raw token"

if not PW_TOKEN:
    raise RuntimeError("PW_TOKEN is empty. Update the PW_TOKEN GitHub Secret.")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is empty. Update the GROQ_API_KEY GitHub Secret.")

ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()
GMAIL_APP_PWD = os.environ.get("GMAIL_APP_PWD", "").strip()

GDRIVE_SA_JSON = os.environ.get("GDRIVE_SA_JSON", "").strip()
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "").strip()

BASE_URL = "https://api.penpencil.co"
CLIENT_ID = "5eb393ee95fab7468a79d189"
BATCH_ID = "6779345c20fa0756e4a7fd08"

GROUPS = [
    {
        "name": "Group 1",
        "groupId": "69cb7c5e4a6bd7893a91aa22",
        "conversationId": "69ce5c7c8a5087b50b14c482",
    },
    {
        "name": "Group 2",
        "groupId": "69cb7c67e223436a272111c9",
        "conversationId": "69ce5d26b3e8f731557c9116",
    },
    {
        "name": "Group 3",
        "groupId": "69cb7c6cd6e4a400b77ebccc",
        "conversationId": "69ce5f7b369fd47f199d51a8",
    },
    {
        "name": "Group 4",
        "groupId": "69cb7c7036b09e3dea135a30",
        "conversationId": "69ce60754bd6bffed97b9eeb",
    },
    {
        "name": "Group 5",
        "groupId": "69cb7c7426c54583a30f3039",
        "conversationId": "69ce60a65155c4ac4c289fea",
    },
]

SUBJECT_MIXES = [
    ("Physics", "Physics", "Chemistry", "Chemistry", "Maths"),
    ("Maths", "Maths", "Physics", "Chemistry", "Chemistry"),
    ("Chemistry", "Chemistry", "Maths", "Maths", "Physics"),
    ("Physics", "Maths", "Chemistry", "Physics", "Maths"),
    ("Maths", "Physics", "Physics", "Chemistry", "Maths"),
]

HISTORY_FILE = Path("history.json")
TODAY_Q_FILE = Path("todays_questions.json")
QUIZ_STATE_FILE = Path("quiz_delivery_state.json")
SENT_PHOTOS_FILE = Path("sent_photos.json")
COLLEGE_STATE_FILE = Path("college_delivery_state.json")
PDF_DIR = Path("pdfs")

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_UNKNOWN = "UNKNOWN"

REQUEST_TIMEOUT = 20
UPLOAD_TIMEOUT = 45
POST_DELAY = 1.0

TOKEN_FINGERPRINT = hashlib.sha256(PW_TOKEN.encode()).hexdigest()[:16]

groq_client = Groq(api_key=GROQ_API_KEY)


# =============================================================================
# CLOCK / LOGGING / JSON
# =============================================================================

def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def today_str() -> str:
    return today_ist().isoformat()


def log(msg: str) -> None:
    print(f"[{now_ist().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[WARN] Could not read {path}: {e}")
        return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def send_alert(subject: str, body: str) -> None:
    if not ALERT_EMAIL or not GMAIL_APP_PWD:
        log("[INFO] Alert email not configured; skipping email.")
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = ALERT_EMAIL
        msg["To"] = ALERT_EMAIL

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(ALERT_EMAIL, GMAIL_APP_PWD)
            smtp.send_message(msg)

        log(f"Alert email sent: {subject}")
    except Exception as e:
        # Email failure must never be confused with PW/Groq delivery failure.
        log(f"[WARN] Alert email failed: {e}")


# =============================================================================
# PW HEADERS / HTTP HELPERS
# =============================================================================

def get_pw_headers(json_request: bool = False) -> Dict[str, str]:
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,hi;q=0.8",
        "authorization": f"Bearer {PW_TOKEN}",
        "client-id": CLIENT_ID,
        "client-type": "WEB",
        "origin": "https://www.pw.live",
        "referer": "https://www.pw.live/",
        "randomid": str(uuid.uuid4()),
        "x-sdk-version": "0.0.28",
    }

    if json_request:
        headers["content-type"] = "application/json"

    return headers


def response_is_explicitly_unsuccessful(response: requests.Response) -> bool:
    """
    Some APIs return HTTP 200 with {"success": false}.
    Treat that as failure instead of trusting the HTTP code alone.
    """
    try:
        data = response.json()
    except Exception:
        return False

    if isinstance(data, dict) and data.get("success") is False:
        return True

    return False


def response_detail(response: requests.Response, limit: int = 500) -> str:
    text = response.text.replace("\n", " ").strip()
    return text[:limit]


def classify_http_response(
    response: requests.Response,
    operation: str,
) -> Tuple[str, str]:
    if response.status_code in (200, 201) and not response_is_explicitly_unsuccessful(response):
        return STATUS_SUCCESS, f"HTTP {response.status_code}"

    if response.status_code in (401, 403):
        return (
            STATUS_FAILED,
            f"HTTP {response.status_code}: PW authentication/authorization rejected "
            f"the request. This does NOT by itself prove token expiry. "
            f"Response: {response_detail(response)}",
        )

    if 400 <= response.status_code < 500:
        return (
            STATUS_FAILED,
            f"HTTP {response.status_code}: client/API rejection during {operation}. "
            f"Response: {response_detail(response)}",
        )

    if response.status_code >= 500:
        # Server returned a definite response. The request was rejected/errored,
        # but for write operations the server may have processed it before failing.
        return (
            STATUS_UNKNOWN,
            f"HTTP {response.status_code}: server-side error during {operation}. "
            f"Because this is a write operation, delivery state is UNKNOWN. "
            f"Response: {response_detail(response)}",
        )

    if response.status_code not in (200, 201):
        return (
            STATUS_UNKNOWN,
            f"Unexpected HTTP {response.status_code} during {operation}. "
            f"Delivery state is UNKNOWN. Response: {response_detail(response)}",
        )

    return STATUS_SUCCESS, f"HTTP {response.status_code}"


def is_write_network_unknown(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            requests.Timeout,
            requests.ConnectionError,
        ),
    )


# =============================================================================
# QUESTION / HISTORY HELPERS
# =============================================================================

def question_fingerprint(question_text: str) -> str:
    return hashlib.sha256(
        question_text.strip().encode("utf-8")
    ).hexdigest()


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).strip()).lower()


def validate_question(q: Any) -> Tuple[bool, str]:
    if not isinstance(q, dict):
        return False, "not an object"

    subject = q.get("subject")
    if subject not in {"Physics", "Chemistry", "Maths"}:
        return False, f"invalid subject={subject!r}"

    question = q.get("question")
    if not isinstance(question, str) or not question.strip():
        return False, "missing question text"

    year_tag = q.get("year_tag")
    if not isinstance(year_tag, str) or not year_tag.strip():
        return False, "missing year_tag"

    # We don't claim that this is a real PYQ; this only validates the expected
    # JEE-style tag shape supplied by the generator.
    if not re.search(r"JEE\s+Main\s+\d{4}", year_tag, re.IGNORECASE):
        return False, f"invalid year_tag={year_tag!r}"

    options = q.get("options")
    if not isinstance(options, list) or len(options) != 4:
        return False, "options must contain exactly 4 items"

    option_strings = [str(x).strip() for x in options]
    if any(not x for x in option_strings):
        return False, "empty option"

    normalized_options = [normalize_text(x) for x in option_strings]
    if len(set(normalized_options)) != 4:
        return False, "duplicate options"

    correct = q.get("correct")
    if isinstance(correct, bool) or not isinstance(correct, int) or not 1 <= correct <= 4:
        return False, f"invalid correct={correct!r}"

    solution = q.get("solution")
    if not isinstance(solution, str) or not solution.strip():
        return False, "missing solution"

    # Reject obvious image-dependent questions.
    image_words = [
        "figure below",
        "figure shown",
        "diagram below",
        "diagram shown",
        "graph below",
        "graph shown",
        "image below",
        "image shown",
        "refer to the figure",
        "refer to the diagram",
        "as shown in the figure",
    ]
    qnorm = normalize_text(question)
    if any(x in qnorm for x in image_words):
        return False, "image/figure-dependent question"

    # Question must not contain a literal backslash because the prompt forbids it
    # and PW plain-text polls should not depend on JSON/LaTeX escaping.
    if "\\" in question or any("\\" in x for x in option_strings):
        return False, "contains backslash/LaTeX escape"

    q["options"] = option_strings
    q["question"] = question.strip()
    q["year_tag"] = year_tag.strip()
    q["solution"] = solution.strip()

    return True, "ok"


def validate_question_set(
    questions: List[Any],
    subjects: Tuple[str, ...],
    previously_used: set[str],
) -> Tuple[bool, List[dict], str]:
    if len(questions) < 5:
        return False, [], f"only {len(questions)} questions returned"

    expected = list(subjects)
    expected_counts = {s: expected.count(s) for s in expected}

    candidates: Dict[str, List[dict]] = {
        "Physics": [],
        "Chemistry": [],
        "Maths": [],
    }

    for q in questions:
        ok, reason = validate_question(q)
        if not ok:
            log(f"[WARN] Rejected generated question: {reason}")
            continue

        fp = question_fingerprint(q["question"])
        if fp in previously_used:
            log("[WARN] Rejected generated question: already used.")
            continue

        candidates[q["subject"]].append(q)

    selected: List[dict] = []
    for subject in expected:
        if not candidates[subject]:
            return (
                False,
                [],
                f"missing valid unused question for required subject {subject}",
            )

        q = candidates[subject].pop(0)
        fp = question_fingerprint(q["question"])

        # Guard against duplicates inside the same generated batch.
        if any(question_fingerprint(x["question"]) == fp for x in selected):
            return False, [], "duplicate question in generated batch"

        selected.append(q)

    actual_counts = {s: sum(q["subject"] == s for q in selected) for s in expected_counts}
    if actual_counts != expected_counts:
        return False, [], f"subject mix mismatch: expected {expected_counts}, got {actual_counts}"

    return True, selected, "ok"


# =============================================================================
# GROQ JSON PARSING / GENERATION
# =============================================================================

def clean_latex(text: str) -> str:
    # Keep this conservative. We no longer run a destructive regex over the
    # entire JSON string because doing so can alter valid JSON escaping.
    return text


def extract_questions_from_groq(raw: str) -> List[dict]:
    raw = raw.strip()

    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("{") or stripped.startswith("["):
                raw = stripped
                break

    try:
        parsed = json.loads(clean_latex(raw))
    except json.JSONDecodeError as e:
        log(f"[WARN] Groq JSON parse failed: {e}; raw={raw[:300]!r}")
        return []

    if isinstance(parsed, list):
        questions = parsed
    elif isinstance(parsed, dict):
        questions = parsed.get("questions", [])

        if not isinstance(questions, list):
            # Backward-compatible extraction.
            questions = []
            for key, value in parsed.items():
                if isinstance(value, dict) and (
                    key.lower().startswith("question")
                    or key.lower().startswith("q")
                    or key[:1].isdigit()
                ):
                    questions.append(value)
    else:
        return []

    return questions if isinstance(questions, list) else []


def sample_pyq_text(subject: str, chars: int = 1500) -> str:
    fname = PDF_DIR / f"{subject.lower()}_pyq.txt"

    if not fname.exists():
        return f"[No local PYQ text file for {subject}. Use general JEE knowledge.]"

    text = fname.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= chars:
        return text

    # Stable seed across Python processes; built-in hash() is deliberately
    # avoided because Python randomizes it between processes.
    seed_material = f"{today_str()}::{subject}".encode()
    seed = int(hashlib.sha256(seed_material).hexdigest()[:16], 16)

    rng = random.Random(seed)
    start = rng.randint(0, len(text) - chars)
    chunk = text[start:start + chars]

    nl = chunk.find("\n")
    return chunk[nl:].strip() if nl > 0 else chunk.strip()


def generate_questions(subjects: Tuple[str, ...]) -> List[dict]:
    subject_list = "\n".join(
        f"Q{i + 1}: {subject}" for i, subject in enumerate(subjects)
    )

    pyq_samples = {
        subject: sample_pyq_text(subject, chars=1200)
        for subject in sorted(set(subjects))
    }

    context_block = "\n\n".join(
        f"=== {subject} PYQ SAMPLE ===\n{text}"
        for subject, text in pyq_samples.items()
    )

    prompt = f"""
You are generating JEE Main PYQ-STYLE practice questions, not claiming that
the generated questions are authentic past-year questions.

Generate exactly 5 questions with this exact subject assignment and order:
{subject_list}

PYQ STYLE MATERIAL:
{context_block}

RULES:
- The subject of each object MUST match its requested Q number.
- Each question must be self-contained and answerable without an image.
- Each question must have exactly four options.
- correct must be an integer from 1 to 4.
- solution must explain the answer clearly in plain text.
- Include a plausible JEE Main year/session tag such as
  "[JEE Main 2023 Jan S2]" for metadata/style only.
- Do not claim that the generated question itself is an authentic PYQ.
- Do not copy any sample verbatim.
- Do not use LaTeX backslashes.
- Use plain text notation such as x^2, sqrt(x), sin(theta).
- Avoid ambiguous questions and duplicate options.
- Do not refer to figures, graphs, diagrams, images, or tables.
- Do not put markdown around the JSON.
- Return ONLY one JSON OBJECT with a "questions" array.

Required JSON shape:
{{
  "questions": [
    {{
      "subject": "Maths",
      "year_tag": "[JEE Main 2023 Jan S2]",
      "question": "Complete question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct": 2,
      "solution": "Step 1: ... Step 2: ... Answer: B"
    }}
  ]
}}
""".strip()

    try:
        log("[INFO] Calling Groq for 5 questions...")
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise JEE question-generation system. "
                        "Return only valid JSON matching the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
            response_format={"type": "json_object"},
            include_reasoning=False,
        )

        raw = (resp.choices[0].message.content or "").strip()
        log(f"[DEBUG] Groq raw (first 150): {raw[:150]}")
        return extract_questions_from_groq(raw)

    except Exception as e:
        log(f"[WARN] Groq question generation failed: {e}")
        return []


def generate_intro_message(subjects: Tuple[str, ...]) -> str:
    subject_str = ", ".join(subjects)

    prompt = f"""
Write a short energetic message before today's JEE quiz.

Today's subjects: {subject_str}
Today's date: {today_ist().strftime('%A, %d %B %Y')}

Rules:
- 1-2 lines maximum.
- Mention today's subjects naturally.
- End with an invitation/hype to answer the polls.
- Sound like a real caring teacher/mentor.
- English or Hinglish.
- No quotation marks.
- Return ONLY message text.
""".strip()

    try:
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=150,
            include_reasoning=False,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content
    except Exception as e:
        log(f"[WARN] Intro generation failed: {e}")

    return (
        f"📚 Today's JEE challenge: {subject_str}. "
        f"Give these polls your best shot! 💪"
    )


def generate_motivation_quote() -> str:
    system = """
You write authentic motivational messages for JEE/IIT aspirants in English.

Rules:
- Specific to JEE preparation.
- 1-4 short lines.
- Make the student feel understood, not lectured.
- Avoid generic cliches.
- Keep it intense but clean.
- Avoid: doubt, quit, fail, die, kill, blood, 3 AM, midnight, alone, hopeless.
- Return ONLY JSON: {"quote": "quote text"}
""".strip()

    categories = [
        "discipline_and_consistency",
        "exam_pressure_and_fear",
        "parents_sacrifice",
        "comeback_after_failure",
        "late_night_study_grind",
        "mock_test_mindset",
        "iit_dream_visualization",
        "competition_mindset",
        "time_management",
        "mental_toughness",
    ]

    ordinal = today_ist().toordinal()
    category = categories[ordinal % len(categories)]

    resp = groq_client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Category: {category}\nSeed: {ordinal}"},
        ],
        temperature=0.85,
        max_tokens=200,
        response_format={"type": "json_object"},
        include_reasoning=False,
    )

    raw = (resp.choices[0].message.content or "").strip()

    try:
        value = json.loads(raw).get("quote", "")
        return str(value).strip()
    except Exception:
        return raw


def generate_daily_checkin_message() -> str:
    prompt = f"""
Write a warm message to JEE aspirants at 5 PM asking:
1. How their day is going.
2. Whether they covered today's study target.

Today is {today_ist().strftime('%A, %d %B %Y')}.

Rules:
- Caring mentor tone.
- Casual and warm.
- 1-2 lines maximum.
- English or Hinglish.
- End with an invitation to reply.
- No quotation marks.
- Return ONLY the message text.
""".strip()

    try:
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=100,
            include_reasoning=False,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content
    except Exception as e:
        log(f"[WARN] Checkin generation failed: {e}")

    return (
        "Hey champ! How’s your day going? "
        "Aaj ka study target complete hua? Reply karke batao! 💪"
    )


def generate_weekly_review_message() -> str:
    prompt = f"""
Write an engaging Saturday message to JEE aspirants asking them to:
1. Rate their week out of 10.
2. Share how their week went.

Week ending: {today_ist().strftime('%d %B %Y')}

Rules:
- Warm reflective tone.
- Make students feel safe to share honestly.
- 2-3 lines maximum.
- English or Hinglish.
- Return ONLY the message text.
""".strip()

    try:
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=180,
            include_reasoning=False,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content
    except Exception as e:
        log(f"[WARN] Weekly review generation failed: {e}")

    return (
        "📊 Weekly review time! Rate your week out of 10 and tell me how "
        "your preparation went — honestly. What went well, and what needs work?"
    )


def generate_college_caption(photo_name: str) -> str:
    prompt = f"""
Write a short punchy caption to send with an IIT campus photo to JEE aspirants.

Photo filename hint: {photo_name}
Date seed: {today_ist().toordinal()}

Rules:
- 1-2 lines maximum.
- Make the student want to be there.
- Casual, real Hinglish/English tone.
- No hashtags.
- No quotation marks.
- Return ONLY caption text.
""".strip()

    try:
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=150,
            include_reasoning=False,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content
    except Exception as e:
        log(f"[WARN] Caption generation failed: {e}")

    return "One day, this could be your campus. Keep working towards it. 🎯"


# =============================================================================
# PW: TEXT MESSAGE
# =============================================================================

def send_message(group: dict, text: str) -> Tuple[str, str]:
    if not isinstance(text, str) or not text.strip():
        return STATUS_FAILED, "empty message"

    payload = {
        "batchId": BATCH_ID,
        "groupId": group["groupId"],
        "role": "Mentor",
        "type": "text",
        "text": text,
        "filePages": 0,
    }

    url = f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat"

    try:
        response = requests.post(
            url,
            headers=get_pw_headers(json_request=True),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        status, detail = classify_http_response(response, "text message")

        if status == STATUS_SUCCESS:
            log(f"  ✅ Text confirmed → {group['name']}")
            time.sleep(POST_DELAY)
        elif status == STATUS_UNKNOWN:
            log(f"  ❓ Text UNKNOWN → {group['name']}: {detail}")
        else:
            log(f"  ❌ Text failed → {group['name']}: {detail}")

        return status, detail

    except requests.Timeout as e:
        detail = f"timeout during text send: {e}; delivery state UNKNOWN"
        log(f"  ❓ Text UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except requests.ConnectionError as e:
        detail = f"connection error during text send: {e}; delivery state UNKNOWN"
        log(f"  ❓ Text UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except requests.RequestException as e:
        detail = f"request error during text send: {e}; delivery state UNKNOWN"
        log(f"  ❓ Text UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except Exception as e:
        detail = f"unexpected text-send error: {e}"
        log(f"  ❌ Text failed → {group['name']}: {detail}")
        return STATUS_FAILED, detail


# =============================================================================
# PW: IMAGE UPLOAD + IMAGE MESSAGE
# =============================================================================

def upload_image(image_path: str) -> Tuple[str, str]:
    path = Path(image_path)

    if not path.exists():
        return "", f"image does not exist: {path}"

    try:
        size_kb = max(1, path.stat().st_size // 1024)
        log(f"Uploading image: {path.name} ({size_kb} KB)")

        with path.open("rb") as f:
            files = {"file": (path.name, f, "image/jpeg")}

            response = requests.post(
                f"{BASE_URL}/v1/files",
                headers=get_pw_headers(json_request=False),
                files=files,
                timeout=UPLOAD_TIMEOUT,
            )

        status, detail = classify_http_response(response, "image upload")

        if status != STATUS_SUCCESS:
            log(f"❌ Image upload {status}: {detail}")
            return "", detail

        try:
            data = response.json()
        except Exception:
            return "", "PW returned success HTTP status but non-JSON upload response"

        if isinstance(data, dict) and data.get("success") is False:
            return "", f"upload response explicitly unsuccessful: {data}"

        data_block = data.get("data", {}) if isinstance(data, dict) else {}

        image_id = (
            data_block.get("_id")
            or data_block.get("imageId")
            or data_block.get("id")
            or data.get("_id")
            or data.get("imageId")
            or data.get("id")
        )

        if not image_id:
            return "", f"upload succeeded HTTP-wise but no image ID was returned: {data}"

        log(f"✅ Image upload confirmed → imageId={image_id}")
        return str(image_id), "upload confirmed"

    except (requests.Timeout, requests.ConnectionError) as e:
        detail = f"image upload network error: {e}; upload state UNKNOWN"
        log(f"❓ {detail}")
        return "", detail

    except requests.RequestException as e:
        detail = f"image upload request error: {e}; upload state UNKNOWN"
        log(f"❓ {detail}")
        return "", detail

    except Exception as e:
        detail = f"image upload failed: {e}"
        log(f"❌ {detail}")
        return "", detail


def send_image_message(
    group: dict,
    image_id: str,
    file_size_kb: int,
) -> Tuple[str, str]:
    if not image_id:
        return STATUS_FAILED, "missing image_id"

    payload = {
        "batchId": BATCH_ID,
        "groupId": group["groupId"],
        "role": "Mentor",
        "type": "image",
        "imageId": image_id,
        "filePages": 0,
        "fileSize": max(1, int(file_size_kb)),
    }

    url = f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat"

    try:
        response = requests.post(
            url,
            headers=get_pw_headers(json_request=True),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        status, detail = classify_http_response(response, "image message")

        if status == STATUS_SUCCESS:
            log(f"  ✅ Image confirmed → {group['name']}")
            time.sleep(POST_DELAY)
        elif status == STATUS_UNKNOWN:
            log(f"  ❓ Image UNKNOWN → {group['name']}: {detail}")
        else:
            log(f"  ❌ Image failed → {group['name']}: {detail}")

        return status, detail

    except (requests.Timeout, requests.ConnectionError) as e:
        detail = f"image message network error: {e}; delivery state UNKNOWN"
        log(f"  ❓ Image UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except requests.RequestException as e:
        detail = f"image message request error: {e}; delivery state UNKNOWN"
        log(f"  ❓ Image UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except Exception as e:
        detail = f"unexpected image-send error: {e}"
        log(f"  ❌ Image failed → {group['name']}: {detail}")
        return STATUS_FAILED, detail


# =============================================================================
# PW: POLL
# =============================================================================

def create_poll(group: dict, question: dict) -> Tuple[str, str, str]:
    """
    Returns:
      (status, poll_id, detail)

    IMPORTANT:
    - UNKNOWN is never automatically retried because create-poll is a write.
      A timeout may mean PW created the poll but the response was lost.
    """
    options = question.get("options", [])
    correct = question.get("correct")

    if (
        not isinstance(options, list)
        or len(options) != 4
        or isinstance(correct, bool)
        or not isinstance(correct, int)
        or not 1 <= correct <= 4
    ):
        return STATUS_FAILED, "", "malformed poll data"

    payload = {
        "type": "SINGLE",
        "entityType": "mentorship",
        "entityId": group["groupId"],
        "poll_question": question["question"],
        "correctOption": [correct],
        "pollOptions": [
            {
                "option_id": i + 1,
                "option_label": opt,
                "option_description": opt,
            }
            for i, opt in enumerate(options)
        ],
    }

    url = f"{BASE_URL}/v2/poll/create-poll"

    try:
        response = requests.post(
            url,
            headers=get_pw_headers(json_request=True),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        status, detail = classify_http_response(response, "poll creation")

        if status != STATUS_SUCCESS:
            log(f"  {'❓' if status == STATUS_UNKNOWN else '❌'} Poll create "
                f"{status.lower()} → {group['name']}: {detail}")
            return status, "", detail

        try:
            data = response.json()
        except Exception:
            return (
                STATUS_UNKNOWN,
                "",
                "poll create returned success HTTP status but invalid JSON; "
                "poll ID cannot be confirmed",
            )

        poll_data = data.get("data", {}) if isinstance(data, dict) else {}
        poll_id = (
            poll_data.get("pollId")
            or poll_data.get("poll_id")
            or poll_data.get("_id")
            or data.get("pollId")
            or data.get("poll_id")
            or data.get("_id")
        )

        if not poll_id:
            return (
                STATUS_UNKNOWN,
                "",
                f"poll creation HTTP-successful but pollId missing: {data}",
            )

        return STATUS_SUCCESS, str(poll_id), "poll created"

    except (requests.Timeout, requests.ConnectionError) as e:
        detail = f"poll creation network error: {e}; poll existence UNKNOWN"
        log(f"  ❓ Poll create UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, "", detail

    except requests.RequestException as e:
        detail = f"poll creation request error: {e}; poll existence UNKNOWN"
        log(f"  ❓ Poll create UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, "", detail

    except Exception as e:
        detail = f"poll creation failed: {e}"
        log(f"  ❌ Poll create FAILED → {group['name']}: {detail}")
        return STATUS_FAILED, "", detail


def post_poll_to_chat(
    group: dict,
    question: dict,
    poll_id: str,
) -> Tuple[str, str]:
    options = question["options"]
    correct = question["correct"]

    poll_options_str = json.dumps(
        {
            "pollId": poll_id,
            "type": "SINGLE",
            "pollOptions": [
                {
                    "option_id": i + 1,
                    "option_label": opt,
                    "option_description": opt,
                }
                for i, opt in enumerate(options)
            ],
            "correctOption": [correct],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    payload = {
        "batchId": BATCH_ID,
        "groupId": group["groupId"],
        "role": "Mentor",
        "text": question["question"],
        "type": "poll",
        "pollOptions": poll_options_str,
    }

    url = f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat"

    try:
        response = requests.post(
            url,
            headers=get_pw_headers(json_request=True),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        status, detail = classify_http_response(response, "poll chat post")

        if status == STATUS_SUCCESS:
            log(f"  ✅ Poll confirmed → {group['name']}: "
                f"{question['question'][:55]}...")
            time.sleep(POST_DELAY)
        elif status == STATUS_UNKNOWN:
            log(f"  ❓ Poll UNKNOWN → {group['name']}: {detail}")
        else:
            log(f"  ❌ Poll failed → {group['name']}: {detail}")

        return status, detail

    except (requests.Timeout, requests.ConnectionError) as e:
        detail = f"poll chat network error: {e}; delivery state UNKNOWN"
        log(f"  ❓ Poll UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except requests.RequestException as e:
        detail = f"poll chat request error: {e}; delivery state UNKNOWN"
        log(f"  ❓ Poll UNKNOWN → {group['name']}: {detail}")
        return STATUS_UNKNOWN, detail

    except Exception as e:
        detail = f"poll chat failed: {e}"
        log(f"  ❌ Poll failed → {group['name']}: {detail}")
        return STATUS_FAILED, detail


def send_poll(group: dict, question: dict) -> Tuple[str, str, str]:
    """
    Returns:
      (SUCCESS/FAILED/UNKNOWN, poll_id, detail)

    A poll is SUCCESS only after BOTH:
      1. create-poll returned a confirmed pollId
      2. the poll chat post returned success
    """
    ok, reason = validate_question(question)
    if not ok:
        return STATUS_FAILED, "", f"question validation failed: {reason}"

    create_status, poll_id, create_detail = create_poll(group, question)

    if create_status != STATUS_SUCCESS:
        return create_status, "", f"create step: {create_detail}"

    chat_status, chat_detail = post_poll_to_chat(group, question, poll_id)

    if chat_status == STATUS_SUCCESS:
        return STATUS_SUCCESS, poll_id, "poll created and chat post confirmed"

    return chat_status, poll_id, f"chat step: {chat_detail}"


# =============================================================================
# GOOGLE DRIVE
# =============================================================================

def require_drive_config() -> None:
    if not GDRIVE_SA_JSON:
        raise RuntimeError("GDRIVE_SA_JSON secret is missing.")
    if not GDRIVE_FOLDER_ID:
        raise RuntimeError("GDRIVE_FOLDER_ID secret is missing.")


def get_questions_drive_filename() -> str:
    return f"lakshya_questions_{today_str()}.json"


def get_drive_service(readonly: bool = True):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_info = json.loads(GDRIVE_SA_JSON)

    scope = (
        "https://www.googleapis.com/auth/drive.readonly"
        if readonly
        else "https://www.googleapis.com/auth/drive"
    )

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=[scope],
    )

    return build(
        "drive",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )


def upload_json_to_drive(data: dict) -> None:
    require_drive_config()

    import io
    from googleapiclient.http import MediaIoBaseUpload

    service = get_drive_service(readonly=False)
    filename = get_questions_drive_filename()

    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    media = MediaIoBaseUpload(
        io.BytesIO(content),
        mimetype="application/json",
        resumable=False,
    )

    results = service.files().list(
        q=(
            f"'{GDRIVE_FOLDER_ID}' in parents "
            f"and name='{filename}' and trashed=false"
        ),
        fields="files(id,name)",
        pageSize=100,
    ).execute()

    existing = results.get("files", [])

    if existing:
        service.files().update(
            fileId=existing[0]["id"],
            media_body=media,
        ).execute()
        log(f"✅ Updated {filename} in Drive")
    else:
        metadata = {
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
        }
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
        ).execute()
        log(f"✅ Created {filename} in Drive")


def download_json_from_drive() -> dict:
    require_drive_config()

    import io
    from googleapiclient.http import MediaIoBaseDownload

    service = get_drive_service(readonly=True)
    filename = get_questions_drive_filename()

    results = service.files().list(
        q=(
            f"'{GDRIVE_FOLDER_ID}' in parents "
            f"and name='{filename}' and trashed=false"
        ),
        fields="files(id,name)",
        pageSize=100,
    ).execute()

    files = results.get("files", [])

    if not files:
        raise FileNotFoundError(
            f"{filename} not found in Drive — did quiz mode run today?"
        )

    request = service.files().get_media(fileId=files[0]["id"])
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    return json.loads(fh.read().decode("utf-8"))


def list_drive_photos(service) -> List[dict]:
    """
    Paginate through every image in the folder.
    """
    photos: List[dict] = []
    page_token = None

    while True:
        response = service.files().list(
            q=(
                f"'{GDRIVE_FOLDER_ID}' in parents "
                f"and mimeType contains 'image/' and trashed=false"
            ),
            fields="nextPageToken,files(id,name,mimeType,size)",
            pageSize=500,
            pageToken=page_token,
        ).execute()

        photos.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return photos


def download_drive_photo(service, file_id: str, dest_path: str) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    path = Path(dest_path)

    with path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()


# =============================================================================
# QUIZ STATE
# =============================================================================

def expected_subjects_for_today() -> Tuple[str, ...]:
    # Monday=0 ... Sunday=6. The existing 5-mix rotation is retained.
    return tuple(SUBJECT_MIXES[today_ist().weekday() % len(SUBJECT_MIXES)])


def load_history() -> dict:
    data = load_json(HISTORY_FILE, {"used": []})

    if not isinstance(data, dict):
        data = {"used": []}

    used = data.get("used", [])
    if not isinstance(used, list):
        used = []

    return {"used": used}


def update_history(questions: List[dict]) -> None:
    history = load_history()

    for q in questions:
        fp = question_fingerprint(q["question"])
        if fp not in history["used"]:
            history["used"].append(fp)

    history["used"] = history["used"][-500:]
    save_json(HISTORY_FILE, history)
    log(f"History updated ({len(history['used'])} entries).")


def new_quiz_state(subjects: Tuple[str, ...], questions: List[dict], intro: str) -> dict:
    return {
        "date": today_str(),
        "subjects": list(subjects),
        "intro": intro,
        "questions": questions,
        "groups": {
            group["name"]: {
                "intro": {
                    "status": None,
                    "detail": "",
                },
                "polls": [
                    {
                        "status": None,
                        "detail": "",
                        "poll_id": "",
                    }
                    for _ in questions
                ],
            }
            for group in GROUPS
        },
    }


def validate_today_question_file(data: Any) -> Tuple[bool, List[dict], str]:
    if not isinstance(data, dict):
        return False, [], "question file is not an object"

    saved_date = data.get("date")
    if saved_date != today_str():
        return (
            False,
            [],
            f"question file date is {saved_date!r}, expected {today_str()!r}",
        )

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != 5:
        return False, [], "question file does not contain exactly 5 questions"

    subjects = tuple(data.get("subjects", []))
    if len(subjects) != 5:
        return False, [], "question file does not contain today's subject assignment"

    valid = []
    for q in questions:
        ok, reason = validate_question(q)
        if not ok:
            return False, [], f"invalid saved question: {reason}"
        valid.append(q)

    if tuple(q["subject"] for q in valid) != subjects:
        return False, [], "saved question order does not match saved subject assignment"

    return True, valid, "ok"


# =============================================================================
# MODE: QUIZ
# =============================================================================

def run_quiz() -> int:
    log("=== MODE: QUIZ (1 PM) ===")

    subjects = expected_subjects_for_today()
    log(f"Today's IST date: {today_str()}")
    log(f"Today's subjects: {list(subjects)}")

    # If today's quiz already exists, reuse it. This prevents a rerun after a
    # partial delivery from silently replacing the questions with new ones.
    existing_questions = load_json(TODAY_Q_FILE, None)

    valid_existing, questions, existing_reason = validate_today_question_file(
        existing_questions
    )

    if valid_existing:
        log("✅ Reusing today's existing question set.")
        intro = str(existing_questions.get("intro", "")).strip()
        if not intro:
            intro = generate_intro_message(subjects)

        saved_subjects = tuple(existing_questions.get("subjects", []))
        if saved_subjects != subjects:
            msg = (
                f"Today's question file has subject mix {saved_subjects}, "
                f"but today's expected mix is {subjects}."
            )
            log(f"❌ {msg}")
            send_alert("❌ Quiz FAILED — subject mix mismatch", msg)
            return 1
    else:
        log(f"No reusable today's question set: {existing_reason}")

        history = load_history()
        used = set(history["used"])

        questions = []
        max_attempts = 8

        for attempt in range(1, max_attempts + 1):
            log(f"Generation attempt {attempt}/{max_attempts}")

            raw_questions = generate_questions(subjects)

            ok, selected, reason = validate_question_set(
                raw_questions,
                subjects,
                used,
            )

            if ok:
                questions = selected
                log("✅ Generated 5 structurally valid, unused questions.")
                break

            log(f"[WARN] Generation attempt rejected: {reason}")
            time.sleep(2)

        if len(questions) != 5:
            msg = (
                f"Quiz generation failed: could not obtain exactly 5 valid "
                f"questions after {max_attempts} attempts."
            )
            log(f"❌ {msg}")
            send_alert("❌ Lakshya Quiz FAILED — generation", msg)
            return 1

        intro = generate_intro_message(subjects)

        questions_data = {
            "date": today_str(),
            "subjects": list(subjects),
            "intro": intro,
            "questions": questions,
        }

        save_json(TODAY_Q_FILE, questions_data)
        log(f"💾 Saved today's 5 questions to {TODAY_Q_FILE}")

        try:
            upload_json_to_drive(questions_data)
        except Exception as e:
            # Drive is backup/storage, not the source of PW delivery truth.
            # Do not pretend the quiz failed if the actual question file exists
            # and PW delivery can proceed.
            log(f"[WARN] Drive upload failed: {e}")
            send_alert(
                "⚠️ Quiz backup failed — PW delivery will continue",
                f"Date: {today_str()}\nDrive upload error: {e}",
            )

    # Persist/reuse delivery state.
    state = load_json(QUIZ_STATE_FILE, None)

    if (
        not isinstance(state, dict)
        or state.get("date") != today_str()
        or state.get("questions") != questions
    ):
        state = new_quiz_state(subjects, questions, intro)
        save_json(QUIZ_STATE_FILE, state)

    # Ensure intro in state is available.
    if not state.get("intro"):
        state["intro"] = intro

    total_polls = len(questions) * len(GROUPS)
    poll_counts = {
        STATUS_SUCCESS: 0,
        STATUS_FAILED: 0,
        STATUS_UNKNOWN: 0,
    }
    intro_counts = {
        STATUS_SUCCESS: 0,
        STATUS_FAILED: 0,
        STATUS_UNKNOWN: 0,
    }

    for group in GROUPS:
        name = group["name"]
        group_state = state["groups"][name]

        log(f"\n── {name} ──")

        # Intro is independent from poll delivery.
        if group_state["intro"]["status"] is None:
            status, detail = send_message(
                group,
                f"📢 {state['intro']}",
            )
            group_state["intro"] = {
                "status": status,
                "detail": detail,
            }
            save_json(QUIZ_STATE_FILE, state)
        else:
            status = group_state["intro"]["status"]
            detail = group_state["intro"].get("detail", "")
            log(f"  ↩ Intro already recorded as {status} — not resending.")

        intro_counts[status] += 1

        for i, question in enumerate(questions):
            poll_state = group_state["polls"][i]

            if poll_state["status"] is not None:
                status = poll_state["status"]
                log(
                    f"  ↩ Poll {i + 1}/5 already recorded as {status} "
                    f"— not resending."
                )
                poll_counts[status] += 1
                continue

            log(f"  Poll {i + 1}/5 [{question['subject']}]")

            status, poll_id, detail = send_poll(group, question)

            poll_state.update(
                {
                    "status": status,
                    "detail": detail,
                    "poll_id": poll_id,
                }
            )

            save_json(QUIZ_STATE_FILE, state)
            poll_counts[status] += 1

    confirmed = poll_counts[STATUS_SUCCESS]
    failed = poll_counts[STATUS_FAILED]
    unknown = poll_counts[STATUS_UNKNOWN]

    log(
        f"Quiz poll results: "
        f"{confirmed}/{total_polls} confirmed, "
        f"{failed} failed, {unknown} unknown"
    )

    # History is updated ONLY when every poll is confirmed.
    if confirmed == total_polls and failed == 0 and unknown == 0:
        update_history(questions)

        # Mark completed in state.
        state["completed"] = True
        state["completed_at_ist"] = now_ist().isoformat()
        save_json(QUIZ_STATE_FILE, state)

        body = (
            f"Quiz delivery CONFIRMED by PW API.\n\n"
            f"Polls: {confirmed}/{total_polls}\n"
            f"Failed: {failed}\n"
            f"Unknown: {unknown}\n"
            f"Intro confirmed: {intro_counts[STATUS_SUCCESS]}/{len(GROUPS)}\n"
            f"Date (IST): {today_str()}\n\n"
            f"Subjects: {list(subjects)}\n\n"
            + "\n".join(
                f"Q{i + 1} [{q['subject']}]: {q['question'][:100]}"
                for i, q in enumerate(questions)
            )
        )

        log("✅ QUIZ COMPLETE — all 25 polls confirmed.")
        send_alert("✅ Polls Sent — 25/25 Confirmed", body)
        return 0

    # IMPORTANT: never update history on partial/failed delivery.
    state["completed"] = False
    save_json(QUIZ_STATE_FILE, state)

    body = (
        f"Quiz delivery NOT complete.\n\n"
        f"Polls confirmed: {confirmed}/{total_polls}\n"
        f"Polls failed: {failed}\n"
        f"Polls unknown: {unknown}\n"
        f"Intro confirmed: {intro_counts[STATUS_SUCCESS]}/{len(GROUPS)}\n"
        f"Date (IST): {today_str()}\n\n"
        f"FAILED means PW explicitly rejected the request.\n"
        f"UNKNOWN means the client could not safely determine whether PW "
        f"accepted the write; do not blindly duplicate those writes.\n"
    )

    log("❌ QUIZ FAILED/PARTIAL — NOT reporting success.")
    send_alert("❌ Polls NOT Fully Sent", body)
    return 1


# =============================================================================
# MODE: SOLUTION
# =============================================================================

def run_solution() -> int:
    log("=== MODE: SOLUTION (10 PM) ===")

    data = load_json(TODAY_Q_FILE, None)

    ok, questions, reason = validate_today_question_file(data)
    if not ok:
        msg = (
            f"Solution mode refused to run: today's question file is invalid. "
            f"Reason: {reason}"
        )
        log(f"❌ {msg}")
        send_alert("❌ Solutions NOT Sent — invalid today's quiz", msg)
        return 1

    subjects = tuple(data["subjects"])

    # If quiz state exists, refuse to send solutions for a quiz that was not
    # fully confirmed. This prevents solutions for a partially delivered quiz.
    quiz_state = load_json(QUIZ_STATE_FILE, None)

    if not isinstance(quiz_state, dict) or quiz_state.get("date") != today_str():
        msg = "No valid today's quiz delivery state exists."
        log(f"❌ {msg}")
        send_alert("❌ Solutions NOT Sent — quiz state missing", msg)
        return 1

    all_poll_success = True
    for group in GROUPS:
        group_state = quiz_state.get("groups", {}).get(group["name"], {})
        for poll in group_state.get("polls", []):
            if poll.get("status") != STATUS_SUCCESS:
                all_poll_success = False

    if not all_poll_success:
        msg = (
            "Today's quiz was not fully confirmed by PW API. "
            "Solutions will NOT be sent because doing so could create a "
            "mismatched quiz/solution state."
        )
        log(f"❌ {msg}")
        send_alert("❌ Solutions NOT Sent — quiz incomplete", msg)
        return 1

    state = load_json(
        Path("solution_delivery_state.json"),
        None,
    )

    if (
        not isinstance(state, dict)
        or state.get("date") != today_str()
        or state.get("questions") != questions
    ):
        state = {
            "date": today_str(),
            "questions": questions,
            "groups": {
                group["name"]: [
                    {
                        "status": None,
                        "detail": "",
                    }
                    for _ in questions
                ]
                for group in GROUPS
            },
        }

    letters = ["A", "B", "C", "D"]

    for group in GROUPS:
        log(f"\n── {group['name']} ──")

        # The intro is also tracked to prevent duplicate writes on rerun.
        intro_state = state["groups"][group["name"]]

        for i, q in enumerate(questions):
            item = intro_state[i]

            if item["status"] is not None:
                log(
                    f"  ↩ Solution {i + 1}/5 already recorded as "
                    f"{item['status']} — not resending."
                )
                continue

            correct = q["correct"]
            correct_letter = letters[correct - 1]
            correct_text = q["options"][correct - 1]

            sol_msg = (
                f"Q{i + 1} Solution [{q['subject']}] {q['year_tag']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{q['question']}\n\n"
                f"✅ Correct Answer: ({correct_letter}) {correct_text}\n\n"
                f"📝 Explanation:\n{q['solution']}"
            )

            status, detail = send_message(group, sol_msg)

            item.update(
                {
                    "status": status,
                    "detail": detail,
                }
            )

            save_json(Path("solution_delivery_state.json"), state)

    success = 0
    failed = 0
    unknown = 0

    for group in GROUPS:
        for item in state["groups"][group["name"]]:
            status = item["status"]
            if status == STATUS_SUCCESS:
                success += 1
            elif status == STATUS_FAILED:
                failed += 1
            elif status == STATUS_UNKNOWN:
                unknown += 1

    total = len(GROUPS) * len(questions)

    log(
        f"Solution results: {success}/{total} confirmed, "
        f"{failed} failed, {unknown} unknown"
    )

    if success == total and failed == 0 and unknown == 0:
        send_alert(
            "✅ Solutions Sent — 25/25 Confirmed",
            f"All {total} solution messages were confirmed by the PW API.\n"
            f"Date (IST): {today_str()}\n"
            f"Subjects: {list(subjects)}",
        )
        return 0

    send_alert(
        "❌ Solutions NOT Fully Sent",
        f"Confirmed: {success}/{total}\n"
        f"Failed: {failed}\n"
        f"Unknown: {unknown}\n"
        f"Date (IST): {today_str()}",
    )
    return 1


# =============================================================================
# MODE: CHECKIN
# =============================================================================

def run_checkin() -> int:
    log("=== MODE: CHECKIN (5 PM) ===")

    is_saturday = today_ist().weekday() == 5

    if is_saturday:
        log("Saturday — generating weekly review...")
        message = generate_weekly_review_message()
        email_subject = "Weekly Review"
    else:
        log("Generating daily checkin...")
        message = generate_daily_checkin_message()
        email_subject = "Daily Checkin"

    if not message.strip():
        msg = "Generated checkin message was empty."
        log(f"❌ {msg}")
        send_alert("❌ Checkin FAILED — empty message", msg)
        return 1

    success = 0
    failed = 0
    unknown = 0

    for group in GROUPS:
        status, _ = send_message(group, message)

        if status == STATUS_SUCCESS:
            success += 1
        elif status == STATUS_FAILED:
            failed += 1
        else:
            unknown += 1

    log(
        f"Checkin results: {success}/{len(GROUPS)} confirmed, "
        f"{failed} failed, {unknown} unknown"
    )

    if success == len(GROUPS) and failed == 0 and unknown == 0:
        send_alert(
            f"✅ {email_subject} — 5/5 Confirmed",
            f"Message confirmed by PW API for all {len(GROUPS)} groups.\n"
            f"Date (IST): {today_str()}",
        )
        return 0

    send_alert(
        f"❌ {email_subject} NOT Fully Sent",
        f"Confirmed: {success}/{len(GROUPS)}\n"
        f"Failed: {failed}\n"
        f"Unknown: {unknown}\n"
        f"Date (IST): {today_str()}",
    )
    return 1


# =============================================================================
# MODE: MOTIVATION
# =============================================================================

def run_motivation() -> int:
    log("=== MODE: MOTIVATION (8 AM) ===")

    blocked_words = [
        "3 AM",
        "3AM",
        "doubt",
        "quit",
        "fail",
        "die",
        "kill",
        "blood",
    ]

    quote = ""

    for attempt in range(1, 4):
        try:
            candidate = generate_motivation_quote().strip()

            if candidate and not any(
                word.lower() in candidate.lower()
                for word in blocked_words
            ):
                quote = candidate
                break

            log(
                f"[WARN] Motivation candidate rejected by local content "
                f"screen on attempt {attempt}/3."
            )
        except Exception as e:
            log(f"[WARN] Motivation generation attempt {attempt}/3 failed: {e}")

        time.sleep(1)

    if not quote:
        quote = (
            "Today is another chance to get closer to your IIT dream. "
            "Stay focused and keep moving. 💪"
        )

    message = (
        "🌅 Good Morning, Lakshya JEE 2027!\n\n"
        f"{quote}\n\n"
        "— Keep going. Your IIT is waiting. 💪"
    )

    success = 0
    failed = 0
    unknown = 0

    for group in GROUPS:
        status, _ = send_message(group, message)

        if status == STATUS_SUCCESS:
            success += 1
        elif status == STATUS_FAILED:
            failed += 1
        else:
            unknown += 1

    log(
        f"Motivation results: {success}/5 confirmed, "
        f"{failed} failed, {unknown} unknown"
    )

    if success == len(GROUPS) and failed == 0 and unknown == 0:
        send_alert(
            "✅ Morning Motivation — 5/5 Confirmed",
            f"Message confirmed by PW API for all 5 groups.\n"
            f"Date (IST): {today_str()}\n\n{quote}",
        )
        return 0

    send_alert(
        "❌ Morning Motivation NOT Fully Sent",
        f"Confirmed: {success}/5\n"
        f"Failed: {failed}\n"
        f"Unknown: {unknown}\n"
        f"Date (IST): {today_str()}\n\n{quote}",
    )
    return 1


# =============================================================================
# MODE: COLLEGE
# =============================================================================

def load_college_state() -> dict:
    data = load_json(COLLEGE_STATE_FILE, {"photos": {}})
    if not isinstance(data, dict):
        data = {"photos": {}}
    if not isinstance(data.get("photos"), dict):
        data["photos"] = {}
    return data


def run_college() -> int:
    log("=== MODE: COLLEGE PHOTO (3 PM) ===")

    require_drive_config()

    service = get_drive_service(readonly=True)
    all_photos = list_drive_photos(service)

    log(f"Found {len(all_photos)} photos in Drive folder.")

    if not all_photos:
        msg = "No image files found in the configured Drive folder."
        log(f"❌ {msg}")
        send_alert("❌ College Photo FAILED — no photos", msg)
        return 1

    state = load_college_state()

    all_ids = {str(photo["id"]) for photo in all_photos}

    # Remove state for deleted Drive photos.
    state["photos"] = {
        str(photo_id): value
        for photo_id, value in state["photos"].items()
        if str(photo_id) in all_ids
    }

    completed_ids = {
        photo_id
        for photo_id, value in state["photos"].items()
        if isinstance(value, dict) and value.get("completed") is True
    }

    unsent = [
        photo
        for photo in all_photos
        if str(photo["id"]) not in completed_ids
    ]

    if not unsent:
        log("All photos have completed delivery. Resetting the photo cycle.")

        # Start a new cycle, but keep old history under completed cycles.
        state["photos"] = {}
        save_json(COLLEGE_STATE_FILE, state)
        unsent = all_photos

        send_alert(
            "📸 College Photos — Cycle Complete",
            f"All {len(all_photos)} photos completed delivery. "
            f"A new cycle is starting today ({today_str()}).",
        )

    photo = random.choice(unsent)
    photo_id = str(photo["id"])
    photo_name = photo["name"]

    log(f"Selected: {photo_name}")

    photo_state = state["photos"].get(photo_id)

    if not isinstance(photo_state, dict):
        photo_state = {
            "photo_id": photo_id,
            "photo_name": photo_name,
            "image_id": "",
            "file_size_kb": 0,
            "caption": "",
            "groups": {
                group["name"]: {
                    "image": {"status": None, "detail": ""},
                    "caption": {"status": None, "detail": ""},
                }
                for group in GROUPS
            },
            "completed": False,
        }
        state["photos"][photo_id] = photo_state

    # Upload only if no confirmed image_id exists.
    if not photo_state.get("image_id"):
        ext = Path(photo_name).suffix.lower() or ".jpg"

        # Restrict content type to a safe common image extension.
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            ext = ".jpg"

        tmp_path = Path(f"college_photo_{photo_id}{ext}")

        try:
            download_drive_photo(service, photo_id, str(tmp_path))
            file_size_kb = max(1, tmp_path.stat().st_size // 1024)
            photo_state["file_size_kb"] = file_size_kb
            save_json(COLLEGE_STATE_FILE, state)

            image_id, detail = upload_image(str(tmp_path))

            if not image_id:
                send_alert(
                    "❌ College Photo FAILED — upload",
                    f"Photo: {photo_name}\n"
                    f"Upload result: {detail}\n"
                    f"Date (IST): {today_str()}",
                )
                return 1

            photo_state["image_id"] = image_id
            save_json(COLLEGE_STATE_FILE, state)

        finally:
            tmp_path.unlink(missing_ok=True)

    image_id = photo_state["image_id"]
    file_size_kb = int(photo_state.get("file_size_kb") or 1)

    # Caption is generated once and persisted so reruns do not produce a
    # different caption for the same photo.
    if not photo_state.get("caption"):
        photo_state["caption"] = generate_college_caption(photo_name)
        if not photo_state["caption"].strip():
            photo_state["caption"] = (
                "One day, this could be your campus. "
                "Keep working towards it. 🎯"
            )
        save_json(COLLEGE_STATE_FILE, state)

    caption = photo_state["caption"]

    for group in GROUPS:
        group_name = group["name"]
        group_state = photo_state["groups"][group_name]

        log(f"\n── {group_name} ──")

        # Image and caption are independently tracked.
        if group_state["image"]["status"] is None:
            status, detail = send_image_message(
                group,
                image_id,
                file_size_kb,
            )
            group_state["image"] = {
                "status": status,
                "detail": detail,
            }
            save_json(COLLEGE_STATE_FILE, state)
        else:
            log(
                f"  ↩ Image already recorded as "
                f"{group_state['image']['status']} — not resending."
            )

        # Never send caption if image is not confirmed. This prevents a caption
        # being delivered without the corresponding photo.
        if group_state["image"]["status"] != STATUS_SUCCESS:
            log(
                f"  ⚠️ Caption not sent to {group_name} because image status is "
                f"{group_state['image']['status']}."
            )
            continue

        if group_state["caption"]["status"] is None:
            status, detail = send_message(group, caption)
            group_state["caption"] = {
                "status": status,
                "detail": detail,
            }
            save_json(COLLEGE_STATE_FILE, state)
        else:
            log(
                f"  ↩ Caption already recorded as "
                f"{group_state['caption']['status']} — not resending."
            )

    # Completion means BOTH writes are confirmed for every group.
    all_complete = True
    for group in GROUPS:
        group_state = photo_state["groups"][group["name"]]

        if (
            group_state["image"]["status"] != STATUS_SUCCESS
            or group_state["caption"]["status"] != STATUS_SUCCESS
        ):
            all_complete = False

    if all_complete:
        photo_state["completed"] = True
        photo_state["completed_at_ist"] = now_ist().isoformat()
        save_json(COLLEGE_STATE_FILE, state)

        send_alert(
            "✅ College Photo — 10/10 Actions Confirmed",
            f"Photo: {photo_name}\n"
            f"5 image messages + 5 captions confirmed by PW API.\n"
            f"Date (IST): {today_str()}\n\n"
            f"Caption: {caption}",
        )

        log("✅ College photo mode complete — all image/caption actions confirmed.")
        return 0

    # Never mark partial delivery as sent.
    confirmed_images = 0
    confirmed_captions = 0
    failed = 0
    unknown = 0

    for group in GROUPS:
        group_state = photo_state["groups"][group["name"]]

        for kind in ("image", "caption"):
            status = group_state[kind]["status"]

            if status == STATUS_SUCCESS:
                if kind == "image":
                    confirmed_images += 1
                else:
                    confirmed_captions += 1
            elif status == STATUS_FAILED:
                failed += 1
            elif status == STATUS_UNKNOWN:
                unknown += 1

    body = (
        f"College photo delivery NOT complete.\n\n"
        f"Photo: {photo_name}\n"
        f"Images confirmed: {confirmed_images}/5\n"
        f"Captions confirmed: {confirmed_captions}/5\n"
        f"Failed actions: {failed}\n"
        f"Unknown actions: {unknown}\n"
        f"Date (IST): {today_str()}\n\n"
        f"The photo is NOT marked completed, so the state is preserved for "
        f"safe investigation/recovery."
    )

    send_alert("❌ College Photo NOT Fully Sent", body)
    log("❌ College photo delivery incomplete.")
    return 1


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["quiz", "solution", "motivation", "checkin", "college"],
        required=True,
    )
    args = parser.parse_args()

    log(f"Starting in mode: {args.mode.upper()}")
    log(
        f"IST time: {now_ist().isoformat()} | "
        f"PW_TOKEN received: yes | "
        f"length={len(PW_TOKEN)} | "
        f"format={PW_TOKEN_FORMAT} | "
        f"fingerprint={TOKEN_FINGERPRINT}"
    )

    try:
        if args.mode == "motivation":
            return run_motivation()

        if args.mode == "quiz":
            return run_quiz()

        if args.mode == "solution":
            return run_solution()

        if args.mode == "checkin":
            return run_checkin()

        if args.mode == "college":
            return run_college()

        raise RuntimeError(f"Unsupported mode: {args.mode}")

    except KeyboardInterrupt:
        log("❌ Interrupted.")
        return 130

    except Exception as e:
        err = traceback.format_exc()
        log(f"❌ FATAL ERROR:\n{err}")

        send_alert(
            f"❌ Lakshya Automation CRASHED ({args.mode} mode)",
            f"Error: {e}\n\nTraceback:\n{err}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())