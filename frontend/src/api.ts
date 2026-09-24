const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Mirrors the Pydantic `Ticket` model in backend/app/models.py.
export type Urgency = "critical" | "high" | "normal" | "low";
export type Category = "billing_status" | "password_reset" | "service_restart";

export interface Ticket {
  id: string;
  text: string;
  urgency: Urgency;
  urgency_reason: string;
  effective_urgency: Urgency;
  status: "queued" | "processing" | "done";
  queue_position: number | null;
  processed_order: number | null;
  submitted_at: number;
  started_at: number | null;
  completed_at: number | null;
  decision: "action" | "escalate" | null;
  category: Category | null;
  reason: string | null;
  action: string | null;
  action_result: string | null;
  scores: Record<string, number> | null;
  matched: Record<string, string[]> | null;
}

async function errorMessage(r: Response): Promise<string> {
  try {
    const body = await r.json();
    // FastAPI: {detail: "msg"} or {detail: [{msg: "..."}]} for validation errors
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      return String(body.detail[0].msg).replace(/^Value error, /, "");
    }
  } catch {
    /* not JSON */
  }
  return `${r.status} ${r.statusText}`;
}

export async function submitTicket(text: string): Promise<Ticket> {
  const r = await fetch(`${API_URL}/api/tickets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) throw new Error(await errorMessage(r));
  return r.json();
}

export async function listTickets(): Promise<Ticket[]> {
  const r = await fetch(`${API_URL}/api/tickets`);
  if (!r.ok) throw new Error(await errorMessage(r));
  return (await r.json()).tickets;
}
