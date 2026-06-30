/**
 * Pipeline Lifecycle block.
 *
 * Per the brand handoff: "Write, Drop, and Review." Three card mockups paired
 * with the brand blueprint motion (gantry-motion-2.mp4) as the visual anchor.
 */

const STAGES = [
  {
    name: "Write",
    blurb:
      "Capture the outcome in a card. One sentence, optional acceptance lines, optional artifact links. No prompt theater.",
    sample: "Add invoice batch reconciler for Q2",
  },
  {
    name: "Drop",
    blurb:
      "Hand the card to an agent. The orchestration engine schedules, retries, and routes; you do not.",
    sample: "claude-sonnet, picked up at 09:42",
  },
  {
    name: "Review",
    blurb:
      "Read the diff, not the chat log. Approve, request changes, or escalate. The board records the trail automatically.",
    sample: "47 lines, 1 reviewer pending",
  },
] as const;

export function PipelineLifecycle() {
  return (
    <section id="lifecycle" className="bg-gantry-surface">
      <div className="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div className="max-w-2xl">
          <p className="gantry-eyebrow mb-4">Pipeline lifecycle</p>
          <h2 className="font-display text-3xl md:text-5xl leading-tight text-gantry-gunmetal">
            Write, drop, and review.
          </h2>
          <p className="mt-5 text-base md:text-lg text-gantry-gunmetal/75 leading-relaxed">
            Three motions, the same shape every time. The board is the contract
            between you and the agent: a card in, a result out, a record of how
            it got there.
          </p>
        </div>

        <div className="mt-14 grid md:grid-cols-5 gap-6 items-stretch">
          <div className="md:col-span-2">
            <BlueprintVideo />
          </div>
          <div className="md:col-span-3 grid gap-4">
            {STAGES.map((s, i) => (
              <StageCard key={s.name} index={i} {...s} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function BlueprintVideo() {
  return (
    <div className="gantry-panel h-full p-5 flex flex-col">
      <p className="gantry-eyebrow">Blueprint</p>
      <div className="mt-4 flex-1 flex items-center justify-center bg-gantry-surface rounded-sm border border-gantry-gunmetal/10 overflow-hidden">
        <video
          className="w-full h-auto"
          src="/brand-media/gantry-motion-2.mp4"
          autoPlay
          muted
          loop
          playsInline
          aria-label="A line-based isometric blueprint of the Gantry pipeline."
        />
      </div>
      <p className="mt-4 text-xs font-mono uppercase tracking-wider text-gantry-gunmetal/60">
        nodes connect via structural paths, not chat
      </p>
    </div>
  );
}

interface StageProps {
  index: number;
  name: string;
  blurb: string;
  sample: string;
}

function StageCard({ index, name, blurb, sample }: StageProps) {
  return (
    <div className="gantry-panel p-5 flex gap-5 items-start">
      <div className="font-mono text-xs font-bold text-gantry-forest pt-1">
        0{index + 1}
      </div>
      <div className="flex-1">
        <div className="flex items-center gap-3">
          <h3 className="font-sans text-lg font-semibold text-gantry-gunmetal">
            {name}
          </h3>
          <span className="font-mono text-[10px] uppercase tracking-wider text-gantry-gunmetal/50">
            stage 0{index + 1}
          </span>
        </div>
        <p className="mt-2 text-sm text-gantry-gunmetal/75 leading-relaxed">
          {blurb}
        </p>
        <div className="mt-3 inline-flex items-center gap-2 bg-gantry-surface border border-gantry-gunmetal/10 rounded-sm px-3 py-1.5 font-mono text-xs text-gantry-gunmetal/80">
          <span
            className="inline-block w-1.5 h-1.5 bg-gantry-forest rounded-full"
            aria-hidden="true"
          />
          {sample}
        </div>
      </div>
    </div>
  );
}
