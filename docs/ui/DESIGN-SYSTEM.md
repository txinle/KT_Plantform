# KT research interface design system

## Design read

This is an institutional education-research data product. The interface uses calm precision, trust-first hierarchy, high legibility, and restrained enterprise styling. Teachers receive compact data-rich surfaces; students receive quieter task-focused screens.

Parameters: design variance 4, motion intensity 2, teacher density 7, student density 4, data clarity 10, accessibility priority 10.

## Foundation

- Stack: same-origin, independently built Vue 3 + TypeScript business pages; Django domain services, DRF APIs and session authentication. `/admin` remains Django. The standalone rollout gate selects registered HTML entries; API, download and mutation routes remain backend-owned.
- Theme: one light institutional theme. No section-level dark inversions.
- Font: `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `PingFang SC`, `Hiragino Sans GB`, `Microsoft YaHei`, sans-serif.
- Numeric data: `font-variant-numeric: tabular-nums` for IDs, sequence numbers, timestamps, counts, versions, durations, and timers.
- Workspace: 64 px public header, 224 px independently scrolling left navigation, remaining-width content with 24 px desktop / 16 px mobile gutters. Below 1024 px the same navigation becomes a focus-managed drawer. Module tabs mount within the active course navigation section.
- Assessment: focused question area with a 280 px right question directory; course navigation is omitted. Answer, feedback and capture behavior remain owned by the existing runtime and server.

## Token families

`frontend/src/design-system/tokens.css` is the frontend token source; foundations and workspace styles consume it. `static/css/app.css` remains for legacy fallback pages:

- Primary `#4D49FC`, hover `#3734C9`, selected surface `#F0EFFF`; the KT wordmark retains its navy identity.
- Page `#F7F7F8`, surface `#FFFFFF`, body `#242429`, supporting text `#62626C`.
- Success `#16713E`, warning `#875B0A`, danger `#B52E37`; corresponding text/surface contrast is 5.56:1, 5.41:1 and 5.56:1. These colors identify actual states.
- Body 15–16 px, supporting text 14 px, page headings 26 px desktop / 24 px mobile, section headings 18–20 px.
- Font weights 400, 500, 600, and 700.
- Line-height levels for compact UI, body, and long question text.
- A 4 px spacing base from `--space-1` through `--space-12`.
- Radius levels: status 6 px, controls 8 px, grouped surfaces and dialogs 10 px.
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

## Navigation and feedback layout

- Secondary navigation is inserted immediately after its active primary entry in the Vue VNode tree. DOM and keyboard order match visual order; CSS order is not used for sidebar insertion.
- Mobile feedback directories use native collapsible details, closed initially below 1024 px. The directory DOM stays mounted; expanding navigation does not request answers or record feedback exposure.
- Answer/explanation sections retain their original explicit expansion actions. Layout changes never initialize input or generate research events.
