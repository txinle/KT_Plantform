# KT research interface design system

## Design read

This is an institutional education-research data product. The interface uses calm precision, trust-first hierarchy, high legibility, and restrained enterprise styling. Teachers receive compact data-rich surfaces; students receive quieter task-focused screens.

Parameters: design variance 4, motion intensity 2, teacher density 7, student density 4, data clarity 10, accessibility priority 10.

## Foundation

- Stack: Django Templates, local Bootstrap 5.3, native CSS, native JavaScript.
- Theme: one light institutional theme. No section-level dark inversions.
- Font: `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `PingFang SC`, `Hiragino Sans GB`, `Microsoft YaHei`, sans-serif.
- Numeric data: `font-variant-numeric: tabular-nums` for IDs, sequence numbers, timestamps, counts, versions, durations, and timers.
- Content width: 1320 px maximum with responsive gutters.

## Token families

The implementation in `static/css/app.css` defines:

- Brand navy and hover/active variants.
- Cool neutral page, surface, subtle surface, border, muted text, and primary ink colors.
- Semantic success, warning, danger, and information colors with pale supporting surfaces.
- A type scale from compact metadata to page headings.
- Font weights 400, 500, 600, and 700.
- Line-height levels for compact UI, body, and long question text.
- A 4 px spacing base from `--space-1` through `--space-12`.
- Radius levels: status 4 px, controls 6 px, grouped surfaces 10 px, dialogs 12 px.
- A single cool-tinted elevation shadow.
- Motion durations 120 ms and 180 ms.
- Z-index levels for sticky navigation, sticky actions, and dialogs.

## Component rules

- Page headers contain context/back path, title, supporting text, and at most one primary action group.
- Status badges always include readable text. Color never carries meaning alone.
- Tables use clear column headers, restrained row actions, tabular numbers, and a controlled horizontal scroll on narrow screens.
- Forms place a real label above every input, optional/help text below the label or control, and an inline error below the field. Invalid submissions also show a linked error summary.
- Empty states state what is missing and the next valid action.
- Destructive and irreversible actions use danger styling and an accessible confirmation dialog.
- Teacher surfaces use borders and section spacing more often than drop-shadow cards.
- Student options are whole-row labels with a minimum 52 px target and a visible selected state.

## Accessibility and motion

- Body text, button text, and state text target WCAG AA contrast.
- Every interactive control has a visible `:focus-visible` outline.
- The skip link becomes visible on focus.
- Messages and save feedback use appropriate live regions.
- The current navigation item uses `aria-current="page"`; the current question uses `aria-current="step"`.
- At 200 percent zoom, primary flows remain available without two-dimensional page scrolling.
- Nonessential transitions are disabled under `prefers-reduced-motion: reduce`.

