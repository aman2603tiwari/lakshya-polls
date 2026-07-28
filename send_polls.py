"""
================================================================
  PW POLL AUTOMATION - LAKSHYA JEE 2027
  
  Runs twice daily via two GitHub Actions workflows:
    1. send_polls.py --mode=quiz     → 1 PM IST  (polls + intro message)
    2. send_polls.py --mode=solution → 10 PM IST (solutions as chat messages)

  GitHub Secrets needed:
    PW_TOKEN, ALERT_EMAIL, GMAIL_APP_PWD, GROQ_API_KEY
================================================================
"""

import requests
import time
import os
import json
import smtplib
import uuid
import sys
import re
from collections import Counter
from datetime import datetime
from email.mime.text import MIMEText
from groq import Groq

# ── SECRETS ───────────────────────────────────────────────────
AUTH_TOKEN    = os.environ["PW_TOKEN"]
ALERT_EMAIL   = os.environ["ALERT_EMAIL"]
GMAIL_APP_PWD = os.environ["GMAIL_APP_PWD"]
GROQ_API_KEY  = os.environ["GROQ_API_KEY"]

# ── GROQ ──────────────────────────────────────────────────────
GROQ_MODEL  = "llama-3.3-70b-versatile"
GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)

# ── PW CONSTANTS ──────────────────────────────────────────────
CLIENT_ID = "5eb393ee95fab7468a79d189"
BATCH_ID  = "6779345c20fa0756e4a7fd08"
API_BASE  = "https://api.penpencil.co"
DELAY_SEC = 1.5

# ── GROUPS ────────────────────────────────────────────────────
GROUPS = [
    {"name": "Group 1", "groupId": "69cb7c5e4a6bd7893a91aa22", "conversationId": "69ce5c7c8a5087b50b14c482"},
    {"name": "Group 2", "groupId": "69cb7c67e223436a272111c9", "conversationId": "69ce5d26b3e8f731557c9116"},
    {"name": "Group 3", "groupId": "69cb7c6cd6e4a400b77ebccc", "conversationId": "69ce5f7b369fd47f199d51a8"},
    {"name": "Group 4", "groupId": "69cb7c7036b09e3dea135a30", "conversationId": "69ce60754bd6bffed97b9eeb"},
    {"name": "Group 5", "groupId": "69cb7c7426c54583a30f3039", "conversationId": "69ce60a65155c4ac4c289fea"},
]

SUBJECTS = {
    "Physics":   "pdfs/physics_pyq.txt",
    "Chemistry": "pdfs/chemistry_pyq.txt",
    "Maths":     "pdfs/maths_pyq.txt",
}

HISTORY_FILE  = "history.json"
QUESTIONS_FILE = "todays_questions.json"  # quiz mode saves this, solution mode reads it

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


# ── SMART TEXT SAMPLING ───────────────────────────────────────

def sample_subject_text(subject: str, max_chars: int = 3000) -> str:
    path = SUBJECTS[subject]
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found. Run extract_pdfs.py and commit the .txt files.")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        full_text = f.read()
    total = len(full_text)
    if total <= max_chars:
        return full_text
    import random
    seed = datetime.now().timetuple().tm_yday * 100 + hash(subject) % 100
    random.seed(seed)
    start = random.randint(0, total - max_chars)
    chunk = full_text[start: start + max_chars]
    first_newline = chunk.find("\n")
    if first_newline > 0:
        chunk = chunk[first_newline:]
    return chunk.strip()


# ── CLEAN RAW JSON FROM GROQ ──────────────────────────────────

def clean_groq_json(raw: str) -> str:
    """Strip markdown fences and fix LaTeX backslash escapes."""
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # Replace LaTeX commands like \alpha \frac \theta with plain text
    raw = re.sub(r'\\([a-zA-Z]+)', r' \1 ', raw)
    # Remove any remaining invalid lone backslashes
    raw = re.sub(r'\\(?!["\\/bfnrtu])', r' ', raw)
    return raw.strip()


# ── GROQ: GENERATE QUESTIONS ──────────────────────────────────

def generate_questions_via_groq(subject_mix: list, history: dict) -> list:
    counts = Counter(subject_mix)

    subject_blocks = ""
    for subj in set(subject_mix):
        text = sample_subject_text(subj, max_chars=3000)
        subject_blocks += f"\n--- {subj.upper()} PYQ EXCERPT ---\n{text}\n"

    used_hint = ""
    if history["used_questions"]:
        recent = history["used_questions"][-15:]
        used_hint = "Avoid repeating:\n" + "\n".join(f"- {q}" for q in recent) + "\n\n"

    dist = ", ".join(f"{v} {k}" for k, v in counts.items())

    prompt = f"""You are a JEE question selector for Lakshya JEE 2027. Pick 5 JEE PYQ questions: {dist}.

{used_hint}Rules:
- Pick actual JEE PYQs from the excerpts below
- Each question MUST include the exam year AND session in brackets e.g. [JEE Main 2022 June S1], [JEE Adv 2019 P2], [JEE Main 2020 Sep S2]
- 4 specific answer options each (A, B, C, D) — no vague placeholders
- correct: 1=A, 2=B, 3=C, 4=D
- Also include a short step-by-step solution in a "solution" field (2-4 lines max)
- CRITICAL: Do NOT use LaTeX backslashes like \alpha \frac \theta \sqrt — plain text only
- Write math as plain text: "alpha" not "\alpha", "x^2" not "\frac", "sqrt(x)" not "\sqrt{{x}}"
- Backslashes break JSON — plain text only

{subject_blocks}

Reply ONLY with a valid JSON array of exactly 5 objects, no markdown, no backticks, no backslashes:
[
  {{
    "subject": "Physics",
    "question": "Full question text [JEE Main 2021 Feb S1]",
    "options": ["option A", "option B", "option C", "option D"],
    "correct": 2,
    "solution": "Step 1: ... Step 2: ... Answer: B"
  }}
]"""

    print("🤖 Calling Groq for questions...")
    response = GROQ_CLIENT.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000,
    )

    raw = clean_groq_json(response.choices[0].message.content)

    try:
        questions = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e}\nRaw:\n{raw[:500]}")

    if len(questions) != 5:
        raise ValueError(f"Groq returned {len(questions)} questions, need 5")

    for i, q in enumerate(questions):
        assert "question" in q and q["question"].strip(), f"Q{i+1} missing question"
        assert "options"  in q and len(q["options"]) == 4, f"Q{i+1} needs 4 options"
        assert "correct"  in q and 1 <= q["correct"] <= 4, f"Q{i+1} correct must be 1-4"
        assert "solution" in q and q["solution"].strip(),   f"Q{i+1} missing solution"

    return questions


# ── GROQ: GENERATE DAILY INTRO MESSAGE ───────────────────────

def generate_intro_message(subject_mix: list) -> str:
    counts  = Counter(subject_mix)
    mix_str = ", ".join(f"{v} {k}" for k, v in counts.items())
    today   = datetime.now().strftime("%A")

    prompt = f"""Write a short, energetic motivational message for JEE 2027 aspirants.
It's {today}. Today's quiz has {mix_str} questions.
Rules:
- Max 4 lines
- Encouraging and hype them up to attempt the quiz
- Mention it's a PYQ-based quiz
- End with something like "Drop your answers below!" or "Let's go!"
- No emojis in excess — 1-2 max
- Vary the style each time so it doesn't feel repetitive
Just write the message text directly, nothing else."""

    print("🤖 Calling Groq for intro message...")
    response = GROQ_CLIENT.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()


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
        print(f"⚠️ Email failed: {e}")


# ── PW HEADERS ────────────────────────────────────────────────

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


# ── PW: SEND CHAT MESSAGE ─────────────────────────────────────

def send_chat_message(group: dict, text: str):
    """Send a plain text message to a group chat."""
    url = f"{API_BASE}/v1/conversation/{group['conversationId']}/chat"
    payload = {
        "batchId": BATCH_ID,
        "groupId": group["groupId"],
        "role":    "Mentor",
        "text":    text,
        "type":    "text",
    }
    res = requests.post(url, json=payload, headers=get_headers(), timeout=10)
    if res.status_code not in (200, 201):
        raise RuntimeError(f"chat message failed ({res.status_code}): {res.text[:200]}")


# ── PW: CREATE + POST POLL ────────────────────────────────────

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
        raise RuntimeError(f"non-JSON response: {res.text[:200]}")
    if data.get("data", {}).get("pollId"):
        return data["data"]
    raise RuntimeError(f"no pollId: {data}")


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
        raise RuntimeError(f"chat post failed ({res.status_code}): {res.text[:200]}")


# ── MODE: QUIZ (1 PM) ─────────────────────────────────────────

def run_quiz_mode():
    today      = datetime.now().strftime("%A, %d %B %Y")
    history    = load_history()
    subject_mix = get_todays_mix()
    counts     = Counter(subject_mix)

    print(f"📚 {len(history['used_questions'])} questions used so far")
    print(f"📐 Today's mix: {dict(counts)}\n")

    # Generate questions
    try:
        questions = generate_questions_via_groq(subject_mix, history)
        print(f"✅ {len(questions)} questions generated:")
        for i, q in enumerate(questions):
            print(f"   Q{i+1} [{q.get('subject','?')}]: {q['question'][:65]}...")
        print()
    except Exception as e:
        msg = f"Groq question generation failed: {e}"
        print(f"❌ {msg}")
        send_alert_email("🔴 Lakshya Polls — Groq Error", msg)
        exit(1)

    # Save today's questions for solution mode at 10 PM
    with open(QUESTIONS_FILE, "w") as f:
        json.dump(questions, f, indent=2)
    print(f"💾 Saved questions to {QUESTIONS_FILE}\n")

    # Generate intro message
    try:
        intro = generate_intro_message(subject_mix)
        print(f"💬 Intro message:\n{intro}\n")
    except Exception as e:
        intro = "Today's PYQ Quiz is live! 5 questions across Physics, Chemistry & Maths. Attempt all and drop your answers below! 🔥"
        print(f"⚠️ Groq intro failed, using fallback: {e}")

    # Send to all groups
    total   = len(questions) * len(GROUPS)
    success = 0
    fail    = 0
    failures = []

    print(f"📋 Sending: 1 intro + {len(questions)} polls × {len(GROUPS)} groups\n")

    for group in GROUPS:
        print(f"📂 {group['name']}")

        # Send intro message first
        try:
            send_chat_message(group, intro)
            print(f"  💬 Intro message sent")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️ Intro message failed: {e}")

        # Send 5 polls
        for i, poll in enumerate(questions):
            try:
                poll_data = create_poll(group, poll)
                post_poll_to_chat(group, poll, poll_data)
                print(f"  ✅ Poll {i+1} [{poll.get('subject','?')}] → sent")
                success += 1
            except Exception as e:
                err = f"Poll {i+1} → {group['name']}: {e}"
                print(f"  ❌ {err}")
                failures.append(err)
                fail += 1
            time.sleep(DELAY_SEC)

    # Update history
    for q in questions:
        fp = q["question"][:80]
        if fp not in history["used_questions"]:
            history["used_questions"].append(fp)
    save_history(history)
    print(f"\n📝 History updated — {len(history['used_questions'])} total")
    print(f"🎉 Quiz mode done! {success} sent, {fail} failed out of {total}.")

    if fail > 0:
        send_alert_email(
            f"⚠️ Lakshya Polls — {fail} failed",
            f"Sent: {success}/{total}\nFailed: {fail}/{total}\n\n" + "\n".join(f"• {f}" for f in failures)
        )
        exit(1)
    else:
        send_alert_email(
            f"✅ Lakshya Polls — All {success} sent!",
            f"{today}\nMix: {dict(counts)}\n\n"
            + "\n".join(f"Q{i+1} [{q.get('subject','?')}]: {q['question'][:80]}" for i, q in enumerate(questions))
        )


# ── MODE: SOLUTION (10 PM) ────────────────────────────────────

def run_solution_mode():
    today = datetime.now().strftime("%A, %d %B %Y")

    # Load today's questions
    if not os.path.exists(QUESTIONS_FILE):
        msg = f"todays_questions.json not found — quiz mode may not have run today."
        print(f"❌ {msg}")
        send_alert_email("🔴 Lakshya Solutions — Missing questions file", msg)
        exit(1)

    with open(QUESTIONS_FILE, "r") as f:
        questions = json.load(f)

    print(f"📖 Loaded {len(questions)} questions from {QUESTIONS_FILE}\n")

    option_labels = ["A", "B", "C", "D"]
    success = 0
    fail    = 0
    failures = []

    total = len(questions) * len(GROUPS)
    print(f"📋 Sending {len(questions)} solutions × {len(GROUPS)} groups = {total} messages\n")

    for group in GROUPS:
        print(f"📂 {group['name']}")

        # Send opening line
        try:
            send_chat_message(group, "✅ Solutions to today's quiz are here! Check your answers 👇")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️ Opening message failed: {e}")

        # Send one message per question
        for i, q in enumerate(questions):
            correct_label = option_labels[q["correct"] - 1]
            correct_text  = q["options"][q["correct"] - 1]

            msg_text = (
                f"Q{i+1} Solution [{q.get('subject', '?')}]\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{q['question']}\n\n"
                f"✅ Correct Answer: ({correct_label}) {correct_text}\n\n"
                f"📝 Explanation:\n{q.get('solution', 'See standard solution.')}"
            )

            try:
                send_chat_message(group, msg_text)
                print(f"  ✅ Solution {i+1} sent")
                success += 1
            except Exception as e:
                err = f"Solution {i+1} → {group['name']}: {e}"
                print(f"  ❌ {err}")
                failures.append(err)
                fail += 1
            time.sleep(DELAY_SEC)

    print(f"\n🎉 Solution mode done! {success} sent, {fail} failed out of {total}.")

    if fail > 0:
        send_alert_email(
            f"⚠️ Lakshya Solutions — {fail} failed",
            f"Sent: {success}/{total}\nFailed: {fail}/{total}\n\n" + "\n".join(f"• {f}" for f in failures)
        )
        exit(1)
    else:
        send_alert_email(
            f"✅ Lakshya Solutions — All {success} sent!",
            f"{today}\n\nSolutions delivered to all 5 groups successfully."
        )


# ── MAIN ──────────────────────────────────────────────────────

def main():
    mode = "quiz"
    if len(sys.argv) > 1 and sys.argv[1] == "--mode=solution":
        mode = "solution"

    today = datetime.now().strftime("%A, %d %B %Y")
    print(f"🚀 PW Automation — Lakshya JEE 2027 [{mode.upper()} MODE]")
    print(f"📅 {today}\n")

    # Token check
    try:
        check_token_valid()
        print("✅ Token is valid\n")
    except RuntimeError as e:
        if "TOKEN_EXPIRED" in str(e):
            msg = (
                "TOKEN EXPIRED\n\n"
                "Fix:\n"
                "1. pw.live → any group → manually create one poll\n"
                "2. F12 → Network → POST create-poll → copy Authorization header\n"
                "3. GitHub Secrets → PW_TOKEN → Update\n"
                "4. Actions → Run workflow\n"
            )
            print(msg)
            send_alert_email("🔴 Lakshya — Token Expired", msg)
            exit(1)

    if mode == "quiz":
        run_quiz_mode()
    else:
        run_solution_mode()


if __name__ == "__main__":
    main()