# assertions/

One `.yml` file per surface. Each file holds the full assertion set for that
surface -- performance, accessibility, DOM checks, and anything else that is
too slow or too browser-dependent for the fast smoke run.

`smoke.yml` (in the parent `verify/` directory) is the fast subset: it runs
on every PR and covers only the most critical assertions for each surface.

Files in this directory run during deep verify (`/verify deep <repo-path>`),
which is required before merging any PR that touches a Tier-3 surface as
declared in `tier_map.yml`.

Name each file after the surface name used in `smoke.yml` -- for example,
a surface named `dashboard` in `smoke.yml` gets `assertions/dashboard.yml`.
