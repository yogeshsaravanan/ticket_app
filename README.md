# Support Triage (FastAPI + React)

Paste a support ticket → it's scored for urgency, put in a priority queue, classified into
`billing_status` / `password_reset` / `service_restart`, and the matching (mock) integration
runs. Anything unclear becomes `escalate` with a reason.

## Run it

The backend is managed with [uv](https://docs.astral.sh/uv/) (`backend/pyproject.toml` +
`uv.lock`). Install uv once: `curl -LsSf https://astral.sh/uv/install.sh | sh`
(or `brew install uv` / `pipx install uv`).

Backend and frontend are separate apps. Run each in its own terminal:

```bash
# terminal 1: API
cd backend
uv sync                                  # creates .venv, installs Python 3.12 + locked deps
uv run uvicorn app.main:app --reload     # http://localhost:8000, docs at /docs

# terminal 2: UI
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

The frontend calls the API at `VITE_API_URL` (default `http://localhost:8000`, see
`frontend/.env.example`). The backend allows browser requests from `CORS_ORIGINS`
(default `http://localhost:5173`).

**Deploying:** build the frontend with the real API URL (`VITE_API_URL=https://api.example.com npm run build`)
and host `frontend/dist` anywhere static. Run the backend with
`CORS_ORIGINS=https://app.example.com uv run uvicorn app.main:app --host 0.0.0.0`.

**Tests:** `cd backend && uv run pytest` (23 tests; deprecation warnings fail the run).
`npm run build` type-checks and builds the frontend.

**uv cheatsheet (from `backend/`):**

| | |
|---|---|
| `uv sync --no-dev` | runtime deps only (production) |
| `uv sync --frozen` | install exactly what's in `uv.lock`, no re-resolve (CI) |
| `uv add <pkg>` / `uv add --dev <pkg>` | add a dependency, update the lock |
| `uv lock --upgrade` | bump everything within the version ranges |
| `uv export --no-dev --no-hashes -o requirements.txt` | for tools that need pip format |

Python version is pinned in `backend/.python-version` (3.12); uv downloads it if it's missing.
`requires-python` is `>=3.10`.

The page is one input box and a tickets table. Submit a few tickets quickly and the Status
column shows each ticket's place in line reordering by urgency. Each ticket takes ~2s
(`PROCESS_SECONDS`), so the queue backs up enough to see it.

| env var | default | meaning |
|---|---|---|
| `PROCESS_SECONDS` | 2 | simulated integration latency |
| `AGING_SECONDS` | 30 | a waiting ticket goes up one urgency level per interval (0 = off) |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated frontend URLs allowed to call the API |
| `VITE_API_URL` (frontend) | `http://localhost:8000` | where the UI sends API requests |

## Layout

```
backend/
  app/main.py         FastAPI app factory, routes, CORS, worker thread (lifespan)
  app/models.py       Pydantic request/response models (TicketIn, Ticket, TicketList)
  app/triage.py       classify(), score_urgency(), mock actions, handle_ticket()
  app/queue_store.py  TicketStore: in-memory store + urgency priority queue with aging
  tests/              test_triage.py (logic), test_api.py (HTTP via TestClient)
  pyproject.toml      deps + pytest config (uv); uv.lock pins exact versions
  .python-version     3.12
frontend/src/
  api.ts              typed client, mirrors app/models.py; base URL from VITE_API_URL
  useTickets.ts       polls /api/tickets while anything is pending
  App.tsx             input box + tickets table (the whole UI)
```

API (OpenAPI at `/docs`):
- `POST /api/tickets {"text": ...}` → `202` + Ticket (queued). Blank or over 5000 chars → `422`
- `GET /api/tickets` → `{tickets: Ticket[]}`, newest first
- `GET /api/tickets/{id}` → Ticket or `404`

## Design decisions

**1. Category and urgency are separate axes.** Category decides *what* happens; urgency
decides *when*. "My account was compromised" is **critical** urgency *and* an **escalation**.

**2. Submission is async (202 + polling).** The spec says process by urgency, not arrival.
If `POST` classified and acted inline, tickets would be handled in arrival order and the
priority queue would be decoration. So `POST` only scores urgency and enqueues. One worker
thread (started and stopped in FastAPI's `lifespan`) takes the most urgent pending ticket,
and React polls until everything is done. One worker is deliberate: with N workers and N or
fewer tickets, ordering is invisible.

**3. "Don't guess" is enforced.** A category is acted on only if its score is ≥ 2 **and**
beats the runner-up by ≥ 2. Otherwise it escalates, with *unclear* and *ambiguous* as distinct
reasons. A lone weak keyword ("password", "app") scores 1, so it never triggers an action.

**4. Guardrails run before classification.** "My account was hacked, please reset my
password" matches `password_reset` cleanly, and auto-resetting there is exactly wrong (the
attacker may control the email). Security and legal/chargeback language always escalates.

**5. Urgency with aging.** Queue order is `(effective_level, arrival_seq)`, FIFO within a level.
Waiting tickets are promoted one level per `AGING_SECONDS` so low ones can't starve, but aging
caps at *high*. Processing is non-preemptive: if a low ticket arrives while the worker is idle,
it starts immediately and finishes before anything that arrives after it.

**6. Action failures become escalations.** The mocks never fail, but real integrations will.
`handle_ticket` catches the exception and escalates with the error instead of losing the ticket.

**Why a thread and not `asyncio`?** The mock actions stand in for blocking SDK calls, and
`TicketStore` is a small lock-protected structure shared between request handlers and the
worker. A thread keeps that honest. With async integration clients I'd switch to an
`asyncio.PriorityQueue` and a task.

## Why rules and not an LLM classifier?

No API keys needed, it's deterministic and testable, and it's explainable (every action carries the
phrases that matched, returned in the API response). The weak spot is recall. The seam is `classify()`: same return shape, prompt
an LLM with the three categories plus explicit "escalate if unsure", ask for JSON, and **keep
the guardrails and confidence gate in code**. The model proposes; code decides whether to act.

## What I'd do next

- Persist the queue (Postgres/Redis). A restart loses state, and in-flight tickets aren't retried.
- Idempotency keys on submit; idempotent actions (a double restart is bad).
- SSE or WebSocket instead of polling.
- Log classifications with scores and tune thresholds against real tickets.
- Auth and rate limiting before this faces the internet.

## How I used the AI assistant

Built with Claude as the coding assistant. This started as a stdlib + vanilla JS version,
then was ported to FastAPI + React on request.

1. **Decided before generating.** Async queue vs inline, separate urgency and category, "escalate
   beats guessing" as a hard gate, guardrails first. The assistant writes code fast and is
   less reliable on judgment calls it wasn't asked to make.
2. **Kept the core framework-free.** `triage.py` and `queue_store.py` carried over unchanged
   (one import line), so the port touched only the HTTP and UI layers, and the existing 14
   logic tests proved the behaviour didn't drift.
3. **Tests encode the adversarial cases:** hacked + reset, ambiguous crash-after-payment,
   lone keyword, starvation vs aging (fake clock), action failure, and an API test that
   ordering follows urgency. That test depends on timing, so it was run 15× to confirm it isn't flaky.
4. **Checked it in a real browser.** Headless Chromium drove the built UI: a batch of submissions, a
   validation error surfaced from FastAPI's 422, and a 390px phone layout. That run caught a
   missing favicon 404 in the console; an earlier run caught the history table overflowing on
   mobile.

The failure mode to watch with an assistant is code that looks right but quietly does
something reasonable and wrong, like "classify inline *and* have a queue". Checking
behaviour against the spec was worth more than reading the code.
