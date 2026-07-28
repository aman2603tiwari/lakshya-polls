"""
================================================================
  PW POLL AUTOMATION - LAKSHYA JEE 2027
  Full auto mode — Groq generates fresh questions daily from PYQs
  
  GitHub Secrets needed:
    PW_TOKEN        — Bearer token from pw.live (update weekly)
    ALERT_EMAIL     — your Gmail address
    GMAIL_APP_PWD   — Gmail app password
    GROQ_API_KEY    — from console.groq.com (free)
================================================================
"""

import requests
import time
import os
import json
import smtplib
import uuid
from collections import Counter
from datetime import datetime
from email.mime.text import MIMEText
from groq import Groq

# ── API KEYS (from GitHub Secrets) ───────────────────────────
AUTH_TOKEN    = os.environ["PW_TOKEN"]
ALERT_EMAIL   = os.environ["ALERT_EMAIL"]
GMAIL_APP_PWD = os.environ["GMAIL_APP_PWD"]
GROQ_API_KEY  = os.environ["GROQ_API_KEY"]

# ── GROQ CONFIG ───────────────────────────────────────────────
GROQ_MODEL  = "llama-3.3-70b-versatile"
GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)

# ── PW CONSTANTS (permanent) ─────────────────────────────────
CLIENT_ID = "5eb393ee95fab7468a79d189"
BATCH_ID  = "6779345c20fa0756e4a7fd08"
API_BASE  = "https://api.penpencil.co"
DELAY_SEC = 1.5

# ── 5 GROUPS (permanent) ─────────────────────────────────────
GROUPS = [
    {"name": "Group 1", "groupId": "69cb7c5e4a6bd7893a91aa22", "conversationId": "69ce5c7c8a5087b50b14c482"},
    {"name": "Group 2", "groupId": "69cb7c67e223436a272111c9", "conversationId": "69ce5d26b3e8f731557c9116"},
    {"name": "Group 3", "groupId": "69cb7c6cd6e4a400b77ebccc", "conversationId": "69ce5f7b369fd47f199d51a8"},
    {"name": "Group 4", "groupId": "69cb7c7036b09e3dea135a30", "conversationId": "69ce60754bd6bffed97b9eeb"},
    {"name": "Group 5", "groupId": "69cb7c7426c54583a30f3039", "conversationId": "69ce60a65155c4ac4c289fea"},
]

# ── SUBJECT CONFIG ────────────────────────────────────────────
SUBJECTS = {
    "Physics":   "pdfs/physics_pyq.txt",
    "Chemistry": "pdfs/chemistry_pyq.txt",
    "Maths":     "pdfs/maths_pyq.txt",
}

HISTORY_FILE = "history.json"

# ── HISTORY ───────────────────────────────────────────────────

def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {"used_questions": []}


def save_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ── SUBJECT MIX ───────────────────────────────────────────────

def get_todays_mix() -> list:
    mixes = [
        ["Physics",   "Physics",   "Chemistry", "Chemistry", "Maths"],
        ["Maths",     "Maths",     "Physics",   "Chemistry", "Chemistry"],
        ["Chemistry", "Chemistry", "Maths",     "Maths",     "Physics"],
        ["Physics",   "Maths",     "Chemistry", "Physics",   "Maths"],
        ["Maths",     "Chemistry", "Physics",   "Chemistry", "Physics"],
    ]
    day_index = datetime.now().timetuple().tm_yday % len(mixes)
    return mixes[day_index]


# ── GROQ GENERATION ───────────────────────────────────────────

def load_subject_text(subject: str) -> str:
    path = SUBJECTS[subject]
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run extract_pdfs.py first and commit the .txt files to your repo."
        )
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()[:60000]


def generate_questions_via_groq(subject_mix: list, history: dict) -> list:
    subject_texts = {s: load_subject_text(s) for s in set(subject_mix)}

    used_hint = ""
    if history["used_questions"]:
        recent = history["used_questions"][-30:]
        used_hint = (
            "Do NOT repeat these recently used questions:\n"
            + "\n".join(f"- {q}" for q in recent)
            + "\n\n"
        )

    counts = Counter(subject_mix)
    dist   = ", ".join(f"{v} from {k}" for k, v in counts.items())

    prompt = f"""You are a JEE Main/Advanced question selector for Lakshya JEE 2027.

{used_hint}Select exactly 5 questions distributed as: {dist}.

Rules:
- Pick actual JEE PYQs from the texts below
- Each question must have exactly 4 specific options (not placeholders)
- correct = 1 for A, 2 for B, 3 for C, 4 for D
- Include year tag in question e.g. [JEE Main 2019]
- Moderate difficulty level

--- PHYSICS ---
{subject_texts.get("Physics", "")[:20000]}

--- CHEMISTRY ---
{subject_texts.get("Chemistry", "")[:20000]}

--- MATHS ---
{subject_texts.get("Maths", "")[:20000]}

Return ONLY a valid JSON array of exactly 5 objects. No explanation, no markdown, no backticks.
[
  {{
    "subject": "Physics",
    "question": "Full question text [JEE Main 20XX]",
    "options": ["option A", "option B", "option C", "option D"],
    "correct": 2
  }}
]"""

    print("🤖 Calling Groq llama-3.3-70b-versatile...")
    response = GROQ_CLIENT.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if Groq adds them
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    questions = json.loads(raw)

    if len(questions) != 5:
        raise ValueError(f"Groq returned {len(questions)} questions, need exactly 5")

    for i, q in enumerate(questions):
        assert "question" in q,        f"Q{i+1} missing question"
        assert "options"  in q,        f"Q{i+1} missing options"
        assert "correct"  in q,        f"Q{i+1} missing correct"
        assert len(q["options"]) == 4, f"Q{i+1} must have 4 options"
        assert 1 <= q["correct"] <= 4, f"Q{i+1} correct must be 1-4"

    return questions


# ── EMAIL ─────────────────────────────────────────────────────

def send_alert_email(subject: str, body: str):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = ALERT_EMAIL
        msg["To"]      = ALERT_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(ALERT_EMAIL, GMAIL_APP_PWD)
            smtp.send_message(msg)
        print("📧 Alert email sent!")
    except Exception as e:
        print(f"⚠️ Could not send alert email: {e}")


# ── PW API ────────────────────────────────────────────────────

def get_headers() -> dict:
    return {
        "Content-Type":    "application/json",
        "Authorization":   AUTH_TOKEN,
        "Client-Id":       CLIENT_ID,
        "Client-Type":     "WEB",
        "x-sdk-version":   "0.0.20",
        "randomid":        str(uuid.uuid4()),
        "Origin":          "https://www.pw.live",
        "Referer":         "https://www.pw.live/",
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def check_token_valid():
    res = requests.get(f"{API_BASE}/v1/users/get-user-detail",
                       headers=get_headers(), timeout=10)
    if res.status_code == 401:
        raise RuntimeError("TOKEN_EXPIRED")


def create_poll(group: dict, poll: dict) -> dict:
    url = f"{API_BASE}/v2/poll/create-poll"
    payload = {
        "type":          "SINGLE",
        "entityType":    "mentorship",
        "entityId":      group["groupId"],
        "poll_question": poll["question"],
        "correctOption": [poll["correct"]],
        "pollOptions": [
            {"option_id": i+1, "option_label": opt, "option_description": opt}
            for i, opt in enumerate(poll["options"])
        ],
    }
    res = requests.post(url, json=payload, headers=get_headers(), timeout=10)
    if res.status_code not in (200, 201):
        print(f"    [DEBUG] {res.status_code}: {res.text[:300]}")
        raise RuntimeError(f"create-poll failed ({res.status_code}): {res.text[:200]}")
    try:
        data = res.json()
    except Exception:
        raise RuntimeError(f"create-poll non-JSON: {res.text[:200]}")
    if data.get("data", {}).get("pollId"):
        return data["data"]
    raise RuntimeError(f"no pollId in response: {data}")


def post_poll_to_chat(group: dict, poll: dict, poll_data: dict):
    url = f"{API_BASE}/v1/conversation/{group['conversationId']}/chat"
    poll_options_str = json.dumps({
        "pollId":        poll_data["pollId"],
        "type":          "SINGLE",
        "pollOptions": [
            {"option_id": i+1, "option_label": opt, "option_description": opt}
            for i, opt in enumerate(poll["options"])
        ],
        "correctOption": [poll["correct"]],
    })
    payload = {
        "batchId":     BATCH_ID,
        "groupId":     group["groupId"],
        "role":        "Mentor",
        "text":        poll["question"],
        "type":        "poll",
        "pollOptions": poll_options_str,
    }
    res = requests.post(url, json=payload, headers=get_headers(), timeout=10)
    if res.status_code not in (200, 201):
        print(f"    [DEBUG] {res.status_code}: {res.text[:300]}")
        raise RuntimeError(f"chat failed ({res.status_code}): {res.text[:200]}")


# ── MAIN ──────────────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%A, %d %B %Y")
    print(f"🚀 PW Poll Automation — Lakshya JEE 2027")
    print(f"📅 {today}\n")

    # Token check
    try:
        check_token_valid()
        print("✅ Token is valid\n")
    except RuntimeError as e:
        if "TOKEN_EXPIRED" in str(e):
            msg = (
                "⚠️ TOKEN EXPIRED\n\n"
                "Fix:\n"
                "  1. pw.live → any group → manually create one poll\n"
                "  2. F12 → Network → POST to v2/poll/create-poll → copy Authorization\n"
                "  3. GitHub → Secrets → PW_TOKEN → Update\n"
                "  4. Actions → Run workflow\n"
            )
            print(msg)
            send_alert_email("🔴 Lakshya Polls FAILED — Token Expired", msg)
            exit(1)

    # Load history
    history = load_history()
    print(f"📚 {len(history['used_questions'])} questions used so far\n")

    # Today's subject mix
    subject_mix = get_todays_mix()
    counts = Counter(subject_mix)
    print(f"📐 Today's mix: {dict(counts)}\n")

    # Generate via Groq
    try:
        questions = generate_questions_via_groq(subject_mix, history)
        print(f"\n✅ Groq generated {len(questions)} questions:")
        for i, q in enumerate(questions):
            print(f"   Q{i+1} [{q.get('subject','?')}]: {q['question'][:70]}...")
        print()
    except Exception as e:
        msg = f"❌ Groq failed: {e}"
        print(msg)
        send_alert_email("🔴 Lakshya Polls FAILED — Groq Error", msg)
        exit(1)

    # Send to all groups
    total    = len(questions) * len(GROUPS)
    success  = 0
    fail     = 0
    failures = []

    print(f"📋 {len(questions)} polls × {len(GROUPS)} groups = {total} total\n")

    for group in GROUPS:
        print(f"📂 {group['name']}")
        for i, poll in enumerate(questions):
            try:
                poll_data = create_poll(group, poll)
                post_poll_to_chat(group, poll, poll_data)
                print(f"  ✅ Poll {i+1} → {group['name']}")
                success += 1
            except Exception as e:
                err = f"Poll {i+1} → {group['name']}: {e}"
                print(f"  ❌ {err}")
                failures.append(err)
                fail += 1
            time.sleep(DELAY_SEC)

    # Save history
    for q in questions:
        fp = q["question"][:80]
        if fp not in history["used_questions"]:
            history["used_questions"].append(fp)
    save_history(history)
    print(f"\n📝 History updated — {len(history['used_questions'])} questions tracked")

    print(f"\n🎉 Done! {success} sent, {fail} failed out of {total}.")

    if fail > 0:
        send_alert_email(
            f"⚠️ Lakshya Polls — {fail} failed today",
            f"✅ Sent: {success}/{total}\n❌ Failed: {fail}/{total}\n\n"
            + "\n".join(f"• {f}" for f in failures)
        )
        exit(1)
    else:
        send_alert_email(
            f"✅ Lakshya Polls — All {success} polls sent!",
            f"📅 {today}\n📐 Mix: {dict(counts)}\n\n"
            + "\n".join(f"Q{i+1} [{q.get('subject','?')}]: {q['question'][:80]}"
                        for i, q in enumerate(questions))
        )


if __name__ == "__main__":
    main()