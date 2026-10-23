# Pointing the app at learn.sasanksankaran.in

The website and the APK do not keep the study data. Each screen asks the API, and the API reads and writes `data/app.db` on the machine that runs it. Signing in on the phone and signing in on the website use the same groups, sessions, and scores when both call the same API.

The public address is `https://learn.sasanksankaran.in`.

## What calls what

| Client | Where the address is set | What it requests |
|---|---|---|
| Website | Same host as the page. Requests go to `/api/...` on that host. | Groups, topics, sessions, usage, settings |
| APK | Server field on the login screen. The default is `API_ORIGIN` in `frontend/src/api.ts`. | The same `/api/...` paths, on that server |
| API | This computer, port 8000. Cloudflare forwards the public hostname to it. | — |

The APK remembers a server address after the first login. If the phone still has an older address, change the Server field to `https://learn.sasanksankaran.in` and sign in again.

## Preprod on this computer

`.env` at the project root:

```bash
CORS_ORIGINS=https://learn.sasanksankaran.in,http://localhost:5173
PUBLIC_BASE_URL=https://learn.sasanksankaran.in
COOKIE_SECURE=false
```

`COOKIE_SECURE` stays false while you still open the local site at `http://localhost:5173`. Leave `http://localhost:5173` in `CORS_ORIGINS` for that local site.

Restart the API after changing `.env`. The local website at `http://localhost:5173` still talks to the API on this computer through the Vite proxy. It does not send those requests to the public hostname.

Rebuild the APK after changing `API_ORIGIN`, then copy `learning-revision.apk` to the phone:

```bash
cd frontend
npm run build:apk
cd android && ./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ../../learning-revision.apk
```

## Production

1. Run the API on this computer: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
2. Build the website with `cd frontend && npm run build`. Serve the `frontend/dist` files at `https://learn.sasanksankaran.in`.
3. Send only paths that start with `/api` to port 8000. Send every other path to `frontend/dist`.
4. In Cloudflare, create a tunnel and a public hostname `learn.sasanksankaran.in` aimed at that local site. The computer opens the tunnel outward. The router does not accept an inbound port.
5. In `.env`, set `COOKIE_SECURE=true` and restart the API. The website login cookie is then HTTPS-only.
6. Install the APK built with `API_ORIGIN` set to `https://learn.sasanksankaran.in`.

To use a different hostname later, change these three places and restart or rebuild:

- `API_ORIGIN` in `frontend/src/api.ts`, then rebuild the APK.
- `PUBLIC_BASE_URL` and `CORS_ORIGINS` in `.env`, then restart the API.
- The Cloudflare hostname, so the new name reaches this same API.
