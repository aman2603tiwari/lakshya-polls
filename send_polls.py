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

# ── AUTH (loaded from GitHub Secret — never hardcode here) ───
PW_USERNAME = os.environ["PW_USERNAME"]   # your phone number
PW_PASSWORD = os.environ["PW_PASSWORD"]   # your PW password

# ── PERMANENT IDs (never change these) ───────────────────────
CLIENT_ID   = "5eb393ee95fab7468a79d189"
BATCH_ID    = "6779345c20fa0756e4a7fd08"
ORG_ID      = "5eb393ee95fab7468a79d189"
API_BASE    = "https://api.penpencil.co"
DELAY_SEC   = 1.5   # gap between each poll (be gentle on the API)

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
    {
        "question": "Question 1 Poll",
        "options":  ["A", "B", "C", "D"],
        "correct":  1,
    },
    {
        "question": "Question 2 Poll",
        "options":  ["A", "B", "C", "D"],
        "correct":  1,
    },
    {
        "question": "Question 3 Poll",
        "options":  ["A", "B", "C", "D"],
        "correct":  1,
    },
    {
        "question": "Question 4 Poll",
        "options":  ["A", "B", "C", "D"],
        "correct":  1,
    },
    {
        "question": "Question 5 Poll",
        "options":  ["A", "B", "C", "D"],
        "correct":  1,
    },
]
# ================================================================


def login() -> str:
    """Login to PW and return a fresh Bearer token."""
    print("🔐 Logging in to PW...")
    url = f"{API_BASE}/v3/users/login"
    payload = {
        "username":       PW_USERNAME,
        "password":       PW_PASSWORD,
        "organizationId": ORG_ID,
        "client_id":      CLIENT_ID,
    }
    headers = {
        "Content-Type": "application/json",
        "Client-Id":    CLIENT_ID,
        "Client-Type":  "WEB",
    }
    res = requests.post(url, json=payload, headers=headers)
    data = res.json()

    token = (
        data.get("data", {}).get("token") or
        data.get("token") or
        data.get("data", {}).get("accessToken")
    )
    if not token:
        raise RuntimeError(f"Login failed: {data}")

    print("✅ Login successful!")
    return f"Bearer {token}"


def get_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": token,
        "Client-Id":    CLIENT_ID,
        "Client-Type":  "WEB",
        "x-sdk-version": "0.0.20",
    }


def create_poll(token: str, group: dict, poll: dict) -> dict:
    url = f"{API_BASE}/v2/poll/create-poll"
    payload = {
        "type":           "SINGLE",
        "entityType":     "mentorship",
        "entityId":       group["groupId"],
        "poll_question":  poll["question"],
        "correctOption":  [poll["correct"]],
        "pollOptions": [
            {"option_id": i+1, "option_label": opt, "option_description": opt}
            for i, opt in enumerate(poll["options"])
        ],
    }
    res = requests.post(url, json=payload, headers=get_headers(token))
    data = res.json()
    if res.status_code in (200, 201) and data.get("data", {}).get("pollId"):
        return data["data"]
    raise RuntimeError(f"create-poll failed ({res.status_code}): {data.get('message', data)}")


def post_poll_to_chat(token: str, group: dict, poll: dict, poll_data: dict):
    url = f"{API_BASE}/v1/conversation/{group['conversationId']}/chat"
    import json
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
    res = requests.post(url, json=payload, headers=get_headers(token))
    data = res.json()
    if res.status_code not in (200, 201):
        raise RuntimeError(f"chat failed ({res.status_code}): {data.get('message', data)}")


def main():
    token = login()
    total = len(POLLS) * len(GROUPS)
    print(f"\n📋 {len(POLLS)} polls × {len(GROUPS)} groups = {total} total\n")

    success = 0
    fail    = 0

    for group in GROUPS:
        print(f"\n📂 {group['name']}")
        for i, poll in enumerate(POLLS):
            try:
                poll_data = create_poll(token, group, poll)
                post_poll_to_chat(token, group, poll, poll_data)
                print(f"  ✅ Poll {i+1} → {group['name']} — OK")
                success += 1
            except Exception as e:
                print(f"  ❌ Poll {i+1} → {group['name']} — FAILED: {e}")
                fail += 1
            time.sleep(DELAY_SEC)

    print(f"\n🎉 Done! {success} sent, {fail} failed out of {total} total.")
    if fail > 0:
        exit(1)   # makes GitHub Actions mark the run as failed so you notice


if __name__ == "__main__":
    main()
