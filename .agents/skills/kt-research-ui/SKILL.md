---
name: kt-research-ui
description: Use when auditing, redesigning, or iterating the Django and Bootstrap interfaces in this KT research data-collection repository, especially teacher dashboards, question-bank workflows, experiment configuration, student single-question assessment screens, responsive states, and accessibility.
---

# KT Research UI

## Design read

Treat this repository as an educational research data-collection product for teachers, administrators, and students, not as a marketing site. Make the interface trustworthy, restrained, clear, stable, and accessible. Allow higher information density for teachers, but minimize cognitive interference in student assessment screens.

Use these defaults:

- `DESIGN_VARIANCE: 4`
- `MOTION_INTENSITY: 2`
- `VISUAL_DENSITY: 6`
- `DATA_CLARITY: 10`
- `ACCESSIBILITY_PRIORITY: 10`

## Required workflow

1. Read [RESEARCH-INVARIANTS.md](references/RESEARCH-INVARIANTS.md) before touching templates, JavaScript, views, services, or tests.
2. Inventory the affected routes, templates, forms, DOM hooks, Fetch contracts, and user paths.
3. Audit the current rendered desktop and mobile states with [PAGE-AUDIT.md](references/PAGE-AUDIT.md).
4. Read [DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md), then reuse its tokens and component rules before adding page-specific CSS.
5. Implement the smallest targeted change in Django Templates, Bootstrap 5.3, native CSS, and native JavaScript.
6. Verify the affected flow and complete [ACCEPTANCE-CHECKLIST.md](references/ACCEPTANCE-CHECKLIST.md).

## Engineering constraints

- Preserve the current Django monolith, Django Templates, Bootstrap, native CSS, and native JavaScript Fetch API.
- Do not introduce React, Vue, Tailwind, HTMX, GSAP, an SPA, or a frontend build pipeline.
- Do not change URLs, form field names, DOM IDs, `data-*` attributes, CSRF behavior, or Fetch payloads unless the same change includes regression coverage.
- Prefer design tokens and reusable template components over repeated page-specific markup.
- Keep student assessment stability and cognitive load ahead of visual novelty.
- Keep teacher data hierarchy, action paths, error recovery, and irreversible-action clarity ahead of decoration.
- Do not add production dependencies unless Bootstrap, CSS, and native JavaScript cannot reasonably implement the requirement.
- Use the local system font stack. Do not add remote fonts.
- Keep transitions between 120 and 200 ms and support `prefers-reduced-motion`.
- Never use `window.alert()` or native `confirm()` for production feedback. Use inline status regions or accessible Bootstrap dialogs.
- Never encode state with color alone. Pair color with text, shape, or an accessible label.

## Visual rules

- Use a low-saturation navy brand color, cool neutral surfaces, and semantic green, amber, and red only for real status.
- Avoid purple-blue gradients, large glass panels, excessive shadows, decorative statistics, giant marketing headlines, emoji icons, pill badges everywhere, and three identical generic cards.
- Use cards only where grouping or elevation communicates hierarchy. Prefer section boundaries, tables, and spacing for dense teacher workflows.
- Use tabular figures for IDs, sequence numbers, counts, dates, durations, and timers.
- Give containers, controls, and status labels distinct restrained radii.
- Preserve a visible keyboard focus indicator, real labels, useful error summaries, and touch targets of at least 44 px for primary student interactions.
- Make the local app fully usable when offline. Vendor Bootstrap runtime files with their license when possible; otherwise document the degradation honestly.

## Verification gate

Run at least:

```text
python manage.py check
python manage.py test
python manage.py collectstatic --noinput
node --check static/js/app.js
node --check static/js/assessment.js
```

Also exercise teacher and student demo paths at 1440 x 900 and 390 x 844. For any touched research invariant, add or update an automated regression test before reporting completion.

