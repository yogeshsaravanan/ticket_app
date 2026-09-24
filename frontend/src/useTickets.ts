import { useCallback, useEffect, useRef, useState } from "react";
import { listTickets, type Ticket } from "./api";

const POLL_MS = 700;

/** Ticket list that polls while anything is still queued or processing. */
export function useTickets() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [offline, setOffline] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    let pending = false;
    try {
      const next = await listTickets();
      setTickets(next);
      setOffline(false);
      pending = next.some((t) => t.status !== "done");
    } catch {
      setOffline(true);
      pending = true; // keep retrying until the backend is back
    }
    if (pending) timer.current = window.setTimeout(refresh, POLL_MS);
  }, []);

  useEffect(() => {
    refresh();
    return () => window.clearTimeout(timer.current);
  }, [refresh]);

  return { tickets, offline, refresh };
}
