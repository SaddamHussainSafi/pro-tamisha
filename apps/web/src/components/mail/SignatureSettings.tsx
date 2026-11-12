import { useEffect, useId, useRef, useState } from "react";
import { mailError, scrubHtml, type MailAccount } from "../../lib/mail";
import { ACCENTS, defaultFields, toHex, renderSignature, safeColor, SIG_FONTS, sigForDisplay, sigForWire, signatures, TEMPLATES, type SigFields, type TemplateId } from "../../lib/signature";
import { Icon } from "../../ui/Icon";
import { LoadingRows } from "../../ui/primitives";
import { Modal } from "../gmail/PagePreview";
import { FormatBar } from "./Formatting";

/**
 * Settings › Signature, for one mailbox and one person. Pick a template and fill the details,
 * or write a fully custom signature with the same editor as compose. The preview is what the
 * recipient sees; the HTML is tables + inline styles so every mail client shows it the same.
 */
export function SignatureSettings({ open, account, myName, onClose, onSaved }: { open: boolean; account: MailAccount; myName: string; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<SigFields | null>(null);
  const [onNew, setOnNew] = useState(true);
  const [onReply, setOnReply] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const editor = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    setF(null);
    setMsg(null);
    setLoadErr(null);
    signatures.get(account.id).then(
      (s) => {
        const base = defaultFields(myName, account.address);
        const saved = s.fields && Object.keys(s.fields).length ? { ...base, ...s.fields } : base;
        // A signature saved elsewhere (e.g. the mailbox default) without our fields opens as custom.
        if (!(s.fields && Object.keys(s.fields).length) && s.html.trim()) Object.assign(saved, { mode: "custom", customHtml: sigForWire(sigForDisplay(s.html)) });
        setF(saved);
        setOnNew(s.use_on_new);
        setOnReply(s.use_on_reply);
      },
      (err: unknown) => setLoadErr(mailError(err)),
    );
  }, [open, account.id, account.address, myName]);

  const set = (patch: Partial<SigFields>) => setF((x) => (x ? { ...x, ...patch } : x));
  // Custom mode: the editor owns its HTML (seeded from the chosen template the first time).
  useEffect(() => {
    if (!f || f.mode !== "custom" || !editor.current) return;
    if (!editor.current.innerHTML) editor.current.innerHTML = scrubHtml(sigForDisplay(f.customHtml || renderSignature({ ...f, mode: "template" })));
  }, [f]);

  async function save() {
    if (!f) return;
    setBusy(true);
    setMsg(null);
    const fields = f.mode === "custom" ? { ...f, customHtml: sigForWire(editor.current?.innerHTML ?? f.customHtml) } : f;
    try {
      await signatures.put(account.id, { html: sigForWire(renderSignature(fields)), fields, use_on_new: onNew, use_on_reply: onReply });
      setF(fields);
      setMsg({ ok: true, text: "Signature saved." });
      onSaved();
    } catch (err) {
      setMsg({ ok: false, text: mailError(err) });
    } finally {
      setBusy(false);
    }
  }

  const preview = f ? scrubHtml(sigForDisplay(f.mode === "custom" ? (editor.current?.innerHTML ?? f.customHtml) : renderSignature(f))) : "";
  const field = (key: keyof SigFields, label: string, opts: { type?: string; placeholder?: string; wide?: boolean } = {}) =>
    f && (
      <label className={`text-sm ${opts.wide ? "sm:col-span-2" : ""}`}>
        <span className="mb-1 block text-xs text-ink-muted">{label}</span>
        <input type={opts.type ?? "text"} className="input py-2" value={String(f[key] ?? "")} placeholder={opts.placeholder} onChange={(e) => set({ [key]: e.target.value } as Partial<SigFields>)} />
      </label>
    );

  return (
    <Modal open={open} title={`Signature · ${account.label}`} onClose={onClose} wide>
      {loadErr ? (
        <p role="alert" className="alert-danger m-5">
          {loadErr}
        </p>
      ) : !f ? (
        <LoadingRows rows={5} cols={2} />
      ) : (
        <div className="grid h-full min-h-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-h-0 space-y-5 overflow-y-auto border-line p-5 lg:border-r">
            <div role="radiogroup" aria-label="Style" className="space-y-2">
              <p className="text-sm font-semibold">Style</p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {TEMPLATES.map((t) => (
                  <TemplateCard key={t.id} id={t.id} label={t.label} hint={t.hint} on={f.mode === "template" && f.template === t.id} onPick={() => set({ mode: "template", template: t.id })} />
                ))}
                <button
                  type="button"
                  role="radio"
                  aria-checked={f.mode === "custom"}
                  onClick={() => {
                    if (editor.current) editor.current.innerHTML = "";
                    set({ mode: "custom", customHtml: f.customHtml || sigForWire(renderSignature({ ...f, mode: "template" })) });
                  }}
                  className={`flex cursor-pointer flex-col items-start gap-1 rounded-xl border p-2.5 text-left ${f.mode === "custom" ? "border-brand-primary bg-brand-accent/10" : "border-line hover:bg-subtle"}`}
                >
                  <Icon name="pencil" size={18} className="text-ink-muted" />
                  <span className="text-sm font-medium">Custom</span>
                  <span className="text-[11px] leading-tight text-ink-muted">Write it yourself</span>
                </button>
              </div>
            </div>

            {f.mode === "custom" ? (
              <div className="space-y-2">
                <p className="text-sm font-semibold">Your signature</p>
                <div className="overflow-hidden rounded-xl border border-line">
                  <div className="py-1">
                    <FormatBar
                      exec={(cmd, arg) => {
                        editor.current?.focus();
                        document.execCommand("styleWithCSS", false, "true");
                        document.execCommand(cmd, false, arg);
                        set({ customHtml: sigForWire(editor.current?.innerHTML ?? "") });
                      }}
                    />
                  </div>
                  <div
                    ref={editor}
                    role="textbox"
                    aria-multiline="true"
                    aria-label="Signature text"
                    contentEditable
                    suppressContentEditableWarning
                    className="min-h-40 p-3 text-sm outline-none"
                    onInput={(e) => set({ customHtml: sigForWire(e.currentTarget.innerHTML) })}
                    onPaste={(e) => {
                      e.preventDefault();
                      document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
                    }}
                  />
                </div>
                <button
                  type="button"
                  className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted hover:text-ink"
                  onClick={() => {
                    if (editor.current) editor.current.innerHTML = `<img src="/api/v1/mail/signature-logo" width="${f.logoSize}" alt="Tamisha Trading">` + editor.current.innerHTML;
                    set({ customHtml: sigForWire(editor.current?.innerHTML ?? "") });
                  }}
                >
                  <Icon name="image" size={14} />
                  Insert company logo
                </button>
              </div>
            ) : (
              <>
                <fieldset className="grid gap-3 sm:grid-cols-2">
                  <legend className="mb-2 text-sm font-semibold">Details</legend>
                  {field("name", "Full name")}
                  {field("title", "Job title", { placeholder: "e.g. Sales Manager" })}
                  {field("department", "Department", { placeholder: "e.g. Sales" })}
                  {field("company", "Company")}
                  {field("phone", "Phone", { type: "tel" })}
                  {field("mobile", "Mobile", { type: "tel" })}
                  {field("email", "Email", { type: "email" })}
                  {field("website", "Website")}
                  {field("address", "Address", { wide: true })}
                </fieldset>
                <details className="group">
                  <summary className="cursor-pointer text-sm font-semibold">Social links (optional)</summary>
                  <div className="mt-3 grid gap-3 sm:grid-cols-3">
                    {field("linkedin", "LinkedIn")}
                    {field("facebook", "Facebook")}
                    {field("instagram", "Instagram")}
                  </div>
                </details>
                <fieldset className="grid gap-3 sm:grid-cols-2">
                  <legend className="mb-2 text-sm font-semibold">Look</legend>
                  <div className="space-y-1.5 sm:col-span-2">
                    <span className="block text-xs text-ink-muted">Accent colour</span>
                    <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Accent colour">
                      {ACCENTS.map((a) => (
                        <button key={a.value} type="button" role="radio" aria-checked={f.accent === a.value} aria-label={a.label} title={a.label} onClick={() => set({ accent: a.value })} className={`size-7 cursor-pointer rounded-full ring-offset-2 ${f.accent === a.value ? "ring-2 ring-ink" : ""}`} style={{ background: a.value }} />
                      ))}
                      <label className="ml-1 inline-flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
                        <input type="color" className="size-7 cursor-pointer rounded-full border-0 bg-transparent p-0" value={toHex(f.accent)} onChange={(e) => set({ accent: safeColor(e.target.value) })} aria-label="Custom accent colour" />
                        Custom
                      </label>
                    </div>
                  </div>
                  <label className="text-sm">
                    <span className="mb-1 block text-xs text-ink-muted">Font</span>
                    <select className="input py-2" value={f.font} onChange={(e) => set({ font: e.target.value })}>
                      {SIG_FONTS.map((x) => (
                        <option key={x.css} value={x.css}>
                          {x.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-xs text-ink-muted">Text size: {f.fontSize}px</span>
                    <input type="range" min={11} max={16} className="w-full accent-brand-primary" value={f.fontSize} onChange={(e) => set({ fontSize: Number(e.target.value) })} />
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" className="size-4 accent-brand-primary" checked={f.showLogo} onChange={(e) => set({ showLogo: e.target.checked })} />
                    Show the company logo
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-xs text-ink-muted">Logo size: {f.logoSize}px</span>
                    <input type="range" min={40} max={160} step={4} disabled={!f.showLogo} className="w-full accent-brand-primary" value={f.logoSize} onChange={(e) => set({ logoSize: Number(e.target.value) })} />
                  </label>
                  {field("disclaimer", "Disclaimer line (optional)", { wide: true, placeholder: "e.g. This email and any files are confidential…" })}
                </fieldset>
              </>
            )}
            <fieldset className="space-y-2">
              <legend className="mb-1 text-sm font-semibold">Use it</legend>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="size-4 accent-brand-primary" checked={onNew} onChange={(e) => setOnNew(e.target.checked)} />
                Insert on new emails
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="size-4 accent-brand-primary" checked={onReply} onChange={(e) => setOnReply(e.target.checked)} />
                Insert on replies and forwards
              </label>
            </fieldset>
          </div>
          <div className="flex min-h-0 flex-col bg-subtle">
            <p className="px-5 pt-4 text-xs font-medium text-ink-muted">Preview · as the recipient sees it</p>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              <div className="rounded-xl bg-card p-5 shadow-[0_1px_2px_rgb(24_24_27/0.06)]">
                <p className="text-sm text-ink-muted">Hi Maria,</p>
                <p className="pb-3 pt-2 text-sm text-ink-muted">The order is confirmed for Tuesday.</p>
                <p className="text-sm text-ink-muted">--</p>
                {/* Our own template output, cleaned by the same rules as compose (no scripts, no remote pictures). */}
                <div aria-label="Signature preview" role="region" className="overflow-x-auto pt-1" dangerouslySetInnerHTML={{ __html: preview }} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2.5 border-t border-line bg-card px-5 py-3">
              {msg && (
                <p role={msg.ok ? "status" : "alert"} className={`min-w-0 flex-1 text-sm ${msg.ok ? "text-success" : "text-danger"}`}>
                  {msg.text}
                </p>
              )}
              <span className="ml-auto" />
              <button type="button" className="btn-ghost" onClick={onClose}>
                Close
              </button>
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void save()} id={`${id}-save`}>
                {busy ? "Saving…" : "Save signature"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

function TemplateCard({ id, label, hint, on, onPick }: { id: TemplateId; label: string; hint: string; on: boolean; onPick: () => void }) {
  // A tiny wireframe of each layout.
  const bar = "h-1 rounded-full bg-ink/25";
  const logo = <span className="size-4 shrink-0 rounded-sm bg-brand-accent/60" />;
  const shape = {
    classic: (
      <div className="flex items-center gap-1.5">
        {logo}
        <span className="h-6 w-px bg-brand-primary" />
        <div className="flex-1 space-y-1">
          <div className={`${bar} w-3/4 bg-ink/50`} />
          <div className={`${bar} w-full`} />
        </div>
      </div>
    ),
    modern: (
      <div className="flex gap-1.5">
        <span className="w-1 rounded bg-brand-primary" />
        <div className="flex-1 space-y-1">
          <div className={`${bar} w-3/4 bg-ink/50`} />
          <div className={`${bar} w-full`} />
          <div className={`${bar} w-2/3`} />
        </div>
      </div>
    ),
    minimal: (
      <div className="space-y-1">
        <div className={`${bar} w-1/2 bg-ink/50`} />
        <div className={`${bar} w-full`} />
        <div className={`${bar} w-3/4`} />
      </div>
    ),
    compact: (
      <div className="space-y-1 pt-1.5">
        <div className={`${bar} w-full bg-ink/40`} />
        <div className={`${bar} w-4/5`} />
      </div>
    ),
    centered: (
      <div className="flex flex-col items-center gap-1 border-t-2 border-brand-primary pt-1">
        {logo}
        <div className={`${bar} w-1/2 bg-ink/50`} />
      </div>
    ),
    logotop: (
      <div className="space-y-1">
        {logo}
        <div className={`${bar} w-1/2 bg-ink/50`} />
        <div className={`${bar} w-3/4`} />
      </div>
    ),
  }[id];
  return (
    <button type="button" role="radio" aria-checked={on} onClick={onPick} title={hint} className={`flex cursor-pointer flex-col gap-1.5 rounded-xl border p-2.5 text-left ${on ? "border-brand-primary bg-brand-accent/10" : "border-line hover:bg-subtle"}`}>
      <div className="h-9 w-full">{shape}</div>
      <span className="text-sm font-medium">{label}</span>
    </button>
  );
}
