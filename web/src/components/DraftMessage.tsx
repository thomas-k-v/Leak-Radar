import { useEffect, useState } from "react";
import { api, useApi } from "../api/client";
import type { Pitch } from "../api/types";
import { Failed, Loading } from "./common";

export function DraftMessage({ pitch_id }: { pitch_id: string }) {
  const { data: p, error, setData } = useApi<Pitch>(`/api/pitches/${pitch_id}`);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (p) setText(p.edited_text ?? p.draft_text);
  }, [p?.pitch_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <Failed what="draft" error={error} />;
  if (!p) return <Loading />;
  const current = p.edited_text ?? p.draft_text;
  const dirty = text !== current;

  async function patch(body: Partial<Pick<Pitch, "status" | "edited_text">>) {
    setSaving(true);
    setSaveError(null);
    try {
      setData(await api<Pitch>(`/api/pitches/${pitch_id}`, { method: "PATCH", body: JSON.stringify(body) }));
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const withEdits = (status: Pitch["status"]) => (dirty ? { status, edited_text: text } : { status });

  return (
    <section className="card draft" aria-label={`Draft ${pitch_id}`}>
      <h3 className="card-title">
        Outreach draft · {p.merchant_name}
        <span className={`pill status-${p.status}`}>{p.status}</span>
        <span className="sub mono" style={{ marginLeft: "auto" }}>{p.pitch_id} · {p.leak_id} · {p.tone}</span>
      </h3>
      <textarea value={text} onChange={(e) => setText(e.target.value)} aria-label="Draft text" disabled={saving} />
      <div className="actions">
        <button className="btn" disabled={!dirty || saving} onClick={() => patch({ edited_text: text })}>Save edits</button>
        <button className="btn btn-good" disabled={saving || (p.status === "approved" && !dirty)} onClick={() => patch(withEdits("approved"))}>
          Approve
        </button>
        <button className="btn" disabled={saving || (p.status === "rejected" && !dirty)} onClick={() => patch(withEdits("rejected"))}>
          Reject
        </button>
        {p.status !== "draft" && (
          <button className="btn btn-ghost" disabled={saving} onClick={() => patch({ status: "draft" })}>Back to draft</button>
        )}
        <span className="note">Approving only records your decision. Nothing is sent.</span>
      </div>
      {saveError && <div className="msg-error" style={{ marginTop: 8 }}>{saveError}</div>}
    </section>
  );
}
