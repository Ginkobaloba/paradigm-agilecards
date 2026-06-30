import { Header } from "./components/Header";
import { Hero } from "./components/Hero";
import { DirectShift } from "./components/DirectShift";
import { PipelineLifecycle } from "./components/PipelineLifecycle";
import { FunctionalScenarios } from "./components/FunctionalScenarios";
import { WorksWith } from "./components/WorksWith";
import { FinalAction } from "./components/FinalAction";
import { Footer } from "./components/Footer";

/**
 * Gantry marketing landing.
 *
 * Single page, structured per the brand handoff:
 *   1. Hero
 *   2. The Direct Shift (chaotic chat vs. structured board)
 *   3. Pipeline Lifecycle (Write, Drop, Review)
 *   4. Functional Scenarios (interface panels)
 *   5. Works With (BYO-LLM vendor strip)
 *   6. Final Action (email capture)
 *
 * The Header uses the gantry wordmark in Geist Mono Bold with -0.04em
 * tracking. Because this surface is the landing (i.e. "on the main board
 * route" from the customer's perspective), the active-state [ ▮ ] modifier
 * renders in gantry-forest beside the wordmark.
 */
export function App() {
  return (
    <div className="min-h-screen flex flex-col bg-gantry-surface">
      <Header isActive />
      <main className="flex-1">
        <Hero />
        <DirectShift />
        <PipelineLifecycle />
        <FunctionalScenarios />
        <WorksWith />
        <FinalAction />
      </main>
      <Footer />
    </div>
  );
}
