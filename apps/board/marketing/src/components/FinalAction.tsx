import { useState, type FormEvent } from "react";

/**
 * Final Action block.
 *
 * Per the brand handoff: centered email input, copy "Clear out your
 * administrative logjam." The form is wired to a placeholder submit; replace
 * the action endpoint at deploy time with the real waitlist provider.
 */
export function FinalAction() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email) return;
    // Placeholder. Replace with real provider POST at deploy time.
    // eslint-disable-next-line no-console
    console.info("[gantry] waitlist signup", email);
    setSubmitted(true);
  }

  return (
    <section
      id="get-started"
      className="bg-gantry-gunmetal text-gantry-surface"
    >
      <div className="mx-auto max-w-3xl px-6 py-24 md:py-32 text-center">
        <p className="gantry-eyebrow !text-gantry-surface/70 mb-5">
          Get started
        </p>
        <h2 className="font-display text-3xl md:text-5xl leading-tight">
          Clear out your administrative logjam.
        </h2>
        <p className="mt-5 text-base md:text-lg text-gantry-surface/75 max-w-xl mx-auto leading-relaxed">
          One card at a time, one column at a time. Drop your address and we
          will send you early access the day it is ready.
        </p>

        {submitted ? (
          <p
            className="mt-10 font-mono text-sm uppercase tracking-[0.18em] text-gantry-forest"
            role="status"
          >
            [ &#9646; ] you are on the list
          </p>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="mt-10 max-w-md mx-auto flex flex-col sm:flex-row gap-3"
            aria-label="Join the Gantry waitlist"
          >
            <label htmlFor="waitlist-email" className="sr-only">
              Email address
            </label>
            <input
              id="waitlist-email"
              type="email"
              required
              autoComplete="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="gantry-input flex-1 !bg-gantry-surface/10 !border-gantry-surface/20 !text-gantry-surface placeholder:!text-gantry-surface/40 focus:!border-gantry-forest"
            />
            <button
              type="submit"
              className="gantry-btn !bg-gantry-forest hover:!bg-gantry-sage"
            >
              Join the waitlist
            </button>
          </form>
        )}

        <p className="mt-6 text-xs font-mono uppercase tracking-[0.18em] text-gantry-surface/40">
          no demos. no calendar links. just the link, on launch day.
        </p>
      </div>
    </section>
  );
}
