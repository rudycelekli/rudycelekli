# DESIGN.md: Agentic Power profile extension

## Source

- URL: https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/
- Capture date: 2026-09-26
- Evidence: live rendered page, accessibility structure, computed CSS tokens, and the source framework graphic
- Attribution: The Agentic Power Framework is developed by Dr. Mark Allen at HeroForge.AI. This profile adapts its concepts and color roles; it does not claim authorship of the framework.

## Reference Visual

![Agentic Power Framework](https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/assets/agentic-power-framework.svg)

Use the source page as the visual reference for hierarchy, editorial confidence, and the blue/teal/violet/amber semantic palette. The profile implementation translates that light editorial system into Rudy's existing dark technical visual language rather than cloning the page.

## Design Summary

The source feels like a rigorous strategy paper made interactive: generous whitespace, oversized navy headlines, concise numbered sections, bright blue quantitative emphasis, and restrained diagrams. The profile adaptation should preserve the clarity and conceptual hierarchy while remaining consistent with the existing dark, evidence-first GitHub identity.

## Design Tokens

### Colors

Observed source tokens:

- Ink: `#14253E`
- Paper: `#F7F9FC`
- Blue: `#2159E8`
- Teal: `#087F80`
- Violet: `#7254B3`
- Amber: `#9A6217`
- Muted text: `#526177`
- Rules: `#D9E2ED`

Dark-profile translation:

- Canvas: `#07111E` to `#150B2E`
- Primary text: `#F8FAFC`
- Muted text: `#94A3B8`
- Capability / direction: `#4D7CFE`
- System design / orchestration: `#2DE2C5`
- Autonomy / scale: `#9B7CFF`
- Learning / improvement: `#F2A93B`

### Typography

- Source family: `Avenir Next`, Avenir, `Segoe UI`, sans-serif.
- Source hero: approximately 65px, weight 650, compact line height.
- Profile headings: system sans-serif, bold, tight tracking.
- System labels: monospace, uppercase, wide tracking.
- Keep paragraphs concise and let the visual carry the taxonomy.

### Spacing And Layout

- Broad 1200px visual canvas.
- 24–32px rounded panels with subtle one-pixel borders.
- Large title zone followed by one strong conceptual diagram.
- Four evenly weighted capability modules; avoid a dense badge wall.
- Use generous internal padding and clear left-to-right progression.

## Components

- Formula strip: one clear ratio with a visible numerator and denominator.
- Four-lever capability rail: Delegate, Orchestrate, Verify, Improve.
- Human-judgment boundary: goals and constraints enter from the left.
- Evidence gate: only accepted, replayable work counts at the output.
- Live Agentic Power equation: modeled HEH, operator-estimated human direction, central AP, and a visible uncertainty range. Preserve the conservative GitHub direction proxy in the evidence data for comparison.
- Two-row month-over-month timeline from January 2025 onward: accepted HEH bars plus a modeled AP line.
- Semantic SVG motion: evidence signals flow, operating stages activate in sequence, and charts reveal their series; all motion stops under `prefers-reduced-motion`.
- Responsive SVG copy: wrap or clamp repository names and descriptions inside their own bounds and preserve a readable text equivalent in the README.
- Auto-discovered contribution constellation: qualifying upstream projects flow into a two-column evidence layout with current stars and forks labeled as project reach, never personal credit.
- Attribution link: directly credit the Agentic Power Framework and its author.

## Page Pattern

1. Existing proof-driven hero.
2. Evidence thesis and proof loop.
3. Verified upstream contribution proof, before self-authored projects.
4. Flagship proof stack.
5. Live modeled Agentic Power snapshot with evidence and caveats.
6. Agentic operating model and framework attribution.
7. Research questions, tools, community leadership, and current work.
8. Closing invitation.

## Content Style

- Short declarative headings.
- Prefer operational verbs: delegate, orchestrate, verify, improve.
- Distinguish capability from measured output.
- Never publish an Agentic Power multiplier without visible evidence scope, assumptions, uncertainty, and audit limitations.
- Treat human judgment, acceptance criteria, and authority boundaries as core system components.

## Agent Build Instructions

- Preserve the existing dark navy hero and cyan/violet visual identity.
- Translate the framework's four semantic colors into luminous accents on dark panels.
- Build original diagrams; do not copy HeroForge artwork or language beyond attributed framework terms.
- Tie every capability claim to public work or phrase it as an operating principle.
- Keep the profile scannable: one graphic, one short explanation, four concrete operating principles.
- Keep contribution claims machine-counted. Discover public upstream projects from merged PR history, include one only when GitHub's Contributors API lists Rudy, and calculate accepted code only from merged pull requests.
- Treat GitHub-indexed commits as a separate cached attribution signal, never as a synonym for merged pull requests or accepted work.

## Rerun Inputs

```yaml
workflow: firecrawl-website-design-clone
source_url: https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/
target_stack: GitHub profile README + SVG
output: DESIGN.md
```
