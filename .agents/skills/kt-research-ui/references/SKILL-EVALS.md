# Skill trigger evaluations

## Should trigger

- "Reproduce the supplied reference at 1:1 fidelity for an existing Vue pilot while retaining the same-URL Django kill-switch fallback."
- "This page is scheduled for Vue migration; use the redesign to complete the Service-to-API-to-Vue vertical slice."
- "Audit whether this legacy Django page has entered the frontend separation migration scope."
- "Improve the mobile and accessibility behavior of the student question page."
- "Unify the form, status, and visual hierarchy of the experiment-management page."

## Should not trigger or should refuse scope expansion

- "Change seed_demo to three classes and 120 students so the data matches the screenshot."
- "Let the frontend decide whether a class can publish, feedback is open, or randomization passed."
- "Vue is already the primary path; copy the same new layout into the Django template."
- "Fix numeric-question grading."
- "Change PostgreSQL indexes."
- "Add a new KT model."
- "Rewrite randomization, grading, or data-export algorithms."

## Expected migration decisions

- A route with an approved Vue component/API and same-URL feature-flag branch plus retained template is classified as **Vue primary + Django fallback**. New presentation work goes to Vue; the template receives only necessary safety compatibility.
- An approved Vue migration with missing route/API/error-state coverage is **Vue migration in progress**. The agent completes the scoped vertical slice instead of polishing the template first.
- A page with no approved Vue slice or rollout evidence is **legacy Django** and remains on its existing presentation path.
- A module whose exit criteria explicitly retired the template is **Vue migration complete**; the agent does not recreate a fallback.
- If evidence conflicts, the agent asks for clarification instead of creating a second long-lived implementation.

## Expected authority behavior

- Select the correct persisted or user-supplied visual reference for the page state.
- Treat reference imagery as visual authority only, never as a data specification.
- Preserve Service authority and server-trimmed API contracts.
- Add authorized API projection fields when required by a Vue vertical slice, without duplicating domain rules in serializers or Vue.
- Scope Vue feature styles and test structure/behavior before pixel calibration.
