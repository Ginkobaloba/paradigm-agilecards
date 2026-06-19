/**
 * Footer.
 *
 * Minimal, structural. The wordmark sits flush left and pairs the inactive
 * lockup (no [ ▮ ] modifier) since the footer is not a navigation surface.
 */
export function Footer() {
  return (
    <footer className="border-t border-gantry-gunmetal/10 bg-gantry-surface">
      <div className="mx-auto max-w-6xl px-6 py-10 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="flex items-center gap-3">
          <span className="gantry-wordmark text-lg leading-none">gantry</span>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-gantry-gunmetal/55">
            workflow surface, not a chatbot
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-6 text-xs font-mono uppercase tracking-wider text-gantry-gunmetal/60">
          <a href="#direct-shift" className="hover:text-gantry-gunmetal">
            How it works
          </a>
          <a href="#lifecycle" className="hover:text-gantry-gunmetal">
            Lifecycle
          </a>
          <a href="#works-with" className="hover:text-gantry-gunmetal">
            Works with
          </a>
          <span className="text-gantry-gunmetal/40">
            &copy; {new Date().getFullYear()} Gantry
          </span>
        </div>
      </div>
    </footer>
  );
}
