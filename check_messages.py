"""
check_messages.py — Lakshya JEE 2027 Student Message Monitor

Checks PW mentorship conversations four times a day.

- Ignores Aman/mentor messages
- Detects new student messages
- Sends Gmail alert
- Persists seen message IDs
- Does NOT fail the entire workflow because one group returns 403/404
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://api.penpencil.co"

CLIENT_ID = "5eb393ee95fab7468a79d189"

# This is your sender ID from the actual PW response.
MY_SENDER_ID = "69ca46ebb7bb9d3b7e522108"

STATE_FILE = Path("message_monitor_state.json")


# These are the IDs currently present in send_polls.py
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


# ============================================================
# SECRETS
# ============================================================

PW_TOKEN = os.environ["PW_TOKEN"]
ALERT_EMAIL = os.environ["ALERT_EMAIL"]
GMAIL_APP_PWD = os.environ["GMAIL_APP_PWD"]


# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "Authorization": f"Bearer {PW_TOKEN}",
    "client-id": CLIENT_ID,
    "client-type": "WEB",
    "origin": "https://www.pw.live",
    "referer": "https://www.pw.live/",
    "x-sdk-version": "0.0.28",
    "randomid": "2f81cbed-4d22-4f57-994e-3f78dbf6e309",
}


# ============================================================
# LOGGING
# ============================================================

def log(message):
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] {message}",
        flush=True
    )


# ============================================================
# STATE
# ============================================================

def load_state():

    if not STATE_FILE.exists():
        return {
            "initialized": False,
            "seen_ids": {}
        }

    try:

        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(state, dict):
            raise ValueError("Invalid state format")

        state.setdefault("initialized", False)
        state.setdefault("seen_ids", {})

        return state

    except Exception as e:

        log(f"⚠️ Could not read state: {e}")

        return {
            "initialized": False,
            "seen_ids": {}
        }


def save_state(state):

    temp_file = STATE_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp_file.replace(STATE_FILE)


# ============================================================
# FETCH PW CHAT
# ============================================================

def get_messages(group):

    conversation_id = group["conversationId"]

    url = (
        f"{BASE_URL}/v1/conversation/"
        f"{conversation_id}/chat"
    )

    params = {
        "page": 1,
        "limit": 50
    }

    log(f"  GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=20
    )

    log(
        f"  PW response: "
        f"{response.status_code}"
    )

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if response.status_code == 401:

        raise RuntimeError(
            "PW_TOKEN_EXPIRED"
        )

    # --------------------------------------------------------
    # Permission / inaccessible conversation
    # --------------------------------------------------------

    if response.status_code in (403, 404):

        log(
            f"  ⚠️ {group['name']} is not readable "
            f"({response.status_code})"
        )

        return None

    response.raise_for_status()

    payload = response.json()

    messages = payload.get("data", [])

    if not isinstance(messages, list):

        log(
            f"  ⚠️ Unexpected response format: "
            f"{str(payload)[:300]}"
        )

        return []

    return messages


# ============================================================
# MESSAGE HELPERS
# ============================================================

def message_time(message):

    return (
        message.get("createdAt")
        or message.get("updatedAt")
        or ""
    )


def message_sort_key(message):

    return (
        message_time(message),
        str(message.get("_id", ""))
    )


def message_text(message):

    text = message.get("text")

    if isinstance(text, str) and text.strip():
        return text.strip()

    message_type = str(
        message.get("type", "")
    ).lower()

    if message_type == "image":
        return "📷 Sent an image."

    if message_type == "poll":
        return "📊 Sent a poll."

    if message_type:
        return (
            f"[{message_type} message "
            f"with no text]"
        )

    return "[Message with no text]"


def format_time(value):

    if not value:
        return "Unknown time"

    try:

        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        ist = dt.astimezone(
            timezone(
                timedelta(
                    hours=5,
                    minutes=30
                )
            )
        )

        return ist.strftime(
            "%d %b %Y, %I:%M:%S %p IST"
        )

    except Exception:

        return value


# ============================================================
# EMAIL
# ============================================================

def send_email(new_messages):

    if not new_messages:
        return

    subject = (
        f"🔔 Lakshya JEE 2027 — "
        f"{len(new_messages)} new student message(s)"
    )

    lines = [
        "New student message(s) detected.",
        ""
    ]

    for item in new_messages:

        lines.extend([
            f"Group: {item['group_name']}",
            f"Student: {item['sender_name']}",
            f"Time: {format_time(item['created_at'])}",
            f"Type: {item['type']}",
            "",
            item["text"],
            "",
            "-" * 60,
            ""
        ])

    body = "\n".join(lines)

    message = MIMEText(
        body,
        "plain",
        "utf-8"
    )

    message["Subject"] = subject
    message["From"] = ALERT_EMAIL
    message["To"] = ALERT_EMAIL

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30
    ) as smtp:

        smtp.login(
            ALERT_EMAIL,
            GMAIL_APP_PWD
        )

        smtp.send_message(message)

    log(
        f"📧 Email sent for "
        f"{len(new_messages)} student message(s)"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("🚀 Student-message monitor starting")

    state = load_state()

    initialized = state.get(
        "initialized",
        False
    )

    seen_ids = state.get(
        "seen_ids",
        {}
    )

    next_seen = dict(seen_ids)

    all_new_messages = []

    readable_groups = 0
    inaccessible_groups = 0

    # ========================================================
    # CHECK EVERY GROUP
    # ========================================================

    for group in GROUPS:

        log(
            f"Checking {group['name']}..."
        )

        try:

            messages = get_messages(group)

            # ------------------------------------------------
            # 403 / 404
            # ------------------------------------------------

            if messages is None:

                inaccessible_groups += 1

                continue

            readable_groups += 1

            messages = sorted(
                messages,
                key=message_sort_key
            )

            conversation_id = (
                group["conversationId"]
            )

            previous_ids = set(
                next_seen.get(
                    conversation_id,
                    []
                )
            )

            # =================================================
            # FIRST RUN
            # =================================================

            if not initialized:

                current_ids = [
                    str(m["_id"])
                    for m in messages
                    if m.get("_id")
                ]

                next_seen[
                    conversation_id
                ] = current_ids[-200:]

                log(
                    f"  🟢 Baseline created: "
                    f"{len(current_ids)} messages"
                )

                continue

            # =================================================
            # FIND NEW STUDENT MESSAGES
            # =================================================

            group_new = []

            for message in messages:

                message_id = message.get("_id")

                if not message_id:
                    continue

                message_id = str(message_id)

                # Already seen
                if message_id in previous_ids:
                    continue

                sender_id = str(
                    message.get(
                        "sender",
                        ""
                    )
                )

                # Ignore Aman
                if sender_id == MY_SENDER_ID:
                    continue

                group_new.append({
                    "group_name": group["name"],
                    "sender_name": (
                        message.get(
                            "senderName"
                        )
                        or "Unknown student"
                    ),
                    "created_at": (
                        message.get(
                            "createdAt"
                        )
                        or message.get(
                            "updatedAt"
                        )
                        or ""
                    ),
                    "type": (
                        message.get(
                            "type"
                        )
                        or "unknown"
                    ),
                    "text": message_text(
                        message
                    )
                })

            all_new_messages.extend(
                group_new
            )

            # ------------------------------------------------
            # Save all fetched IDs
            # ------------------------------------------------

            fetched_ids = [
                str(m["_id"])
                for m in messages
                if m.get("_id")
            ]

            merged = list(
                dict.fromkeys(
                    list(previous_ids)
                    + fetched_ids
                )
            )

            next_seen[
                conversation_id
            ] = merged[-200:]

            log(
                f"  New student messages: "
                f"{len(group_new)}"
            )

        except Exception as e:

            log(
                f"  ❌ {group['name']} error: {e}"
            )

            # Don't kill all other groups.
            continue

    # ========================================================
    # SAVE STATE
    # ========================================================

    state["seen_ids"] = next_seen
    state["initialized"] = True

    save_state(state)

    # ========================================================
    # EMAIL
    # ========================================================

    if all_new_messages:

        send_email(
            all_new_messages
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    log("")
    log("========================================")
    log("MONITOR SUMMARY")
    log("========================================")

    log(
        f"Readable groups: "
        f"{readable_groups}/5"
    )

    log(
        f"Inaccessible groups: "
        f"{inaccessible_groups}/5"
    )

    log(
        f"New student messages: "
        f"{len(all_new_messages)}"
    )

    if inaccessible_groups:

        log(
            "⚠️ Some conversations returned "
            "403/404. Their IDs need to be "
            "verified from PW Network requests."
        )

    log("========================================")
    log("✅ Monitor run complete")


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log(
            f"❌ Fatal monitor error: {e}"
        )

        sys.exit(1)