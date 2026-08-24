"""
check_messages.py — Lakshya JEE 2027 Student Message Monitor

Runs from GitHub Actions four times a day.

Behavior:
    1. Check all five PW mentorship conversations.
    2. Fetch the latest 50 messages from each conversation.
    3. Compare message IDs against the persistent state.
    4. Ignore messages sent by Aman.
    5. If one or more NEW student messages exist:
           -> send one email containing all student messages.
    6. If there are NO new student messages:
           -> still send one email saying:
              "No new messages by any student."
    7. Save the latest message IDs so messages are not reported twice.

Important:
    The first run establishes the baseline and does NOT report old
    messages as new.
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
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.penpencil.co"

CLIENT_ID = "5eb393ee95fab7468a79d189"

# Aman Tiwari's sender ID obtained from the actual PW API response.
MY_SENDER_ID = "69ca46ebb7bb9d3b7e522108"

STATE_FILE = Path("message_monitor_state.json")


# ============================================================
# FIVE PW GROUPS
# ============================================================

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
# GITHUB SECRETS
# ============================================================

PW_TOKEN = os.environ["PW_TOKEN"]
ALERT_EMAIL = os.environ["ALERT_EMAIL"]
GMAIL_APP_PWD = os.environ["GMAIL_APP_PWD"]


# ============================================================
# PW API HEADERS
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
        flush=True,
    )


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():

    if not STATE_FILE.exists():

        return {
            "initialized": False,
            "seen_ids": {},
        }

    try:

        state = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(state, dict):
            raise ValueError(
                "State file is not a JSON object"
            )

        state.setdefault(
            "initialized",
            False
        )

        state.setdefault(
            "seen_ids",
            {}
        )

        return state

    except Exception as e:

        log(
            f"⚠️ Could not read state file: {e}"
        )

        return {
            "initialized": False,
            "seen_ids": {},
        }


def save_state(state):

    temp_file = STATE_FILE.with_suffix(
        ".tmp"
    )

    temp_file.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_file.replace(STATE_FILE)


# ============================================================
# FETCH MESSAGES
# ============================================================

def get_messages(group):

    conversation_id = group[
        "conversationId"
    ]

    url = (
        f"{BASE_URL}/v1/conversation/"
        f"{conversation_id}/chat"
    )

    params = {
        "page": 1,
        "limit": 50,
    }

    log(
        f"  GET {url}?page=1&limit=50"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=20,
    )

    log(
        f"  PW response: "
        f"{response.status_code}"
    )

    # --------------------------------------------------------
    # TOKEN EXPIRED
    # --------------------------------------------------------

    if response.status_code == 401:

        raise RuntimeError(
            "PW_TOKEN_EXPIRED"
        )

    # --------------------------------------------------------
    # GROUP NOT ACCESSIBLE
    # --------------------------------------------------------

    if response.status_code in (
        403,
        404,
    ):

        log(
            f"  ⚠️ {group['name']} "
            f"is not readable "
            f"({response.status_code})"
        )

        return None

    # --------------------------------------------------------
    # OTHER HTTP ERROR
    # --------------------------------------------------------

    response.raise_for_status()

    payload = response.json()

    messages = payload.get(
        "data",
        []
    )

    if not isinstance(
        messages,
        list,
    ):

        log(
            "  ⚠️ Unexpected PW response "
            "format."
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
        str(
            message.get(
                "_id",
                ""
            )
        ),
    )


def message_text(message):

    text = message.get(
        "text"
    )

    if (
        isinstance(text, str)
        and text.strip()
    ):

        return text.strip()

    message_type = str(
        message.get(
            "type",
            ""
        )
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
            value.replace(
                "Z",
                "+00:00"
            )
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
# EMAIL — NEW STUDENT MESSAGES
# ============================================================

def send_student_messages_email(
    new_messages,
    checked_at,
    inaccessible_groups,
):

    subject = (
        "🔔 Lakshya JEE 2027 — "
        f"{len(new_messages)} New Student Message"
    )

    if len(new_messages) != 1:
        subject += "s"

    lines = [
        "New student message(s) detected "
        "in your PW Lakshya JEE 2027 groups.",
        "",
        f"Monitor check time: "
        f"{checked_at}",
        "",
    ]

    for index, item in enumerate(
        new_messages,
        start=1,
    ):

        lines.extend(
            [
                "=" * 60,
                f"STUDENT MESSAGE #{index}",
                "=" * 60,
                "",
                f"Group: {item['group_name']}",
                f"Student: {item['sender_name']}",
                f"Message time: "
                f"{format_time(item['created_at'])}",
                f"Type: {item['type']}",
                "",
                "Message:",
                item["text"],
                "",
            ]
        )

    if inaccessible_groups:

        lines.extend(
            [
                "",
                "-" * 60,
                "WARNING",
                "-" * 60,
                "",
                "The following groups could not be checked:",
                "",
            ]
        )

        for group in inaccessible_groups:
            lines.append(
                f"- {group['name']} "
                f"({group['status']})"
            )

    body = "\n".join(lines)

    send_email(
        subject,
        body,
    )


# ============================================================
# EMAIL — NO NEW STUDENT MESSAGES
# ============================================================

def send_no_messages_email(
    checked_at,
    checked_groups,
    inaccessible_groups,
    own_new_messages,
):

    subject = (
        "✅ Lakshya JEE 2027 — "
        "No New Student Messages"
    )

    lines = [
        "No new messages by any student.",
        "",
        f"Monitor check time: "
        f"{checked_at}",
        "",
        "Summary:",
        f"- Groups checked successfully: "
        f"{checked_groups}/5",
        f"- New messages sent by Aman: "
        f"{own_new_messages}",
        "- New messages by students: 0",
        "",
    ]

    if inaccessible_groups:

        lines.extend(
            [
                "⚠️ WARNING:",
                "",
                "Some groups could not be checked:",
                "",
            ]
        )

        for group in inaccessible_groups:

            lines.append(
                f"- {group['name']} "
                f"({group['status']})"
            )

        lines.extend(
            [
                "",
                "Therefore this is NOT a complete "
                "five-group check.",
                "",
            ]
        )

    else:

        lines.extend(
            [
                "All 5 PW groups were checked "
                "successfully.",
                "",
            ]
        )

    lines.extend(
        [
            "Result:",
            "",
            "No new message from any student "
            "was detected.",
        ]
    )

    body = "\n".join(lines)

    send_email(
        subject,
        body,
    )


# ============================================================
# EMAIL — MONITOR INCOMPLETE / FAILED
# ============================================================

def send_monitor_failure_email(checked_at, checked_groups, failed_groups):
    """Send a warning when not all groups were successfully checked."""
    subject = (
        "⚠️ Lakshya JEE 2027 — Message Monitor Incomplete"
    )

    lines = [
        "The student-message monitor could NOT complete a full check.",
        "",
        f"Monitor check time: {checked_at}",
        "",
        "Summary:",
        f"- Groups checked successfully: {checked_groups}/{len(GROUPS)}",
        f"- Groups failed/inaccessible: {len(failed_groups)}/{len(GROUPS)}",
        "",
        "IMPORTANT: No conclusion about new student messages can be made.",
        "The monitor will NOT report 'No New Student Messages' when any",
        "group could not be checked.",
        "",
        "Failed groups:",
        "",
    ]

    for group in failed_groups:
        lines.append(
            f"- {group['name']}: {group['status']}"
        )

    body = "\n".join(lines)
    send_email(subject, body)


# ============================================================
# COMMON EMAIL SENDER
# ============================================================

def send_email(
    subject,
    body,
):

    message = MIMEText(
        body,
        "plain",
        "utf-8",
    )

    message["Subject"] = subject
    message["From"] = ALERT_EMAIL
    message["To"] = ALERT_EMAIL

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30,
    ) as smtp:

        smtp.login(
            ALERT_EMAIL,
            GMAIL_APP_PWD,
        )

        smtp.send_message(
            message
        )

    log(
        f"📧 Email sent: {subject}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log(
        "🚀 Student-message monitor starting"
    )

    # --------------------------------------------------------
    # Current check time
    # --------------------------------------------------------

    checked_at = datetime.now(
        timezone.utc
    ).astimezone(
        timezone(
            timedelta(
                hours=5,
                minutes=30
            )
        )
    ).strftime(
        "%d %b %Y, %I:%M:%S %p IST"
    )

    log(
        f"Check time: {checked_at}"
    )

    # --------------------------------------------------------
    # Load persistent state
    # --------------------------------------------------------

    state = load_state()

    initialized = state.get(
        "initialized",
        False
    )

    seen_ids = state.get(
        "seen_ids",
        {}
    )

    next_seen = dict(
        seen_ids
    )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    all_new_student_messages = []

    checked_groups = 0

    inaccessible_groups = []

    own_new_messages = 0

    # ========================================================
    # CHECK ALL FIVE GROUPS
    # ========================================================

    for group in GROUPS:

        log(
            f"Checking {group['name']}..."
        )

        try:

            messages = get_messages(
                group
            )

            # ------------------------------------------------
            # 403 / 404
            # ------------------------------------------------

            if messages is None:

                inaccessible_groups.append(
                    {
                        "name": group["name"],
                        "status": "403/404",
                    }
                )

                continue

            checked_groups += 1

            # ------------------------------------------------
            # Sort oldest → newest
            # ------------------------------------------------

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
                    str(
                        message["_id"]
                    )
                    for message in messages
                    if message.get(
                        "_id"
                    )
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
            # FIND NEW MESSAGES
            # =================================================

            group_student_messages = []

            for message in messages:

                message_id = message.get(
                    "_id"
                )

                if not message_id:
                    continue

                message_id = str(
                    message_id
                )

                # ------------------------------------------------
                # Already processed
                # ------------------------------------------------

                if message_id in previous_ids:
                    continue

                # ------------------------------------------------
                # New message
                # ------------------------------------------------

                sender_id = str(
                    message.get(
                        "sender",
                        ""
                    )
                )

                # ------------------------------------------------
                # OUR OWN MESSAGE
                # ------------------------------------------------

                if sender_id == MY_SENDER_ID:

                    own_new_messages += 1

                    log(
                        f"  ↳ New message from Aman "
                        f"ignored: {message_id}"
                    )

                    continue

                # ------------------------------------------------
                # STUDENT MESSAGE
                # ------------------------------------------------

                student_message = {
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
                    ),
                }

                group_student_messages.append(
                    student_message
                )

            # ------------------------------------------------
            # Add this group's student messages
            # ------------------------------------------------

            all_new_student_messages.extend(
                group_student_messages
            )

            log(
                f"  New student messages: "
                f"{len(group_student_messages)}"
            )

            # =================================================
            # SAVE ALL FETCHED MESSAGE IDS
            # =================================================

            fetched_ids = [
                str(
                    message["_id"]
                )
                for message in messages
                if message.get(
                    "_id"
                )
            ]

            merged_ids = list(
                dict.fromkeys(
                    list(previous_ids)
                    + fetched_ids
                )
            )

            # Keep only the most recent 200 IDs.
            next_seen[
                conversation_id
            ] = merged_ids[-200:]

        except Exception as e:

            log(
                f"  ❌ {group['name']} error: {e}"
            )

            inaccessible_groups.append(
                {
                    "name": group["name"],
                    "status": str(e),
                }
            )

            # Continue checking other groups.
            continue

    # ========================================================
    # SAVE STATE
    # ========================================================

    state["seen_ids"] = next_seen

    # Only mark the global baseline as initialized after ALL groups
    # have been successfully checked. If a group was inaccessible
    # (for example because PW_TOKEN expired), do not establish a
    # partial baseline and do not risk treating unseen old messages
    # as new on a later run.
    if checked_groups == len(GROUPS) and not inaccessible_groups:
        state["initialized"] = True
    else:
        state["initialized"] = initialized

    save_state(
        state
    )

    log(
        "💾 Monitor state saved."
    )

    # ========================================================
    # SEND EXACTLY ONE EMAIL
    # ========================================================

    if inaccessible_groups:

        # ----------------------------------------------------
        # INCOMPLETE CHECK — DO NOT CLAIM NO MESSAGES
        # ----------------------------------------------------
        # If even one group failed, the monitor cannot safely
        # conclude that there are no new student messages.
        # This is especially important for 401/token failures.

        send_monitor_failure_email(
            checked_at=checked_at,
            checked_groups=checked_groups,
            failed_groups=inaccessible_groups,
        )

    elif all_new_student_messages:

        # ----------------------------------------------------
        # STUDENT MESSAGES EXIST
        # ----------------------------------------------------

        send_student_messages_email(
            new_messages=all_new_student_messages,
            checked_at=checked_at,
            inaccessible_groups=inaccessible_groups,
        )

    else:

        # ----------------------------------------------------
        # NO NEW STUDENT MESSAGES
        # ----------------------------------------------------
        # This branch is reached ONLY after all five groups
        # have been successfully checked.

        send_no_messages_email(
            checked_at=checked_at,
            checked_groups=checked_groups,
            inaccessible_groups=inaccessible_groups,
            own_new_messages=own_new_messages,
        )

    # ========================================================
    # FINAL LOG SUMMARY
    # ========================================================

    log("")
    log(
        "========================================"
    )

    log(
        "MONITOR SUMMARY"
    )

    log(
        "========================================"
    )

    log(
        f"Groups checked successfully: "
        f"{checked_groups}/5"
    )

    log(
        f"Groups inaccessible/failed: "
        f"{len(inaccessible_groups)}/5"
    )

    log(
        f"New messages by Aman: "
        f"{own_new_messages}"
    )

    log(
        f"New messages by students: "
        f"{len(all_new_student_messages)}"
    )

    if inaccessible_groups:

        log(
            "⚠️ Some groups could not be checked."
        )

        for group in inaccessible_groups:

            log(
                f"   - {group['name']}: "
                f"{group['status']}"
            )

    log(
        "========================================"
    )

    log(
        "✅ Monitor run complete"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log(
            f"❌ Fatal monitor error: {e}"
        )

        sys.exit(1)