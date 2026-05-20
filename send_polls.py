"""
================================================================
  PW POLL AUTOMATION - LAKSHYA JEE 2027
  Edit POLLS section below daily (on GitHub website or phone)
  Everything else is permanent — never needs changing
================================================================
"""

import requests
import time
import os
import json
import smtplib
from email.mime.text import MIMEText

# ── AUTH (from GitHub Secret — update weekly) ────────────────
AUTH_TOKEN = os.environ["PW_TOKEN"]   # full "Bearer eyJ..." string

# ── EMAIL ALERT CONFIG (from GitHub Secrets) ─────────────────
ALERT_EMAIL   = os.environ["ALERT_EMAIL"]    # your gmail address
GMAIL_APP_PWD = os.environ["GMAIL_APP_PWD"]  # gmail app password (not your real password)

# ── PERMANENT IDs (never change these) ───────────────────────
CLIENT_ID  = "5eb393ee95fab7468a79d189"
BATCH_ID   = "6779345c20fa0756e4a7fd08"
API_BASE   = "https://api.penpencil.co"
DELAY_SEC  = 1.5

# ── 5 GROUPS (permanent) ─────────────────────────────────────
GROUPS = [
    {"name": "Group 1", "groupId": "69cb7c5e4a6bd7893a91aa22", "conversationId": "69ce5c7c8a5087b50b14c482"},
    {"name": "Group 2", "groupId": "69cb7c67e223436a272111c9", "conversationId": "69ce5d26b3e8f731557c9116"},
    {"name": "Group 3", "groupId": "69cb7c6cd6e4a400b77ebccc", "conversationId": "69ce5f7b369fd47f199d51a8"},
    {"name": "Group 4", "groupId": "69cb7c7036b09e3dea135a30", "conversationId": "69ce60754bd6bffed97b9eeb"},
    {"name": "Group 5", "groupId": "69cb7c7426c54583a30f3039", "conversationId": "69ce60a65155c4ac4c289fea"},
]

# ================================================================
#  ✏️  EDIT THIS SECTION DAILY — change questions and options
#  correct: 1=A, 2=B, 3=C, 4=D
# ================================================================
POLLS = [
    {"question": "Question 1 Poll", "options": ["A", "B", "C", "D"], "correct": 1},
    {"question": "Question 2 Poll", "options": ["A", "B", "C", "D"], "correct": 1},
    {"question": "Question 3 Poll", "options": ["A", "B", "C", "D"], "correct": 1},
    {"question": "Question 4 Poll", "options": ["A", "B", "C", "D"], "correct": 1},
    {"question": "Question 5 Poll", "options": ["A", "B", "C", "D"], "correct": 1},
]
# ================================================================


def send_alert_email(subject: str, body: str):
    """Send a Gmail alert when something goes wrong."""
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


def get_headers() -> dict:
    return {
        "Content-Type":  "application/json",
        "Authorization": AUTH_TOKEN,
        "Client-Id":     CLIENT_ID,
        "Client-Type":   "WEB",
        "x-sdk-version": "0.0.20",
    }


def check_token_valid():
    """Quick check — hit a lightweight endpoint to verify token works."""
    res = requests.get(
        f"{API_BASE}/v1/users/get-user-detail",
        headers=get_headers(),
        timeout=10
    )
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
    data = res.json()
    if res.status_code in (200, 201) and data.get("data", {}).get("pollId"):
        return data["data"]
    raise RuntimeError(f"create-poll failed ({res.status_code}): {data.get('message', data)}")


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
    data = res.json()
    if res.status_code not in (200, 201):
        raise RuntimeError(f"chat failed ({res.status_code}): {data.get('message', data)}")


def main():
    print("🚀 PW Poll Automation starting — Lakshya JEE 2027")

    # ── Token check first ──────────────────────────────────────
    try:
        check_token_valid()
        print("✅ Token is valid\n")
    except RuntimeError as e:
        if "TOKEN_EXPIRED" in str(e):
            msg = (
                "⚠️ PW Poll Automation — TOKEN EXPIRED\n\n"
                "Your PW_TOKEN in GitHub Secrets has expired.\n\n"
                "Steps to fix (takes 2 minutes):\n"
                "  1. Open pw.live in Chrome (logged in)\n"
                "  2. F12 → Network tab → click any api.penpencil.co request\n"
                "  3. Copy the full 'Authorization' header value\n"
                "  4. Go to GitHub → lakshya-polls → Settings → Secrets → PW_TOKEN → Update\n\n"
                "Polls were NOT sent today. After updating the token, "
                "go to Actions → Run workflow to send them manually.\n"
            )
            print(msg)
            send_alert_email("🔴 Lakshya Polls FAILED — Token Expired", msg)
            exit(1)

    # ── Send all polls ─────────────────────────────────────────
    total   = len(POLLS) * len(GROUPS)
    success = 0
    fail    = 0
    failures = []

    print(f"📋 {len(POLLS)} polls × {len(GROUPS)} groups = {total} total\n")

    for group in GROUPS:
        print(f"📂 {group['name']}")
        for i, poll in enumerate(POLLS):
            try:
                poll_data = create_poll(group, poll)
                post_poll_to_chat(group, poll, poll_data)
                print(f"  ✅ Poll {i+1} ({poll['question']}) → {group['name']}")
                success += 1
            except Exception as e:
                msg = f"Poll {i+1} ({poll['question']}) → {group['name']}: {e}"
                print(f"  ❌ {msg}")
                failures.append(msg)
                fail += 1
            time.sleep(DELAY_SEC)

    # ── Final summary ──────────────────────────────────────────
    print(f"\n🎉 Done! {success} sent, {fail} failed out of {total} total.")

    if fail > 0:
        email_body = (
            f"⚠️ PW Poll Automation finished with {fail} failure(s).\n\n"
            f"✅ Successful: {success}/{total}\n"
            f"❌ Failed: {fail}/{total}\n\n"
            "Failed polls:\n" + "\n".join(f"  • {f}" for f in failures) +
            "\n\nCheck GitHub Actions logs for details."
        )
        send_alert_email(f"⚠️ Lakshya Polls — {fail} poll(s) failed today", email_body)
        exit(1)


if __name__ == "__main__":
    main()
