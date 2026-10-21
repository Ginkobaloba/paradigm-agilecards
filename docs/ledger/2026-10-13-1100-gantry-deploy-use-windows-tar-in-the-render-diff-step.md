# 2026-10-13 11:00 CDT - GANTRY_DEPLOY: use Windows tar in the render-and-diff step
- **Who:** PromptBuilder (portal seat), at Daedalus's request
- **Change:** `docs/board/GANTRY_DEPLOY.md` step 1 now extracts the 8d43b7a recipe with `C:\Windows\System32\tar.exe` instead of plain `tar`.
- **Why:** in a PowerShell session with Git on PATH, plain `tar` resolves to Git's GNU tar (`C:\Program Files\Git\usr\bin\tar.exe`), which parses `C:` in the archive path as a remote host and fails ("Cannot connect to C: resolve failed"). Hit while rehearsing PR #77's option B render on 2026-10-13.
- **State after:** docs only; nothing run against the live Gantry.
- **Refs:** `docs/board/GANTRY_DEPLOY.md`, PR #77