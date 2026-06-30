/**
 * Works With block.
 *
 * The BYO-LLM tenet made flesh. Four neutral tiles: Anthropic Claude, OpenAI
 * GPT, Google Gemini, and a "self-host" tile that closes the door on vendor
 * lock-in. Logos are text placeholders for now; the brand handoff leaves this
 * decision open and a typographic treatment is on-brand for tech casual.
 */

const VENDORS = [
  { name: "Anthropic Claude", note: "claude-3.5-sonnet, opus" },
  { name: "OpenAI GPT", note: "gpt-4o, o1" },
  { name: "Google Gemini", note: "gemini-1.5-pro" },
  { name: "Self host", note: "ollama, vllm, your own" },
] as const;

export function WorksWith() {
  return (
    <section
      id="works-with"
      className="bg-gantry-surface border-t border-gantry-gunmetal/10"
    >
      <div className="mx-auto max-w-6xl px-6 py-20 md:py-24">
        <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-6">
          <div className="max-w-xl">
            <p className="gantry-eyebrow mb-4">Works with</p>
            <h2 className="font-display text-3xl md:text-4xl leading-tight text-gantry-gunmetal">
              Bring the model
              <br />
              <span className="italic font-normal">you already trust.</span>
            </h2>
          </div>
          <p className="text-sm md:text-base text-gantry-gunmetal/70 max-w-md leading-relaxed">
            Gantry is the surface, not the brain. Point it at the API key you
            already pay for, or run it against a model on your own hardware.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-2 md:grid-cols-4 gap-3">
          {VENDORS.map((v) => (
            <div
              key={v.name}
              className="gantry-panel px-5 py-6 flex flex-col items-start gap-2 hover:border-gantry-forest/40 transition-colors"
            >
              <div className="font-sans text-base font-semibold text-gantry-gunmetal">
                {v.name}
              </div>
              <div className="font-mono text-[11px] uppercase tracking-wider text-gantry-gunmetal/55">
                {v.note}
              </div>
            </div>
          ))}
        </div>

        <p className="mt-8 text-xs font-mono uppercase tracking-[0.18em] text-gantry-gunmetal/55">
          one surface, any model
        </p>
      </div>
    </section>
  );
}
