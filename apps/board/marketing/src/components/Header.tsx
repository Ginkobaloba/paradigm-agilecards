interface HeaderProps {
  /**
   * Whether the current route is the primary board route. When true, the
   * structural `[ ▮ ]` modifier renders in gantry-forest beside the wordmark
   * per the brand handoff active-state rules.
   */
  isActive?: boolean;
}

/**
 * Top navbar.
 *
 * The wordmark is rendered as text (Geist Mono Bold, lowercase, tracked at
 * -0.04em), not as an image. The brand handoff reserves the PNG logotypes
 * for favicons and social cards. Active-state lockup adds the `[ ▮ ]`
 * structural modifier in gantry-forest.
 */
export function Header({ isActive = false }: HeaderProps) {
  return (
    <header className="border-b border-gantry-gunmetal/10 bg-gantry-surface/90 backdrop-blur supports-[backdrop-filter]:bg-gantry-surface/70 sticky top-0 z-40">
      <div className="mx-auto max-w-6xl px-6 h-16 flex items-center justify-between">
        <a
          href="/"
          className="group flex items-center gap-2"
          aria-label="Gantry, home"
        >
          <span className="gantry-wordmark text-xl leading-none">gantry</span>
          {isActive ? (
            <span
              className="gantry-wordmark-active text-base leading-none"
              aria-hidden="true"
            >
              [&nbsp;&#9646;&nbsp;]
            </span>
          ) : null}
        </a>

        <nav className="hidden md:flex items-center gap-7 text-sm text-gantry-gunmetal/80">
          <a
            href="#direct-shift"
            className="hover:text-gantry-gunmetal transition-colors"
          >
            How it works
          </a>
          <a
            href="#lifecycle"
            className="hover:text-gantry-gunmetal transition-colors"
          >
            Lifecycle
          </a>
          <a
            href="#works-with"
            className="hover:text-gantry-gunmetal transition-colors"
          >
            Works with
          </a>
          <a
            href="#get-started"
            className="gantry-btn !py-2 !px-4 text-xs"
          >
            Get started
          </a>
        </nav>
      </div>
    </header>
  );
}
