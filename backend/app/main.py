"""FastAPI app: ticket intake API + one background worker.

    uvicorn app.main:app --reload            # from backend/

POST enqueues and returns 202. The worker thread processes the most urgent
pending ticket; the client polls. See README "Design decisions" for why
submission is async rather than classify-in-the-request.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .models import Ticket, TicketIn, TicketList
from .queue_store import TicketStore
from .triage import handle_ticket, score_urgency


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------
class Worker:
    def __init__(self, store: TicketStore, process_seconds: float):
        self.store = store
        self.process_seconds = process_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="triage-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            ticket = self.store.next_ticket(timeout=0.25)  # wake up to check _stop
            if ticket is None:
                continue
            time.sleep(self.process_seconds)  # simulated integration latency
            try:
                result = handle_ticket(ticket["id"], ticket["text"])
            except Exception as exc:  # never lose a ticket
                result = {"decision": "escalate", "reason": f"Internal error: {exc}"}
            self.store.complete(ticket["id"], result)


# --------------------------------------------------------------------------
# App factory (tests build their own app with no latency)
# --------------------------------------------------------------------------
def create_app(process_seconds: float | None = None,
               aging_seconds: float | None = None) -> FastAPI:
    if process_seconds is None:
        process_seconds = float(os.environ.get("PROCESS_SECONDS", "2"))
    if aging_seconds is None:
        aging_seconds = float(os.environ.get("AGING_SECONDS", "30"))

    store = TicketStore(aging_seconds=aging_seconds)
    worker = Worker(store, process_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Support Triage", lifespan=lifespan)
    app.state.store = store

    # The React app runs on its own origin, so the browser needs CORS.
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.post("/api/tickets", response_model=Ticket, status_code=202)
    def submit_ticket(body: TicketIn) -> dict:
        urgency, why = score_urgency(body.text)
        return store.submit(body.text, urgency, why)

    @app.get("/api/tickets", response_model=TicketList)
    def list_tickets() -> dict:
        return {"tickets": store.list()}

    @app.get("/api/tickets/{ticket_id}", response_model=Ticket)
    def get_ticket(ticket_id: str) -> dict:
        t = store.get(ticket_id)
        if t is None:
            raise HTTPException(404, "ticket not found")
        return t

    return app


app = create_app()
