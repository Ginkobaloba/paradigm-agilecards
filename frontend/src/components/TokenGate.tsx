import { useState } from "react";

import { api, ApiError } from "../lib/api";
import { setToken } from "../lib/auth";

interface Props {
  onAuthed: (token: string) => void;
}

/**
 * Token entry screen. Drew creates a token on the backend via the CLI,
 * pastes it here, hits enter. We validate by hitting /api/columns under
 * the proposed token; if 200 we save it, if 401 we wipe it.
 */
export function TokenGate({ onAuthed }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    if (busy) return;
    const trimmed = value.trim();
    if (trimmed.length === 0) return;

    setBusy(true);
    setError(null);

    // Temporarily save so the api wrapper picks it up.
    setToken(trimmed);
    try {
      await api.listColumns();
      onAuthed(trimmed);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : String(err);
      setError(msg);
      setToken(""); // wipe rejected token
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <form
        onSubmit={submit}
        className="surface w-full max-w-md p-6 flex flex-col gap-4"
      >
        <div>
          <h1 className="text-base font-semibold text-text mb-1">
            agile-cards-board
          </h1>
          <p className="text-xs text-muted">
            Paste a bearer token. Don't have one? Run{" "}
            <code className="font-mono text-text">
              npm run create-token -- --label &lt;name&gt;
            </code>{" "}
            on the backend.
          </p>
        </div>
        <input
          type="password"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          placeholder="paste token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="input w-full"
        />
        {error ? (
          <div className="text-xs text-danger">{error}</div>
        ) : null}
        <button
          type="submit"
          disabled={busy || value.trim().length === 0}
          className="btn btn-primary disabled:opacity-50"
        >
          {busy ? "checking…" : "sign in"}
        </button>
      </form>
    </div>
  );
}
