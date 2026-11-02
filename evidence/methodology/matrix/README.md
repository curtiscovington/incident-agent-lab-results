# Current editorial matrix specification

This is the revised native figure specification. The historical catalog source
under `code/figure-reference` is preserved for provenance; this specification
supersedes its wording for the article. Data values and JSON-valid counts are
unchanged. Use `figure_engine.render(spec)` from the included reference source
and its `rasterize.py` with an installed compatible fontTools/Playwright/Chromium
environment; no inference or tool calls are involved. The reference source is
Linux-oriented and uses installed DejaVu fonts; dependencies/font binaries are
not bundled. The saved PNG is delivered separately to the draft editor.

See the root README for article labels and frozen arm IDs.
