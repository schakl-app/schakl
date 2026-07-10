/**
 * The leave-request body the API accepts (#48), shared by the two form actions that post one.
 *
 * `hours` is deliberately absent. The server computes it from the employee's schedule minus
 * weekends, holidays and breaks; a client that could post it is a client the balance cannot
 * trust, and that is the whole reason the calculation moved.
 */
export interface LeaveRequestBody {
  leave_type_id: string;
  start_date: string;
  /** Null on a whole-day request — which is what most of them are. */
  start_time: string | null;
  end_date: string;
  end_time: string | null;
  note: string | null;
  /**
   * Present only when the form actually offered the field. A member never posts it (the API
   * would 403), and a manager who unticks the box posts `null` to **clear** a stored override
   * rather than silently leaving it on a span whose dates just changed.
   */
  hours_override?: number | null;
}

export function requestBody(form: FormData): LeaveRequestBody {
  const body: LeaveRequestBody = {
    leave_type_id: String(form.get("leave_type_id") ?? ""),
    start_date: String(form.get("start_date") ?? ""),
    start_time: String(form.get("start_time") ?? "").trim() || null,
    end_date: String(form.get("end_date") ?? ""),
    end_time: String(form.get("end_time") ?? "").trim() || null,
    note: String(form.get("note") ?? "").trim() || null,
  };
  if (form.has("override_offered")) {
    const raw = String(form.get("hours_override") ?? "").trim();
    body.hours_override = raw ? Number(raw) : null;
  }
  return body;
}
