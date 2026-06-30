/**
 * The Direct Shift block.
 *
 * Per the brand handoff: a visual comparison of chaotic chat vs. structured
 * board, anchored on the card drop animation (gantry-motion-1.mp4). The video
 * is loaded from /brand-media/ which Vite serves from the public folder.
 */
export function DirectShift() {
  return (
    <section
      id="direct-shift"
      className="border-y border-gantry-gunmetal/10 bg-white"
    >
      <div className="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div className="max-w-2xl">
          <p className="gantry-eyebrow mb-4">The direct shift</p>
          <h2 className="font-display text-3xl md:text-5xl leading-tight text-gantry-gunmetal">
            Stop scrolling chat logs.
            <br />
            <span className="italic font-normal">
              Start watching cards land.
            </span>
          </h2>
          <p className="mt-5 text-base md:text-lg text-gantry-gunmetal/75 leading-relaxed">
            Chat asks you to remember the thread, recall the prompt, and parse
            paragraphs of output. A board asks you to read one column. The work
            is the same, the surface is not.
          </p>
        </div>

        <div className="mt-14 grid md:grid-cols-2 gap-6">
          <ChaoticChat />
          <StructuredBoard />
        </div>
      </div>
    </section>
  );
}

function ChaoticChat() {
  return (
    <div className="gantry-panel p-6 h-full">
      <div className="flex items-center justify-between">
        <p className="gantry-eyebrow">Before</p>
        <span className="font-mono text-[10px] uppercase tracking-wider text-gantry-gunmetal/50">
          chat thread, 1,847 messages
        </span>
      </div>
      <div className="mt-5 space-y-3 max-h-80 overflow-hidden relative">
        {[
          "you: ok now also handle the retry case",
          "agent: which retry case did you mean, the upload retry or the webhook retry",
          "you: webhook. also do the upload one. and check the rate limits while youre in there",
          "agent: rate limits per route or globally",
          "you: per route, but exempt /health",
          "agent: working on it. should i also add the metric you mentioned yesterday",
          "you: yes. and dont break the cron",
          "agent: which cron",
          "you: the nightly one. the OTHER one",
          "agent: understood. starting now. one moment.",
        ].map((line, i) => (
          <div
            key={i}
            className="text-sm text-gantry-gunmetal/70 leading-snug"
          >
            {line}
          </div>
        ))}
        <div className="absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-white to-transparent" />
      </div>
      <p className="mt-5 text-xs font-mono uppercase tracking-wider text-gantry-gunmetal/50">
        the surface forgets, you carry the state
      </p>
    </div>
  );
}

function StructuredBoard() {
  return (
    <div className="gantry-panel-dark p-6 h-full relative overflow-hidden">
      <div className="flex items-center justify-between">
        <p className="gantry-eyebrow !text-gantry-forest">After</p>
        <span className="font-mono text-[10px] uppercase tracking-wider text-gantry-surface/60">
          gantry, 4 columns
        </span>
      </div>

      <div className="mt-5">
        <video
          className="w-full rounded-sm border border-gantry-surface/10"
          src="/brand-media/gantry-motion-1.mp4"
          autoPlay
          muted
          loop
          playsInline
          aria-label="A card dropping into a Gantry column with a snappy, weighted motion."
        />
      </div>

      <p className="mt-5 text-xs font-mono uppercase tracking-wider text-gantry-surface/60">
        the surface holds the state, you read one column
      </p>
    </div>
  );
}
