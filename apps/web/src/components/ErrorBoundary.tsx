import { Component, type ErrorInfo, type ReactNode } from "react";
import { apiRequest } from "../api";
import { isChunkError } from "../lib/errors";
import { Icon } from "../ui/Icon";

// Catches a crash while drawing a screen, so one broken screen never leaves a blank window.
// The person sees what happened, that saved work is safe, and what to do next; the crash is sent
// to the server log with a short reference they can quote to support.

const RELOADED_KEY = "pt.chunk-reload";

function newReference(): string {
  const r = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random()).slice(2);
  return `web-${r.replace(/-/g, "").slice(0, 8)}`;
}

/** Best effort: a crash report must never cause another error. */
function report(reference: string, kind: "render" | "chunk", error: Error) {
  void apiRequest("POST", "/client-errors", {
    reference,
    kind,
    path: window.location.pathname.slice(0, 200),
    name: (error.name || "Error").slice(0, 200),
    message: (error.message || "").slice(0, 500),
    stack: (error.stack || "").slice(0, 4000),
  }).catch(() => undefined);
}

/** After an update, reload once to fetch the new screens (not in a loop if it keeps failing). */
function reloadOnceForUpdate(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOADED_KEY) ?? "0");
    if (Date.now() - last < 30_000) return false;
    sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}

type Props = { children: ReactNode; scope: "app" | "page"; resetKey?: string };
type State = { error: Error | null; reference: string };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, reference: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error, reference: newReference() };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const chunk = isChunkError(error);
    if (chunk && navigator.onLine !== false && reloadOnceForUpdate()) return;
    console.error("[screen error]", this.state.reference, error, info.componentStack);
    report(this.state.reference, chunk ? "chunk" : "render", error);
  }

  componentDidUpdate(prev: Props) {
    // Moving to another screen clears the error.
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null, reference: "" });
  }

  render() {
    const { error, reference } = this.state;
    if (!error) return this.props.children;
    const chunk = isChunkError(error);
    const offline = navigator.onLine === false;
    const title = chunk ? "This screen couldn't be loaded" : "This screen had a problem";
    const body = chunk
      ? offline
        ? "You're offline, so this screen couldn't be downloaded. Reconnect, then reload the page."
        : "Part of Pro Trading couldn't be downloaded (an update may have just been installed). Reload the page to get the latest version."
      : "Something unexpected went wrong while showing this screen. Everything you saved before is safe; anything you were typing on this screen may need to be entered again.";
    const panel = (
      <div role="alert" className="grid place-items-center gap-2 px-6 py-12 text-center">
        <span className="grid size-14 place-items-center rounded-full bg-danger/10"><Icon name="alert" size={24} className="text-danger" /></span>
        <h1 className="font-medium text-danger">{title}</h1>
        <p className="max-w-md text-sm text-ink-muted">{body}</p>
        {!chunk && <p className="text-xs text-ink-muted">If it happens again, give support this reference: <span className="font-mono">{reference}</span></p>}
        <div className="mt-2 flex flex-wrap justify-center gap-2">
          {!chunk && <button className="btn-ghost" onClick={() => this.setState({ error: null, reference: "" })}>Try again</button>}
          <button className="btn-primary" onClick={() => window.location.reload()}>Reload page</button>
          {this.props.scope === "page" && <a className="btn-ghost" href="/">Go to Overview</a>}
        </div>
      </div>
    );
    if (this.props.scope === "page") return <div className="card">{panel}</div>;
    return <div className="grid min-h-full place-items-center bg-canvas p-6"><div className="card w-full max-w-lg">{panel}</div></div>;
  }
}
