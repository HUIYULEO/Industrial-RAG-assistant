# Frontend

The Next.js workspace for the design-review assistant. For the system overview, see the [root README](../README.md).

## Structure

| Path | Contents |
|---|---|
| `app/page.tsx` | The whole workspace: sign-in and the five views |
| `app/api/backend/[...path]/route.ts` | Catch-all proxy to the FastAPI backend |
| `lib/api.ts` | Fetch helper, error type, and a Server-Sent Events reader |
| `lib/types.ts` | Shared response types |

## Views

| View | Does |
|---|---|
| Knowledge control | Import a requirement CSV; register, upload, reparse, index, and archive documents |
| Review packages | Freeze a baseline and an indexed document version |
| Evidence chat | Ask within a Review Package or a selected document scope; open citations in a source reader with the original PDF page |
| Review matrix | Choose an analysis strategy, follow per-requirement progress, retry failures, export to Excel |
| How it works | In-app orientation |

## How it talks to the backend

The browser only ever calls `/api/backend/*` on the Next.js server. The route handler forwards the request to `BACKEND_URL` and streams the response back, so the backend needs no CORS configuration and is not exposed to the browser.

The session token returned by `/auth/login` is kept in `localStorage` and sent as a bearer token on every request. A `401` clears the session.

Analysis progress is polled until every item has settled.

## Local development

Run the backend stack with Docker Compose, then:

```powershell
npm install
```

```powershell
npm run dev
```

`BACKEND_URL` defaults to `http://127.0.0.1:8000`.
