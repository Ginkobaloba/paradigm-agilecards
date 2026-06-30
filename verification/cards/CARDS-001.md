---
AC: AC-CARDS-001
Phase: v1
Status: PASS
Verifier: Claude (K2)
Verified at: 2026-06-30
Evidence: >
  AC text: "Repo exists at the org's GitHub org under the name
  paradigm-agilecards. Verification: Audit -- repo URL recorded in DECISIONS.md."

  - GitHub repo renamed agile-cards -> paradigm-agilecards (chunk K2). Confirmed:
    `gh repo view Ginkobaloba/paradigm-agilecards --json name,url,visibility,deleteBranchOnMerge`
    -> {"name":"paradigm-agilecards",
        "url":"https://github.com/Ginkobaloba/paradigm-agilecards",
        "visibility":"PUBLIC","deleteBranchOnMerge":true}.
  - Repo URL recorded in DECISIONS.md (repo root), "Repository" section:
    https://github.com/Ginkobaloba/paradigm-agilecards
  - DECISIONS.md here is a local stub; the canonical platform DECISIONS.md is
    owned by K18 (AC-COMP-001/002/003).
  - Landed on main via PR #44 (squash). CI run 28424040046 all green.
---

# AC-CARDS-001 -- Repo exists as `paradigm-agilecards`, URL recorded in DECISIONS.md

## Audit steps

```bash
gh repo view Ginkobaloba/paradigm-agilecards --json name,url,visibility,deleteBranchOnMerge
grep -n "paradigm-agilecards" DECISIONS.md
```

Expected: repo name `paradigm-agilecards`, URL
`https://github.com/Ginkobaloba/paradigm-agilecards`, and the same URL present
in `DECISIONS.md`.

## Result

PASS -- repo renamed and live under the new name; URL recorded in DECISIONS.md.
