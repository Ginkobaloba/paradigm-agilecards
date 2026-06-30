# Definition of Done

Project-wide quality bar for the Paradigm Coding Solutions program. The
locked plan is `C:\dev\PARADIGM_PLAN.md`. This document is the output
contract for sprint **S41** (Section 5.5 of the plan).

A sprint is not "done" until every applicable section below passes.
"Applicable" is set by the sprint's output contract: a token-package
sprint has no Lighthouse section, an API sprint has no brand-compliance
section, a docs sprint has no deploy section. Each section opens with
**Applies to** so reviewers can skip what does not fit.

"Pass" means a verifiable artifact in the PR or the handoff doc, not a
verbal assurance. Evidence before assertion.

---

## 0. Scope and how to use this

- Covers all 43 sprints in `PARADIGM_PLAN.md` across the four
  workstreams (A: brand system, B1: agile-cards-board re-skin, B2:
  career-ops web UI, C: Paradigm site and portal) plus the
  cross-cutting set (S41 to S43).
- Reviewers run the **Section 7 PR pre-merge checklist** at review
  time. Each item is either checked, or marked `N/A -- <reason>`.
  Silently skipping an item is not allowed.
- The bar applies whether the work is done by Drew, by an agent, or by
  a future collaborator. The DoD is the floor; individual sprints may
  raise it.

---

## 1. Brand compliance (Section 3 of `PARADIGM_PLAN.md`)

**Applies to:** any sprint that ships visible UI -- S08 to S15, S20 to
S25, S26 to S40, S43, and any later visual change to a
brand-consuming app.

**Does not apply to:** pure backend (S16 to S19, S33), pure docs (S06,
S27, S41, S42 sign-off), pure config without UI surface.

### 1.1 Tokens

- [ ] Consumes `@paradigm/brand-tokens` at v1.0.0 or higher (the S07
  freeze). Does not redefine color, type, spacing, radius, or shadow
  tokens locally.
- [ ] No hardcoded hex literals in component source. The Tailwind
  config is the only file that names hex, and it does so only through
  the brand preset. Grep evidence in the PR:
  `grep -rE '#[0-9A-Fa-f]{3,8}' src/` returns zero hits outside the
  preset.
- [ ] Dark theme is the default surface on every product app and on
  the Paradigm marketing site (Section 3.4 / OPEN-2). The marketing
  site ships a working light toggle; product apps may but are not
  required to.
- [ ] On agile-cards-board specifically: tier-badge colors do not
  collide with Paradigm Green (`#1F9D57`) or Verdant. Functional
  categorical palette stays as app-level overrides on top of the
  preset (Section 4.5).

### 1.2 Typography

- [ ] Headings: Space Grotesk. Body: Inter. Mono: JetBrains Mono. No
  other typefaces ship.
- [ ] Wordmark renders as **"Paradigm"** in sentence case, Space
  Grotesk 600, tight tracking (Section 3.5, OPEN-5).

### 1.3 Visual language

- [ ] Icons use Lucide only. No mixing icon sets.
- [ ] Spacing follows the 4px scale (`space-1` to `space-16`). Any
  off-scale value carries a one-line justification in the PR.
- [ ] Radius uses `radius-sm` 4, `radius-md` 8, `radius-lg` 12,
  `radius-full` only.
- [ ] Elevation uses the three shadow tokens plus `focus-ring`. No
  ad-hoc shadows.
- [ ] No stock photos of people pointing at laptops. Imagery is
  abstract technical (node graphs, schematics), real product
  screenshots, or code in JetBrains Mono.
- [ ] On Paradigm marketing pages: the shift-rule motif appears at
  most once per major section.
- [ ] One green element per diagram. Other structure stays on the
  grey ramp (Section 3.5).

---

## 2. Accessibility -- WCAG 2.1 AA minimum (Sections 3.4 and 5.9)

**Applies to:** every sprint that ships visible UI.

- [ ] axe-core run against every route, in every theme the route
  supports (both dark and light on the marketing site). Zero AA
  failures.
- [ ] Contrast verified to thresholds 4.5:1 (normal text) and 3:1
  (large text and UI components). For brand tokens, the verified
  pairs in Section 3.4 and Appendix A of the plan are the source of
  truth.
- [ ] Focus ring is the brand `focus-ring` token (2px Paradigm Green
  with 2px offset), visible on every interactive element, and clears
  3:1 against every background it lands on.
- [ ] Light-theme muted text uses Iron (`#3A403D`), not Slate
  (Slate-on-white is 3.83:1 and fails).
- [ ] Primary buttons label with Onyx (`#181C1B`) on the green fill,
  not white (Section 3.4).
- [ ] Status text on light backgrounds uses the corrected dark
  variants from Section 3.4 (`#1E7D3A`, `#8A6A00`, `#C73C34`,
  `#2F6E99`), not the bright semantic hues.
- [ ] Manual second pass by a separate reviewer (per Section 5.9's
  dual-agent rule for S14, S24, S32, S39): keyboard-only navigation
  reaches every interactive control, no focus traps, landmarks are
  present, screen-reader announces page structure.
- [ ] Any axe-core finding suppressed by exception carries a written
  justification in the PR description and the handoff.

---

## 3. Lighthouse thresholds (sprint S32)

**Applies to:** the Paradigm marketing site (S26 to S40) and the
portal shell (S35 to S39). Encouraged but not required for the
agile-cards-board and career-ops UIs.

- [ ] Lighthouse run on every route, in every shipped theme:
  - **Performance:** 90 or higher
  - **Accessibility:** 100
  - **Best Practices:** 100
  - **SEO** (marketing site only): 100
- [ ] Mobile profile run on the marketing site (S42 explicitly checks
  mobile). Tested at 320px and 768px widths at minimum.
- [ ] Zero console errors on every visited route (S42 final smoke
  test).
- [ ] Lighthouse report attached to the PR (HTML export or a
  screenshot of all scores).

---

## 4. Test coverage bar

**Applies to:** every sprint that ships code.

The bar scales with risk. The floor across the program is **no
coverage regression on any file the PR touches**. The targets below
are the per-tier minimums for new code added in the PR.

| Tier | What it covers | Minimum coverage on changed lines |
|---|---|---|
| **Critical** | Portal-auth and JWT verification (S33), the protected-route wrapper, bearer-token issuance and hashing, the Cloudflare Access policy code path, the brand-tokens preset export (S05), and the agile-cards runner's claim / merge gate / eligibility / reaper / amendments paths. | 90%+ line coverage, plus dedicated contract or property tests for the public surface. |
| **Standard** | App UI logic (board components, career-ops views, portal shell), REST endpoints (career-ops `/api/*`, dashboard summary), data-shape transforms. | 80%+ line coverage on new code. |
| **Low-logic** | Pure layout and copy (marketing site pages), CSS / token files, static config. | Visual regression and Lighthouse stand in for unit coverage; no unit-coverage requirement. |

- [ ] Coverage report attached to the PR for any sprint that touches
  critical or standard tier code.
- [ ] No coverage drop on any file the PR touches (the floor rule).
- [ ] For the brand-tokens package specifically (S02 to S07): every
  semantic color token in both themes has a contrast test asserting
  the verified ratio from Section 3.4 / Appendix A.

---

## 5. Security review gate for auth changes (Section 4.3, sprints S33 and S34)

**Applies to:** any change that touches the **auth surface**. The auth
surface is:

- The `portal-auth` module and any consumer that verifies the
  `CF-Access-Jwt-Assertion` header.
- Bearer-token issuance, hashing-at-rest, rotation, or revocation
  (the existing `create-token` script and any successor).
- The Cloudflare Access policy configuration that fronts the portal
  or any app behind the tunnel.
- Any new endpoint that authorizes a request, sets a session cookie,
  or logs the user out.
- CORS rules that sit behind the auth gate.

When the gate triggers:

- [ ] Two independent reviewer agents (or reviewers) inspect the diff
  before merge. One focuses on **XSS, CSRF, and token storage**; the
  other focuses on **session management, lifetime, and logout** (per
  Section 5.9, the highest-stakes review in the plan).
- [ ] All P0 and P1 findings are fixed before merge. P2 and P3 may
  ship with a tracked follow-up issue named in the PR.
- [ ] Findings and resolutions are recorded in the PR description and
  the handoff doc.
- [ ] No bearer token, JWT, secret, or hash preimage is logged,
  printed in an error message, or committed to the repo.
- [ ] No "this user is Drew" hardcoding anywhere in the auth path
  (Section 4.3, designed-for-collaborators rule).
- [ ] If the change extends the Access policy, the new policy is
  scoped to the smallest surface that still works (no
  `*.projectnexuscode.org` widening without a written reason).

---

## 6. Deploy verification

**Applies to:** every sprint whose output contract is a live URL or a
deployed artifact -- S15, S25, S40, S43, and any later sprint that
touches a deployed surface.

### 6.1 Pre-deploy

- [ ] Staging environment renders the change end-to-end. For S43-style
  reverse-proxy or base-path changes, a staging hostname is used (per
  the S43 scope).
- [ ] Visual diff against the previous deploy is reviewed when the
  sprint re-skins existing UI (S15 pattern).
- [ ] A safe-point tag is pushed before the deploy, per
  `SESSION_PROTOCOL.md` section 9:
  `git tag -f safe-pre-<sprint-id> HEAD && git push origin safe-pre-<sprint-id>`.

### 6.2 Post-deploy

- [ ] TLS is valid on every domain touched. No mixed-content warnings
  in the browser console.
- [ ] Redirects work: `www` to apex on the marketing site, any
  legacy-path redirects from the projectnexuscode-site replacement
  (S40).
- [ ] Reverse-proxy path routing reaches each app
  (`portal.projectnexuscode.org`, `app.projectnexuscode.org/board/`,
  `app.projectnexuscode.org/career/`) and the app loads correctly
  under its base path. The classic S43 failure -- forgetting the
  React Router `basename` and 404-ing on direct sub-route nav -- is
  explicitly tested by visiting a deep link, not just the index.
- [ ] CORS, base-path, and JWT-cookie scope verified across the
  origins involved.
- [ ] Mobile sanity check on the marketing site at 320px and 768px
  widths.
- [ ] Zero console errors on every visited route on the live URL.
- [ ] BROOKFIELD_PC dependency acknowledged: if the deployed surface
  is reached through the home tunnel, the PR notes "best effort,
  home machine" (Section 4.7).

### 6.3 Rollback

- [ ] The PR description names the exact rollback command, of the
  form:
  ```powershell
  git reset --hard safe-pre-<sprint-id>
  git push --force-with-lease origin <branch>
  ```
- [ ] Any data migration deployed in the same change has a documented
  reverse step, or is explicitly flagged as one-way.

---

## 7. PR pre-merge checklist

Every PR pastes this block at the bottom of the description and checks
the items that apply. If a section is not applicable, write
`N/A -- <reason>` instead of leaving it blank.

```
- [ ] Sprint ID named, with a link to the S## row in PARADIGM_PLAN.md
- [ ] Output contract from PARADIGM_PLAN.md quoted in the PR description
- [ ] Section 1 -- Brand compliance      (UI sprints)
- [ ] Section 2 -- Accessibility AA      (UI sprints)
- [ ] Section 3 -- Lighthouse            (marketing site, portal shell)
- [ ] Section 4 -- Test coverage at the right tier
- [ ] Section 5 -- Auth security review gate cleared
                   (auth-surface sprints only; dual-agent review,
                   all P0/P1 fixed)
- [ ] Section 6 -- Deploy verification   (deployed sprints)
- [ ] Handoff doc in docs/handoffs/ written per SESSION_PROTOCOL.md
- [ ] safe-pre-<sprint-id> tag pushed   (deployed sprints)
```

---

## 8. What this document intentionally does not do

- It does not set a coding style guide; each repo decides its own.
- It does not pick a branching strategy; `SESSION_PROTOCOL.md`
  section 10 defers this per project.
- It does not set release cadence.
- It is not a substitute for code review. The DoD is the floor that
  every review enforces, not the ceiling that review aims for.

---

## 9. References

- `C:\dev\PARADIGM_PLAN.md` -- the locked plan. Brand spec in Section
  3, Paradigm website and portal (including the auth model) in
  Section 4, multi-agent cross-validation points in Section 5.9. The
  S41 output contract that produced this document is in Section 5.5.
- `C:\dev\SESSION_PROTOCOL.md` -- master session protocol; sections 7
  (PowerShell-only git in `C:\dev\`) and 9 (rollback tagging) are
  load-bearing for the deploy section above.
- `C:\dev\NAMING_CONVENTIONS.md` -- shared naming standard.
- `@paradigm/brand-tokens` v1.0.0 -- the token contract frozen at
  S07. The Tailwind preset and `tokens.css` it exports are the only
  legal source of brand values for any consuming app.
