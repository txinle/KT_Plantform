# KT research interface rules

## Direction

Use a calm institutional research-product language: precise hierarchy, high legibility, restrained enterprise surfaces, and compact teacher workflows. Reference-led pages follow their supplied visual system before generic taste conventions.

## Experiment reference system

- Primary canvas: 1586×992, DPR 1.
- Global shell: one 64 px navigation row; centered business navigation; breadcrumb and local experiment tabs inside the content frame.
- Content frame: wide desktop grid with consistent left/right gutters; compact vertical rhythm; no decorative hero treatment.
- Typography: local Chinese system sans stack; strong but restrained page titles; compact 14–16 px table/form copy; tabular numeric content.
- Color: deep navy text, action blue, cool neutral borders/backgrounds, semantic green/amber/red only when the server state warrants it.
- Surfaces: thin borders, 8–10 px outer radii, subtle shadow only where elevation clarifies grouping.
- Forms: left label/right control row grid on desktop; compact grouped sections; preserved native meaning and accessible names.
- Tables: dense, aligned, readable; actions remain text/button controls; complex details may collapse but cannot disappear.
- Icons: reuse the local SVG sprite and pair status color with text/shape. Do not use emoji or remote icon fonts.

## Responsive and motion

- At 1440×900 retain the desktop hierarchy without clipping or page-level horizontal overflow.
- At 390×844 stack grids and expose dense table data as responsive rows/cards without changing meaning or action availability.
- At 200% zoom, allow natural reflow; do not solve overflow by shrinking essential text below readable sizes.
- Keep transitions within 120–200 ms, state-oriented only, and disable nonessential motion under `prefers-reduced-motion`.

## Scope rule

Feature CSS must be rooted beneath the experiment module container or use feature-specific class names. Never place generic shared selectors in a feature stylesheet unless intentionally changing the shared design system with cross-module tests.
