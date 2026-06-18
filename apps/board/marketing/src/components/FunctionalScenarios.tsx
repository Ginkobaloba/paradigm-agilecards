/**
 * Functional Scenarios block.
 *
 * Per the brand handoff: 2-3 high-contrast interface panels showing real cards.
 * We use three to cover the most common Gantry-shaped jobs: engineering work,
 * back-office reconciliation, and inbound triage.
 */

const SCENARIOS = [
  {
    eyebrow: "Scenario / engineering",
    title: "Ship the fix the right way.",
    description:
      "A bug report comes in. You write the card, drop it into the queue, and review the diff. The agent runs the test suite, opens the PR, and pages a human if anything looks off.",
    columns: [
      {
        name: "BACKLOG",
        cards: [
          "Retry logic for webhook delivery",
          "Rate limit /api/v1/upload",
          "Audit nightly cron failures",
        ],
      },
      {
        name: "IN FLIGHT",
        cards: ["Retry logic for webhook delivery"],
        accent: 0,
      },
      {
        name: "REVIEW",
        cards: ["Patch v2.3.1 ready, 47 lines"],
      },
    ],
  },
  {
    eyebrow: "Scenario / back office",
    title: "Reconcile a quarter in an afternoon.",
    description:
      "A spreadsheet of invoices and a folder of receipts go in. Matched pairs come out. Mismatches are flagged with a one-line explanation and the receipt rendered inline. No more tab roulette.",
    columns: [
      {
        name: "INBOX",
        cards: [
          "Q2 invoice batch / 412 line items",
          "AP statement / Acme Corp",
        ],
      },
      {
        name: "MATCHING",
        cards: ["Q2 invoice batch / 287 matched, 12 flagged"],
        accent: 0,
      },
      {
        name: "FOR REVIEW",
        cards: ["12 mismatches, 1 likely duplicate"],
      },
    ],
  },
] as const;

export function FunctionalScenarios() {
  return (
    <section className="bg-white border-t border-gantry-gunmetal/10">
      <div className="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div className="max-w-2xl">
          <p className="gantry-eyebrow mb-4">In the wild</p>
          <h2 className="font-display text-3xl md:text-5xl leading-tight text-gantry-gunmetal">
            The same surface,
            <br />
            <span className="italic font-normal">different jobs.</span>
          </h2>
          <p className="mt-5 text-base md:text-lg text-gantry-gunmetal/75 leading-relaxed">
            Cards are stage-agnostic. A line of code, a stack of receipts, a
            queue of support tickets. The board does not care what shape the
            work takes, only that it lands.
          </p>
        </div>

        <div className="mt-14 space-y-10">
          {SCENARIOS.map((s) => (
            <ScenarioPanel key={s.title} {...s} />
          ))}
        </div>
      </div>
    </section>
  );
}

interface ScenarioPanelProps {
  eyebrow: string;
  title: string;
  description: string;
  columns: ReadonlyArray<{
    name: string;
    cards: readonly string[];
    accent?: number;
  }>;
}

function ScenarioPanel({
  eyebrow,
  title,
  description,
  columns,
}: ScenarioPanelProps) {
  return (
    <div className="grid md:grid-cols-5 gap-6 items-start">
      <div className="md:col-span-2">
        <p className="gantry-eyebrow mb-3">{eyebrow}</p>
        <h3 className="font-display text-2xl md:text-3xl text-gantry-gunmetal leading-tight">
          {title}
        </h3>
        <p className="mt-4 text-sm md:text-base text-gantry-gunmetal/75 leading-relaxed">
          {description}
        </p>
      </div>

      <div className="md:col-span-3 gantry-panel-dark p-5 md:p-6 rounded-sm">
        <div className="grid grid-cols-3 gap-3">
          {columns.map((c) => (
            <div
              key={c.name}
              className="bg-gantry-gunmetal/60 border border-gantry-surface/10 rounded-sm p-3"
            >
              <div className="text-[9px] font-mono uppercase tracking-[0.18em] text-gantry-surface/70 mb-3">
                {c.name}
              </div>
              <div className="space-y-2">
                {c.cards.map((text, i) => {
                  const active = c.accent === i;
                  const cls = active
                    ? "bg-gantry-forest/90 border-gantry-forest text-gantry-surface shadow-lg shadow-gantry-forest/30"
                    : "bg-gantry-gunmetal/80 border-gantry-surface/10 text-gantry-surface/85";
                  return (
                    <div
                      key={i}
                      className={`rounded-sm border p-2.5 text-[11px] leading-snug ${cls}`}
                    >
                      {text}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
