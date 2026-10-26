# Learning Revision

Adaptive revision for topics you have already studied. You keep groups of topics. The app chooses what to revise, asks questions, and tracks weak subtopics. This repository contains the design, the API, and the web app.

The product design is in [DESIGN.md](DESIGN.md). How to run it on the Windows PC is in [PRODUCTION.md](PRODUCTION.md).

## What works now

- Admin and user accounts. Passwords are hashed. There is no public sign-up.
- Login stays active: the access token is short, and a refresh token lasts 180 days and moves forward each time the app refreshes it.
- Groups, topics, and subtopics you add yourself.
- Per-user AI key storage. The key is encrypted. The API returns only the last four characters.
- Next-topic preview and a saved session. The 70/30 rule picks the topic, at most once a day. UCB picks the subtopics, and a low score is retested next time.
- With an AI key saved, adding a topic builds its subtopic map. A session then asks, scores the answer, stores the result, and sets the next review date.
- Token use is split into first-time subtopics, weekly subtopics, revision, and other.

The weekly subtopic job is not built yet. The web app and the Android APK are both clients of this API. Telegram sends a summary when a session finishes. At 23:59 in your timezone it also sends that day's token use and the standing of each group and topic, and only when tokens were used. If that day is above 10% of the previous 7 days, the message says so.

## Run it locally

Python 3.9 or newer. From the project root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli gen-secrets
```

Copy the printed lines into `.env` at the project root (start from `.env.example`). Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` there as well. The password needs at least 10 characters. Then:

```bash
uvicorn app.main:app --reload --port 8000
```

On startup the API creates that admin if the username is not already in the database. It does not reset the password when the account already exists. `python -m app.cli create-admin --username admin` remains available when those two env values are left empty.

The database file is `data/app.db`. It is git-ignored.

Create further users with `POST /api/admin/users` while signed in as the admin. Interactive API docs are at http://localhost:8000/docs.

## Web app

From `frontend/`, with the API already running on port 8000:

```bash
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to the backend, so the login cookie stays on the same origin.

## Windows server

The always-on Windows PC runs the website and the API as one process. From the project folder, after the one-time setup in [PRODUCTION.md](PRODUCTION.md):

```cmd
run.bat build
run.bat serve
```

That listens on http://127.0.0.1:8000. The Cloudflare tunnel you already use for the planner gets one more hostname, `learn.sasanksankaran.in`, aimed at that port.

## Android APK

The phone app opens `https://learn.sasanksankaran.in`, so a website update shows up the next time you open it. A new APK is only for a change to the Android package. When one is published, the app asks you to update and the new package replaces the old one. The steps are in [PRODUCTION.md](PRODUCTION.md).

The installable file is `learning-revision.apk` in this folder. Copy it to the phone and open it. Android will ask you to allow an install from that source. The login screen shows the server address. If an older address was saved on the phone, replace it with `https://learn.sasanksankaran.in`.

To rebuild after a frontend change, from `frontend/`:

```bash
npm run build:apk
cd android && ./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ../../learning-revision.apk
```

## Tests

```bash
cd backend
python -m pytest
```
