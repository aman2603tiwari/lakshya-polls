# PW Mentorship Automation — Lakshya JEE 2027

Fully automated daily content delivery to PW (PhysicsWallah) mentorship groups using GitHub Actions + Groq AI. Zero manual work except refreshing your PW token every ~7 days.

---

## What It Does

| Time | What gets sent | Where |
|---|---|---|
| **8:00 AM** daily | AI-generated motivational quote (text) | All 5 groups |
| **1:00 PM** Mon–Fri | Intro message + 5 JEE PYQ polls | All 5 groups |
| **3:00 PM** Mon/Wed/Fri | Random IIT campus photo + AI caption | All 5 groups |
| **5:00 PM** daily | Evening check-in (Saturday = weekly review) | All 5 groups |
| **10:00 PM** Mon–Fri | Detailed solutions to today's 5 polls | All 5 groups |

Everything runs on GitHub's free servers. **Your PC does not need to be on.**

---

## Prerequisites

- GitHub account (free)
- Gmail account
- PW Mentor account with active batch groups
- Groq account (free) — [console.groq.com](https://console.groq.com)
- Google Cloud account (free) — only needed for college photo mode

---

## Step 1 — Fork / Create the Repo

1. Go to [github.com](https://github.com) → **New repository**
2. Name it `lakshya-polls` → set to **Private** → Create
3. Upload these files to the repo root:
   - `send_polls.py`
   - `.github/workflows/daily.yml`

Your repo structure should look like:
```
lakshya-polls/
├── send_polls.py
└── .github/
    └── workflows/
        └── daily.yml
```

> To create `.github/workflows/daily.yml` on GitHub: click **Add file → Create new file** → type `.github/workflows/daily.yml` in the filename box. GitHub creates the folders automatically.

---

## Step 2 — Find Your PW Group IDs

Each group needs two IDs: `groupId` and `conversationId`. Both are in the URL when you open a group chat on pw.live.

**How to find them:**
1. Open `pw.live` in Chrome → go to any of your mentorship groups
2. Look at the URL — it looks like:
```
https://www.pw.live/study-v2/mentorship/chat/BATCH_ID?conversationId=CONV_ID&groupId=GROUP_ID&...
```
3. Copy `conversationId` and `groupId` from the URL for each group
4. Also copy `batchId` from the URL (same for all groups in the same batch)

Update the `GROUPS` list and `BATCH_ID` in `send_polls.py`:
```python
BATCH_ID = "your_batch_id_here"

GROUPS = [
    {"name": "Group 1", "groupId": "...", "conversationId": "..."},
    {"name": "Group 2", "groupId": "...", "conversationId": "..."},
    # ... add all your groups
]
```

---

## Step 3 — Get Your PW Token

The PW token authenticates API calls as you. It expires every ~7 days.

**How to get it:**
1. Open `pw.live` in Chrome (must be logged in as your mentor account)
2. Go to any group → **manually create one poll** in the UI
3. Press **F12** → click **Network** tab
4. Find the **POST** request to `api.penpencil.co/v2/poll/create-poll`
5. Click it → **Headers** tab → scroll to **Request Headers**
6. Copy the full `authorization` value (starts with `Bearer eyJ...`)

> ⚠️ You must copy from the **POST** `create-poll` request specifically — not from any other request. This ensures the token has Mentor-level permissions.

---

## Step 4 — Get a Groq API Key

1. Go to [console.groq.com](https://console.groq.com) → Sign up (free)
2. Click **API Keys** → **Create API Key**
3. Copy the key (starts with `gsk_...`)

---

## Step 5 — Set Up Gmail App Password

Gmail requires an App Password for scripts (not your regular password).

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Make sure **2-Step Verification** is ON (Security → 2-Step Verification)
3. Search **"App passwords"** in the search bar → click it
4. App name: type `lakshya-polls` → click **Create**
5. Copy the 16-character password shown — **no spaces**

---

## Step 6 — Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add all of these:

| Secret Name | Value | Notes |
|---|---|---|
| `PW_TOKEN` | `Bearer eyJ0eXAiOiJKV1Q...` | Full value including "Bearer " |
| `GROQ_API_KEY` | `gsk_...` | From console.groq.com |
| `ALERT_EMAIL` | `your@gmail.com` | Where email alerts go |
| `GMAIL_APP_PWD` | `abcdabcdabcdabcd` | 16 chars, no spaces |
| `GDRIVE_SA_JSON` | `{"type":"service_account",...}` | Only needed for college photos |
| `GDRIVE_FOLDER_ID` | `1BxiMVs0...` | Only needed for college photos |

---

## Step 7 — Set Up Google Drive (College Photos — Optional)

Skip this step if you don't want the college photo feature.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project → name it `lakshya-polls`
3. Search **"Google Drive API"** → Enable it
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
   - Name: `lakshya-drive` → Create → Done
5. Click the service account → **Keys** tab → **Add Key → Create new key → JSON**
   - A `.json` file downloads — open it, copy the entire contents
6. In Google Drive: create a folder named `college_photos`
   - Right-click folder → **Share** → paste the service account email (shown in Google Cloud console)
   - Give it **Editor** access (needed to save today's quiz questions there too)
7. Copy the folder ID from the URL: `drive.google.com/drive/folders/**THIS_PART**`
8. Add to GitHub Secrets:
   - `GDRIVE_SA_JSON` = paste the entire JSON file contents
   - `GDRIVE_FOLDER_ID` = the folder ID

**Adding photos:** Just drag IIT campus photos into the `college_photos` Drive folder. They're auto-discovered. No code changes needed.

---

## Step 8 — Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. Click **I understand my workflows, enable them**
3. You should see **"Lakshya JEE 2027 — Daily Automation"** in the left sidebar

---

## Step 9 — Test It

Run each mode manually to verify everything works:

1. Actions tab → **Lakshya JEE 2027 — Daily Automation** → **Run workflow**
2. Select `motivation` → Run → wait ~30 seconds → check your groups
3. Repeat with `quiz` → check that polls appear in all groups
4. Repeat with `solution` → check that solutions appear

Green checkmark ✅ = success. Red ✗ = click the run → click "run" job → scroll logs to find the error.

---

## Daily Workflow (After Setup)

**Your only regular task:**

Every ~7 days when your token expires, you'll get an email alert with subject:
`❌ ... Token likely expired`

**To fix (takes 2 minutes):**
1. Open `pw.live` → any group → create one test poll manually
2. F12 → Network → POST `create-poll` request → copy `authorization` header
3. GitHub → repo → Settings → Secrets → `PW_TOKEN` → Update → paste → Save
4. Done ✅

---

## Customizing the Script

All easy edits are at the top of `send_polls.py`:

### Change subject rotation
```python
SUBJECT_MIXES = [
    ("Physics", "Physics", "Chemistry", "Chemistry", "Maths"),  # Monday
    ("Maths",   "Maths",   "Physics",   "Chemistry", "Chemistry"),  # Tuesday
    # ... one per weekday
]
```

### Change schedule times
Edit `daily.yml`. Times are in UTC (IST = UTC + 5:30):
```yaml
- cron: '30 7 * * 1-5'   # 1:00 PM IST = 7:30 AM UTC
- cron: '30 16 * * 1-5'  # 10:00 PM IST = 4:30 PM UTC
```
Use [crontab.guru](https://crontab.guru) to calculate UTC times.

### Add PYQ text files (better questions)
For more accurate JEE questions, extract text from Arihant PYQ books and commit:
```
pdfs/
├── physics_pyq.txt
├── chemistry_pyq.txt
└── maths_pyq.txt
```
The script auto-detects and uses these if present. Without them, Groq uses its own JEE knowledge.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Token expired` on all groups | Refresh `PW_TOKEN` secret (Step 3) |
| `prohibited word` error | PW blocked the AI message — script auto-retries with a safe fallback |
| `Groq failed` | Check `GROQ_API_KEY` secret is correct |
| `Email failed 535` | Recreate Gmail App Password (Step 5) — don't use your real password |
| Solutions not sending | Quiz mode must run first — check if it ran successfully that day |
| `400 type must be one of...` | Token has wrong permissions — must be copied from `create-poll` POST request specifically |
| College photos not sending | Check `GDRIVE_SA_JSON` and `GDRIVE_FOLDER_ID` secrets, and that the service account has Editor access to the folder |

---

## How the Script Works Internally

```
Quiz mode (1 PM):
  1. Picks today's subject mix from SUBJECT_MIXES
  2. Samples random 3000 chars from PYQ text files
  3. Calls Groq llama-3.3-70b-versatile → gets 5 questions as JSON
  4. Validates each question (must have question, 4 options, correct answer)
  5. Retries up to 8 times if malformed questions returned
  6. Calls Groq again for a fresh intro message
  7. Sends intro + 5 polls to each group (two-step: create-poll API → post to chat)
  8. Saves questions to todays_questions.json + Google Drive for solution mode

Solution mode (10 PM):
  1. Reads todays_questions.json (from Drive or local cache)
  2. For each group: sends "solutions coming" message + 5 solution messages
  3. Each solution shows: question, correct answer, step-by-step explanation
```

---

## File Reference

| File | Purpose | Edit? |
|---|---|---|
| `send_polls.py` | Main script — all 5 modes | Edit `GROUPS`, `BATCH_ID`, prompts |
| `.github/workflows/daily.yml` | Schedule + runner config | Edit cron times if needed |
| `history.json` | Auto-created — tracks used questions | Never edit manually |
| `todays_questions.json` | Auto-created — quiz saves, solution reads | Never edit manually |
| `sent_photos.json` | Auto-created — tracks sent college photos | Never edit manually |
| `pdfs/*.txt` | Optional PYQ text for better questions | Add your own extracted text |

---

## Cost

Everything is free:
- GitHub Actions: free for public repos, 2000 min/month for private (this uses ~5 min/day = ~150 min/month)
- Groq API: free tier (sufficient for this usage)
- Gmail: free
- Google Drive API: free

---

*Built for Lakshya JEE 2027 mentorship program. Runs 5 automated workflows daily, 5 days a week.*