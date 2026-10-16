# app: the public dashboard

The dashboard pages were designed on a Claude Design canvas. This folder runs those same pages as a static
site: `src/pages/*.dc.html` are copies of the canvas pages, and `src/dc/runtime.js` is a small Preact
renderer for their format (`{{holes}}`, `<sc-for>`, `<sc-if>`, `<dc-import>` and a logic class per page).

```
npm ci            # install (Node 18+)
npm run dev       # http://localhost:5173, data served from ../data/dashboard/app_v1
npm test          # renderer tests + every page rendered against the local data
npm run build     # static site in dist/
npm run preview   # serve dist/ locally, with the local data
```

## Data

The pages read their data from `VITE_DATA_BASE` (default `/data/v1`, i.e. the same server). The files are
not committed; locally they live in `data/dashboard/app_v1/`:

| File | What it holds |
|---|---|
| `national.json` | H3 r7 cells (map), districts and their constraints, national series |
| `site.json` | metadata, checks, sources, workflow graph, years, sub-district summaries |
| `subdistrict_{nyamirambo,muhoza,nasho}.json` | buildings of the three deep dives |
| `profiles.json` | the RAMP profile library |
| `figures/*.png` | report figures shown on Climate and Validation |

`data-files.json` records the size and sha256 of each, and which canvas upload it came from.

## Updating from the canvas

Changes made on the canvas reach the site by copying the pages again:

1. Read the canvas's `project/*.dc.html` files and any uploads they reference into two folders
   (Claude can do this with the Artifact tool's `read` action).
2. `python scripts/sync_canvas.py PAGES_DIR UPLOADS_DIR`: copies the pages, rewrites upload links to
   readable names (`app/data-map.json`; figures are named from their alt text) and refreshes
   `data/dashboard/app_v1/`. It stops on an upload id it doesn't know: add it to `data-map.json`.
3. `npm test && npm run build`, then deploy (`docs/hosting.md`).

`sync_canvas.py` also makes the site-only edits: it drops the canvas's Design system tab and fills in the
citation's team name.

Hosting: see [`docs/hosting.md`](../docs/hosting.md).
