# Ledger (one file per entry)

What changed, why it changed, and where things stand. Any agent or human can read the
newest entries and pick up the work without asking.

**One file per entry**, in this directory, named `YYYY-MM-DD-HHMM-<slug>.md`. New files
never conflict, so parallel PRs no longer collide the way they would if every PR appended
to one `docs/LEDGER.md`. This repo never had a committed `docs/LEDGER.md` on `main`, so
there is nothing frozen here: this directory is the ledger from the start.

## Writing an entry

```
node scripts/ledger.mjs new "Short title of the change"
```

That creates the file with the current local time and an empty template. Fill in:

```
# YYYY-MM-DD HH:MM TZ - <short title>
- **Who:** <session name / agent / Drew>
- **Change:** <what changed, concretely>
- **Why:** <the reason; for hard calls, the alternative and why it lost>
- **State after:** <what is true now; what is still open>
- **Refs:** <PRs, commits, files, docs>
```

## Rules

- **Append only.** Never edit or delete an entry. To correct one, add a new entry whose
  **Why** starts `Supersedes <file name>` and explains the correction.
- **One entry per meaningful change:** a merge, deploy, migration, config or infra change,
  a decision, or a revert. Typo fixes don't need one.
- **Record hard calls** under **Why**, with the alternative and why it lost.
- **State facts you checked.** Mark anything unverified, for example "(unverified)".
- **Absolute dates and times** with a time zone, never "today". The file name's date and
  time must match the heading's.
- No em dashes.

`.github/workflows/ledger.yml` runs `node scripts/ledger.mjs check` on every pull request
and on push to `main` that touches `docs/ledger/**` or `scripts/ledger.mjs`, so a
malformed entry fails CI.

## Reading

```
node scripts/ledger.mjs print        # every entry, oldest first
```

There is no committed index on purpose: a generated index file would conflict exactly the
way a single ledger file would.
