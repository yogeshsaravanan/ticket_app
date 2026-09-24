import { useState, type FormEvent } from "react";
import { submitTicket, type Ticket } from "./api";
import { useTickets } from "./useTickets";

function statusText(t: Ticket): string {
  if (t.status === "queued") return `Queued (#${t.queue_position} in line)`;
  if (t.status === "processing") return "Processing…";
  return "Done";
}

function Result({ t }: { t: Ticket }) {
  if (t.status !== "done") return <>–</>;
  if (t.decision === "action") return <>{t.action_result}</>;
  return <>Escalated: {t.reason}</>;
}

export default function App() {
  const { tickets, refresh } = useTickets();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      await submitTicket(text);
      setText("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Support Tickets</h1>

      <form onSubmit={onSubmit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste a support ticket…"
          rows={4}
        />
        <button type="submit" disabled={busy || !text.trim()}>Submit</button>
        {error && <p className="error">{error}</p>}
      </form>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Ticket</th>
            <th>Urgency</th>
            <th>Status</th>
            <th>Category</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {tickets.length === 0 && (
            <tr><td colSpan={6} className="empty">No tickets yet.</td></tr>
          )}
          {tickets.map((t) => (
            <tr key={t.id}>
              <td>{t.id}</td>
              <td>{t.text}</td>
              <td>{t.urgency}</td>
              <td>{statusText(t)}</td>
              <td>{t.status === "done" ? (t.category ?? "escalate") : "–"}</td>
              <td><Result t={t} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
