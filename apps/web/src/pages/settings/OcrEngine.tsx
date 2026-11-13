import { useEffect, useState } from "react";
import { errorText } from "../../lib/errors";
import { Link } from "react-router-dom";
import { api, type OcrEngineReading, type OcrEngineStatus, type OcrExtraction, type OcrExtractionDetail } from "../../api";
import { FIELD_STATUS, lineNotes, trimQty, warningText } from "../../lib/ocr";
import { StatusChip } from "../../ui/primitives";

const NAME: Record<string, string> = { gemini: "Online handwriting reader", tesseract: "Built-in reader", mixed: "Online + built-in reader", document_ai: "Online document reader", fake: "Test reader", disabled: "Off" };
const pct = (a: string | null) => (a === null ? "—" : `${Math.round(Number(a) * 1000) / 10}%`);

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className="num text-base font-semibold text-ink">{children}</dd>
    </div>
  );
}

/**
 * Admin OCR test page: how the reading engine is set up and what it has used today, how each
 * engine has done against lines staff checked, and any one reading in full: the scan, what
 * Gemini answered, the draft lines, and the built-in reader's lines beside them. Read-only;
 * the API key is never shown here (only whether one is set).
 */
export function OcrEngine() {
  const [st, setSt] = useState<OcrEngineStatus | null>(null);
  const [list, setList] = useState<OcrExtraction[]>([]);
  const [id, setId] = useState("");
  const [reading, setReading] = useState<OcrEngineReading | null>(null);
  const [ex, setEx] = useState<OcrExtractionDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.ocrEngine().then(setSt).catch((e) => setError(errorText(e)));
    api.ocrExtractions({ limit: 30 }).then((p) => setList(p.items)).catch(() => setList([]));
  }, []);
  useEffect(() => {
    setReading(null);
    setEx(null);
    if (!id) return;
    let live = true;
    Promise.all([api.ocrEngineReading(id), api.ocrExtraction(id)])
      .then(([r, e]) => {
        if (live) {
          setReading(r);
          setEx(e);
        }
      })
      .catch((e) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [id]);

  if (error && !st) return <p role="alert" className="alert-danger">{error}</p>;
  if (!st) return <p className="text-sm text-ink-muted">Loading…</p>;
  const eng = reading?.engine;
  return (
    <div className="space-y-5">
      <section className="card space-y-4 p-6" aria-labelledby="ocr-engine">
        <h2 id="ocr-engine" className="text-lg font-semibold">Reading engine</h2>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Fact label="Engine">{NAME[st.provider] ?? st.provider}</Fact>
          <Fact label="API key">{st.key_set ? "Set on the server" : "Not set"}</Fact>
          <Fact label="Scans may go to Google">{st.data_consent ? "Yes (owner agreed)" : "No"}</Fact>
          <Fact label="Falls back to">{NAME[st.fallback_provider] ?? st.fallback_provider}</Fact>
          <Fact label="Requests today">{st.used_today} of {st.daily_request_cap}</Fact>
          <Fact label="Left today">{st.remaining_today}</Fact>
          <Fact label="Per minute">{st.used_this_minute} of {st.requests_per_minute}</Fact>
        </dl>
        {st.quota_hit_today && <p className="alert-danger">Google's daily allowance is used up. Scans are read by the built-in reader until it resets (midnight Pacific time).</p>}
        {st.breaker_open_seconds > 0 && <p className="alert-danger">The online reader is paused for {st.breaker_open_seconds} s after repeated failures. Scans are read by the built-in reader meanwhile.</p>}
        <p className="text-sm text-ink-muted">Target: about {st.target_seconds} s a page. One request reads one page; unclear rows may take one more.</p>
      </section>

      <section className="card space-y-3 p-6" aria-labelledby="ocr-acc">
        <h2 id="ocr-acc" className="text-lg font-semibold">Accuracy against checked lines</h2>
        <p className="text-sm text-ink-muted">Over readings staff have reviewed and accepted: a line counts as right when the reviewer confirmed it as read.</p>
        {st.accuracy.length === 0 ? (
          <p className="text-sm text-ink-muted">No accepted readings yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-ink-muted"><th className="py-1">Engine</th><th>Lines checked</th><th>Confirmed as read</th><th>Accuracy</th></tr></thead>
            <tbody>
              {st.accuracy.map((a) => (
                <tr key={a.engine} className="border-t border-line"><td className="py-1.5">{NAME[a.engine] ?? a.engine}</td><td className="num">{a.samples}</td><td className="num">{a.correct}</td><td className="num font-semibold">{pct(a.accuracy)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card space-y-4 p-6" aria-labelledby="ocr-one">
        <h2 id="ocr-one" className="text-lg font-semibold">Look at one reading</h2>
        <div>
          <label className="label" htmlFor="ocr-pick">Reading</label>
          <select id="ocr-pick" className="input" value={id} onChange={(e) => { setError(""); setId(e.target.value); }}>
            <option value="">Choose a scan…</option>
            {list.map((x) => (
              <option key={x.id} value={x.id}>{x.scan_job_no} · {NAME[x.engine?.provider ?? x.provider] ?? x.provider} · {x.status.toLowerCase().replace("_", " ")}</option>
            ))}
          </select>
        </div>
        {error && <p role="alert" className="alert-danger">{error}</p>}
        {reading && ex && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Fact label="Read by">{NAME[eng?.provider ?? reading.provider] ?? reading.provider}</Fact>
              <Fact label="Pages">{reading.page_count ?? "—"}</Fact>
              <Fact label="Seconds">{eng?.seconds ?? "—"}</Fact>
              <Fact label="Lines found">{reading.lines}</Fact>
              <Fact label="Unclear lines">{reading.uncertain}</Fact>
              <Fact label="NO marks">{reading.no_marks}</Fact>
              <Fact label="Crossed-out numbers">{reading.crossed_out}</Fact>
              <Fact label="Reviewer corrections">{reading.corrections}</Fact>
              <Fact label="Requests">{eng?.requests ?? 0}</Fact>
              <Fact label="Tokens">{eng?.total_tokens ?? 0}</Fact>
              <Fact label="Document">{eng?.doc_type ? eng.doc_type.toLowerCase().replace(/_/g, " ") : "—"}</Fact>
            </dl>
            {reading.validation_errors.length > 0 && (
              <ul className="space-y-1" aria-label="Validation errors">
                {reading.validation_errors.map((v) => <li key={v} className="alert-danger">{warningText(v)}</li>)}
              </ul>
            )}
            {reading.warnings.filter((w) => !reading.validation_errors.includes(w)).map((w) => <p key={w} className="rounded-ctl bg-warning/10 px-3 py-2 text-sm">{warningText(w)}</p>)}
            <div className="grid gap-4 lg:grid-cols-3">
              <figure className="space-y-1">
                <figcaption className="text-sm font-semibold">Scan (page 1)</figcaption>
                <img src={api.documentPageUrl(ex.document_id, 1)} alt={`Scan ${ex.scan_job_no}, page 1`} className="w-full rounded-ctl border border-line" />
              </figure>
              <div className="space-y-1">
                <h3 className="text-sm font-semibold">Draft lines ({NAME[eng?.provider ?? reading.provider] ?? reading.provider})</h3>
                <ul className="divide-y divide-line text-sm">
                  {ex.fields.map((f) => (
                    <li key={f.id} className="py-1.5">
                      <span className="font-medium">{f.read_sku ?? f.label}</span> <span className="num">{f.read_qty ? trimQty(f.read_qty) : "?"} {f.read_unit_code ?? ""}</span>{" "}
                      <StatusChip tone={FIELD_STATUS[f.status].tone}>{FIELD_STATUS[f.status].label}</StatusChip>
                      {lineNotes(f.detail).length > 0 && <span className="block text-ink-muted">{lineNotes(f.detail).join(" · ")}</span>}
                    </li>
                  ))}
                  {ex.fields.length === 0 && <li className="py-1.5 text-ink-muted">No lines.</li>}
                </ul>
                <Link className="btn-ghost inline-flex" to={`/scan/review/${ex.id}`}>Open the review screen</Link>
              </div>
              <div className="space-y-1">
                <h3 className="text-sm font-semibold">Built-in reader, same pages</h3>
                <ul className="divide-y divide-line text-sm">
                  {reading.tesseract_lines.map((t, i) => (
                    <li key={`${t.label}-${i}`} className="py-1.5"><span className="font-medium">{t.label}</span> <span className="num">{t.qty ?? "?"}</span> <span className="text-ink-muted">“{t.raw}”</span></li>
                  ))}
                  {reading.tesseract_lines.length === 0 && <li className="py-1.5 text-ink-muted">{eng?.provider === "gemini" || eng?.provider === "mixed" ? "It found no lines." : "Not kept for this reading (it was not read by the online reader)."}</li>}
                </ul>
              </div>
            </div>
            <details>
              <summary className="cursor-pointer text-sm font-semibold">Raw answer (JSON)</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-ctl bg-subtle p-3 text-xs">{JSON.stringify(eng?.pages ?? [], null, 2)}</pre>
            </details>
          </div>
        )}
      </section>

      <section className="card space-y-3 p-6" aria-labelledby="ocr-calls">
        <h2 id="ocr-calls" className="text-lg font-semibold">Latest requests to Google</h2>
        {st.recent_calls.length === 0 ? (
          <p className="text-sm text-ink-muted">None yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-ink-muted"><th className="py-1">When</th><th>Stage</th><th>Page</th><th>Result</th><th>Tokens</th><th>Seconds</th></tr></thead>
            <tbody>
              {st.recent_calls.map((c, i) => (
                <tr key={`${c.at}-${i}`} className="border-t border-line"><td className="py-1.5">{new Date(c.at).toLocaleString()}</td><td>{c.stage}</td><td className="num">{c.page ?? "—"}</td><td>{c.outcome.toLowerCase().replace("_", " ")}{c.http_status && c.http_status !== 200 ? ` (${c.http_status})` : ""}</td><td className="num">{c.total_tokens}</td><td className="num">{c.seconds ?? "—"}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
