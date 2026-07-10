# Vendored frontend libraries

Phase 6 Track A4 (fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md
§3 A4, decision 3): `chart.js` and `cytoscape` are vendored here instead of
loaded from jsdelivr, so the demo works on an offline / intranet-only network
(no CDN egress required).

Files are the unmodified minified UMD builds fetched directly from jsdelivr;
their own banner comments (version, copyright) are preserved as-is and were
not edited.

| File | Package | Version | Source | License |
|---|---|---|---|---|
| `chart.umd.min.js` | [chart.js](https://www.chartjs.org/) | 4.5.1 | `https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js` | MIT |
| `cytoscape.min.js` | [cytoscape](https://js.cytoscape.org/) | 3.34.0 | `https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js` | MIT |

Versions were confirmed from each file's own banner comment (`chart.umd.min.js`)
or embedded version string (`cytoscape.min.js`, `Gh.version="3.34.0"`), cross-checked
against jsdelivr's `x-jsd-version` response header at download time. License text
was verified at `https://cdn.jsdelivr.net/npm/<package>@<version>/LICENSE[.md]`
(both MIT) but is not duplicated here -- see the upstream repos:
- https://github.com/chartjs/Chart.js/blob/master/LICENSE.md
- https://github.com/cytoscape/cytoscape.js/blob/unstable/LICENSE

`src/static/index.html` loads both from `/static/vendor/...` (served by the
existing FastAPI `StaticFiles` mount at `/static`); no server code change was
needed.

## Updating

Both are pinned to a major version (`chart.js@4`, `cytoscape@3`) rather than
an exact patch, matching how the CDN tags were pinned before vendoring. To
refresh:

```bash
curl -fsSL https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js -o src/static/vendor/chart.umd.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js -o src/static/vendor/cytoscape.min.js
```

Then update the version/date in this file and sanity-check
(`node --check src/static/vendor/*.js`, check the file size and banner
comment / embedded version string are non-empty and match the new version).

## If download is not possible (proxy / offline)

If `curl` cannot reach jsdelivr from your machine, do not vendor a partial or
placeholder file. Keep `index.html` pointing at the CDN URLs and download the
two files manually from another machine with internet access:

```bash
https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js
https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js
```

Then drop them into this directory with the exact filenames above, update the
version table, and switch the two `<script src>` tags in `index.html` to
`/static/vendor/chart.umd.min.js` and `/static/vendor/cytoscape.min.js`.
