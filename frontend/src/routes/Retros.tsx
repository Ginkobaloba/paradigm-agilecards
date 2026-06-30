/**
 * Placeholder for the retros view. The backend already has a retros table
 * and routes; the UI just isn't wired yet. v1 will surface a list of
 * retros and a "new retro" composer that snapshots the current state of
 * Done into a summary you can edit.
 */
export function Retros() {
  return (
    <div className="px-5 py-6">
      <div className="surface p-6 max-w-2xl">
        <h2 className="text-sm font-semibold text-text mb-2">Retros</h2>
        <p className="text-xs text-muted leading-relaxed">
          v1 coming soon. The backend already speaks{" "}
          <code className="font-mono">GET/POST /api/retros</code>; this page
          will list past retros, let you start a new one, and snapshot the
          Done column into the summary as a starting point.
        </p>
      </div>
    </div>
  );
}
