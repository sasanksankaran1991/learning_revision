# Production on the Windows PC

Build and try the app on the Mac. Run it on the Windows desktop that already stays on. That PC is the server. The database, the website, the API, and the Telegram poller all live in one process on that machine.

The public site is `https://learn.sasanksankaran.in`. The planner app stays on its own port. This app uses port **8000**.

```
Phone and browser
        │
        ▼
learn.sasanksankaran.in
        │
        ▼
Cloudflare Tunnel (already running for the planner)
        │
        ▼
http://127.0.0.1:8000  ←  python run.py serve
        ├── website (frontend/dist)
        ├── /api
        └── Telegram poller
data/app.db on that same PC
```

The router does not accept an inbound port. The PC opens the tunnel outward, the same way the planner does.

## One-time setup on Windows

Install Python 3.9 or newer, and Node.js. Then, in Command Prompt:

```cmd
cd C:\path\to\learning_revision
python -m venv backend\.venv
run.bat setup
copy .env.example .env
run.bat secrets
```

Put the two printed lines into `.env`. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` (at least 10 characters). For this public site also set:

```env
CORS_ORIGINS=https://learn.sasanksankaran.in
PUBLIC_BASE_URL=https://learn.sasanksankaran.in
COOKIE_SECURE=true
```

`run.bat serve` turns on the built website itself. You do not set `SERVE_WEB` by hand.

```cmd
run.bat build
run.bat serve
```

Open http://127.0.0.1:8000 on that PC and sign in before pointing the tunnel at it.

Leave that window open. Closing it stops the site and the Telegram poller. There is no second bot process.

## Add the hostname to the tunnel you already run

The planner tunnel stays as it is. Add this app as another hostname in the same `%USERPROFILE%\.cloudflared\config.yml`, above the final `http_status:404` line:

```yaml
ingress:
  - hostname: planner.sasanksankaran.in
    service: http://localhost:8080
  - hostname: learn.sasanksankaran.in
    service: http://localhost:8000
  - service: http_status:404
```

Create the DNS name once:

```cmd
cloudflared tunnel route dns YOUR_TUNNEL_NAME learn.sasanksankaran.in
```

Restart the tunnel window. `https://learn.sasanksankaran.in` then opens this app. `https://planner.sasanksankaran.in` keeps opening the planner.

If `cloudflared` is not on PATH, use the full path to `cloudflared.exe`, the same way the planner guide does.

## Keep it running

In Task Scheduler, start these at log on. Uncheck “Stop if runs longer than…”. The PC must stay logged on.

| Task | Program | Arguments | Start in |
|---|---|---|---|
| LearningRevision | `C:\path\to\learning_revision\backend\.venv\Scripts\python.exe` | `run.py serve` | the project folder |
| LearningRevision-Backup | same `python.exe` | `run.py backup` | the project folder, daily |
| The tunnel task you already have | `cloudflared.exe` | the same `tunnel run` command | unchanged |

`run.py backup` copies `data/app.db` into `data/backups` and keeps the newest 14 copies. The app can stay running while the copy is made.

## Moving the study data once

A new `.env` and an empty `data/app.db` start a new admin and an empty library. To keep the accounts, topics, and scores already created on the Mac, copy these two things once and then leave the Mac copy behind:

- `data/app.db`
- `JWT_SECRET` and `FERNET_KEYS` from the Mac `.env`

The encrypted AI key and Telegram token can be read only with that same `FERNET_KEYS`. After the copy, the Windows `data/app.db` is the one that matters. Later backups belong on the Windows PC.

## Updating the website

On the Windows PC:

```cmd
git pull
run.bat setup
run.bat build
```

Stop the serve window and start `run.bat serve` again. `git pull` does not touch `.env` or `data/app.db`.

Close the phone app and open it again. It loads `https://learn.sasanksankaran.in`, so the new screens are there. That does not need an install.

## Updating the phone app itself

A new APK is only for a change inside the Android package, such as a permission or the address the app opens. Build it on this Mac with the same debug key as last time, and install it over the app that is already there. Android replaces it. Uninstall only if Android says the signatures do not match.

The phone asks when a newer package is on the server. After `npm run build:apk` and the Gradle build, copy `learning-revision.apk` to the Windows folder `data/releases/`. Next to it, save `data/releases/release.json`:

```json
{"version_code": 3, "version_name": "1.2", "notes": "What changed in the phone app."}
```

`version_code` must be the number in `frontend/android/app/build.gradle`, and it must be higher than the app on the phone. The next time the app opens it shows Update. Update downloads the package. Then tap Install. Not now hides the bar until the next time you open the app.

The current package is version code 2. Leave `release.json` absent until you actually copy a newer apk into `data/releases/`. The bar stays hidden while that file is missing.

## What still points at the hostname

| Client | Where the address is set |
|---|---|
| Website | The page and `/api` are the same host, so the built site sends `/api/...` with no extra setting |
| APK | The app opens `https://learn.sasanksankaran.in`. The login screen still has a Server field if that address was saved on the phone |
| Tunnel | The ingress line for port 8000 |

If the phone still has an older server address saved, change Server to `https://learn.sasanksankaran.in` and sign in again.

## Mac, while you are still building

Leave `COOKIE_SECURE=false` in the Mac `.env` and keep `http://localhost:5173` in `CORS_ORIGINS`. Use the API on port 8000 and `npm run dev` in `frontend/`. That dev site proxies `/api` to the API. It does not need `run.py serve`.
