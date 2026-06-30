/**
 * Hero block.
 *
 * Headline is verbatim from the brand handoff. Fraunces for the headline,
 * Geist Sans for the supporting copy.
 *
 * The visual is a static interface panel built from brand primitives. The
 * brand handoff calls for a video here (gantry-motion-1.mp4 plays the
 * card drop in the next section); the static panel keeps the hero LCP
 * fast and serves as a graceful fallback if motion is disabled.
 */
export function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div className="absolute inset-0 gantry-grid-bg opacity-60 pointer-events-none" />
      <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-24 md:pt-28 md:pb-32 grid md:grid-cols-2 gap-12 items-center">
        <div>
          <p className="gantry-eyebrow mb-5">
            <span aria-hidden="true">[&nbsp;&#9646;&nbsp;]</span>
            <span className="ml-2">The workflow surface for AI agents</span>
          </p>
          <h1 className="font-display text-4xl md:text-6xl leading-[1.05] tracking-tight text-gantry-gunmetal">
            Manage the outcome.
            <br />
            <span className="italic font-normal text-gantry-gunmetal/85">
              Leave the process to us.
            </span>
          </h1>
          <p className="mt-6 text-lg md:text-xl text-gantry-gunmetal/75 max-w-xl leading-relaxed">
            Gantry is the workflow surface for the AI you already trust. Bring
            your own model, drop a card, and let your agents do the work.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <a href="#get-started" className="gantry-btn">
              Start your pipeline
            </a>
            <a
              href="#direct-shift"
              className="text-sm font-medium text-gantry-gunmetal/80 hover:text-gantry-gunmetal underline underline-offset-4"
            >
              See how it works
            </a>
          </div>
        </div>

        <div className="relative">
          <HeroPanel />
        </div>
      </div>
    </section>
  );
}

/**
 * A static interface panel that mirrors the live board: header strip, three
 * kanban columns, a highlighted card, and fine connection paths to the
 * orchestration engine. Pure CSS so it scales cleanly and ships zero JS
 * weight beyond React.
 */
function HeroPanel() {
  return (
    <div className="gantry-panel-dark shadow-2xl shadow-gantry-gunmetal/20 p-5 md:p-6 rounded-sm">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-gantry-surface/60 font-mono">
        <div className="flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 bg-gantry-forest rounded-full" />
          <span>orchestration engine, live</span>
        </div>
        <span>gantry / kanban</span>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-3">
        <Column title="WRITE" tint="surface" />
        <Column title="DROP" tint="forest" highlightIndex={1} />
        <Column title="REVIEW" tint="surface" />
      </div>

      <div className="mt-5 flex items-center justify-between text-[10px] font-mono text-gantry-surface/60">
        <span>17 cards in flight</span>
        <span>p50 lead time 42m</span>
      </div>
    </div>
  );
}

interface ColumnProps {
  title: string;
  tint: "surface" | "forest";
  highlightIndex?: number;
}

function Column({ title, tint, highlightIndex }: ColumnProps) {
  return (
    <div className="bg-gantry-gunmetal/60 border border-gantry-surface/10 rounded-sm p-3">
      <div className="text-[9px] font-mono uppercase tracking-[0.18em] text-gantry-surface/70 mb-3">
        {title}
      </div>
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <Card
            key={i}
            highlighted={highlightIndex === i}
            tint={tint}
            idx={i}
          />
        ))}
      </div>
    </div>
  );
}

interface CardProps {
  highlighted: boolean;
  tint: "surface" | "forest";
  idx: number;
}

function Card({ highlighted, tint, idx }: CardProps) {
  const base = "rounded-sm border p-2.5 transition-colors";
  const palette = highlighted
    ? "bg-gantry-forest/90 border-gantry-forest text-gantry-surface shadow-lg shadow-gantry-forest/30"
    : tint === "forest"
      ? "bg-gantry-gunmetal/80 border-gantry-surface/10 text-gantry-surface/85"
      : "bg-gantry-gunmetal/80 border-gantry-surface/10 text-gantry-surface/75";
  return (
    <div className={`${base} ${palette}`}>
      <div className="flex items-center justify-between text-[9px] font-mono uppercase tracking-wider opacity-80">
        <span>CARD-{(idx + 7).toString().padStart(3, "0")}</span>
        <span>{highlighted ? "active" : "ready"}</span>
      </div>
      <div className="mt-1.5 text-[11px] leading-snug">
        {idx === 0
          ? "Draft change-log entry for v2.3"
          : idx === 1
            ? "Reconcile invoice batch / Q2"
            : "Triage inbound bug reports"}
      </div>
    </div>
  );
}
