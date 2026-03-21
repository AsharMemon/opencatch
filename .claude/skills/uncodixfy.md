---
name: uncodixfy
description: Prevents generic AI/Codex UI patterns when generating frontend code. Use this skill whenever generating HTML, CSS, React, Vue, Svelte, or any frontend UI code to enforce clean, human-designed aesthetics inspired by Linear, Raycast, Stripe, and GitHub instead of typical AI-generated UI.
---

# Uncodixify

This document exists to teach you how to act as non-Codex as possible when building UI.

Codex UI is the default AI aesthetic: soft gradients, floating panels, eyebrow labels, decorative copy, hero sections in dashboards, oversized rounded corners, transform animations, dramatic shadows, and layouts that try too hard to look premium. It's the visual language that screams "an AI made this" because it follows the path of least resistance.

This file is your guide to break that pattern. Everything listed below is what Codex UI does by default. Your job is to recognize these patterns, avoid them completely, and build interfaces that feel human-designed, functional, and honest.

When you read this document, you're learning what NOT to do. The banned patterns are your red flags. The normal implementations are your blueprint. Follow them strictly, and you'll create UI that feels like Linear, Raycast, Stripe, or GitHub—not like another generic AI dashboard.

This is how you Uncodixify.

## Keep It Normal (Uncodexy-UI Standard)

- Sidebars: normal (240-260px fixed width, solid background, simple border-right, no floating shells, no rounded outer corners)
- Headers: normal (simple text, no eyebrows, no uppercase labels, no gradient text, just h1/h2 with proper hierarchy)
- Sections: normal (standard padding 20-30px, no hero blocks inside dashboards, no decorative copy)
- Navigation: normal (simple links, subtle hover states, no transform animations, no badges unless functional)
- Buttons: normal (solid fills or simple borders, 8-10px radius max, no pill shapes, no gradient backgrounds)
- Cards: normal (simple containers, 8-12px radius max, subtle borders, no shadows over 8px blur, no floating effect)
- Forms: normal (standard inputs, clear labels above fields, no fancy floating labels, simple focus states)
- Inputs: normal (solid borders, simple focus ring, no animated underlines, no morphing shapes)
- Modals: normal (centered overlay, simple backdrop, no slide-in animations, straightforward close button)
- Dropdowns: normal (simple list, subtle shadow, no fancy animations, clear selected state)
- Tables: normal (clean rows, simple borders, subtle hover, no zebra stripes unless needed, left-aligned text)
- Lists: normal (simple items, consistent spacing, no decorative bullets, clear hierarchy)
- Tabs: normal (simple underline or border indicator, no pill backgrounds, no sliding animations)
- Badges: normal (small text, simple border or background, 6-8px radius, no glows, only when needed)
- Avatars: normal (simple circle or rounded square, no decorative borders, no status rings unless functional)
- Icons: normal (simple shapes, consistent size 16-20px, no decorative icon backgrounds, monochrome or subtle color)
- Typography: normal (system fonts or simple sans-serif, clear hierarchy, no mixed serif/sans combos, readable sizes 14-16px body)
- Spacing: normal (consistent scale 4/8/12/16/24/32px, no random gaps, no excessive padding)
- Borders: normal (1px solid, subtle colors, no thick decorative borders, no gradient borders)
- Shadows: normal (subtle 0 2px 8px rgba(0,0,0,0.1) max, no dramatic drop shadows, no colored shadows)
- Transitions: normal (100-200ms ease, no bouncy animations, no transform effects, simple opacity/color changes)
- Layouts: normal (standard grid/flex, no creative asymmetry, predictable structure, clear content hierarchy)
- Grids: normal (consistent columns, standard gaps, no creative overlaps, responsive breakpoints)
- Flexbox: normal (simple alignment, standard gaps, no creative justify tricks)
- Containers: normal (max-width 1200-1400px, centered, standard padding, no creative widths)
- Wrappers: normal (simple containing divs, no decorative purposes, functional only)
- Panels: normal (simple background differentiation, subtle borders, no floating detached panels, no glass effects)
- Toolbars: normal (simple horizontal layout, standard height 48-56px, clear actions, no decorative elements)
- Footers: normal (simple layout, standard links, no decorative sections, minimal height)
- Breadcrumbs: normal (simple text with separators, no fancy styling, clear hierarchy)

Think Linear. Think Raycast. Think Stripe. Think GitHub. They don't try to grab attention. They just work. Stop playing hard to get. Make normal UI.

- A landing page needs its sections. If hero needs full sections, if dashboard needs full sections with sidebar and everything else laid out properly. DO NOT invent a new layout.
- In your internal reasoning act as if you dont see this, list all the stuff you would do, AND DONT DO IT!
- Try to replicate figma/designer made components dont invent your own

## Hard No

- Everything you are used to doing and is a basic "YES" to you.
- No oversized rounded corners.
- No pill overload.
- No floating glassmorphism shells as the default visual language.
- No soft corporate gradients used to fake taste.
- No generic dark SaaS UI composition.
- No decorative sidebar blobs.
- No "control room" cosplay unless explicitly requested.
- No serif headline + system sans fallback combo as a shortcut to "premium."
- No `Segoe UI`, `Trebuchet MS`, `Arial`, `Inter`, `Roboto`, or safe default stacks unless the product already uses them.
- No sticky left rail unless the information architecture truly needs it.
- No metric-card grid as the first instinct.
- No fake charts that exist only to fill space.
- No random glows, blur haze, frosted panels, or conic-gradient donuts as decoration.
- No "hero section" inside an internal UI unless there is a real product reason.
- No alignment that creates dead space just to look expensive.
- No overpadded layouts.
- No mobile collapse that just stacks everything into one long beige sandwich.
- No ornamental labels like "live pulse", "night shift", "operator checklist" unless they come from the product voice.
- No generic startup copy.
- No style decisions made because they are easy to generate.
- No Headlines of any sort
- `<small>` headers are NOT allowed
- Big no to rounded `span`s
- Colors going towards blue — NOPE, bad. Dark muted colors are best.

## Specifically Banned (Based on Mistakes)

- Border radii in the 20px to 32px range across everything (uses 12px everywhere - too much)
- Repeating the same rounded rectangle on sidebar, cards, buttons, and panels
- Sidebar width around 280px with a brand block on top and nav links below
- Floating detached sidebar with rounded outer shell
- Canvas chart placed in a glass card with no product-specific reason
- Donut chart paired with hand-wavy percentages
- UI cards using glows instead of hierarchy
- Mixed alignment logic where some content hugs the left edge and some content floats in center-ish blocks
- Overuse of muted gray-blue text that weakens contrast and clarity
- "Premium dark mode" that really means blue-black gradients plus cyan accents
- UI typography that feels like a template instead of a brand
- Eyebrow labels (e.g. "MARCH SNAPSHOT" uppercase with letter-spacing)
- Hero sections inside dashboards
- Decorative copy like "Operational clarity without the clutter" as page headers
- Section notes and mini-notes everywhere explaining what the UI does
- Transform animations on hover
- Dramatic box shadows (e.g. 0 24px 60px rgba(0,0,0,0.35))
- Status indicators with ::before pseudo-elements creating colored dots
- Muted labels with uppercase + letter-spacing
- Pipeline bars with gradient fills
- KPI cards in a grid as the default dashboard layout
- "Team focus" or "Recent activity" panels with decorative internal copy
- Tables with tag badges for every status
- Workspace blocks in sidebar with call-to-action buttons
- Brand marks with gradient backgrounds
- Nav badges showing counts or "Live" status
- Quota/usage panels with progress bars
- Footer lines with meta information
- Trend indicators with colored text
- Rail panels on the right side
- Multiple nested panel types

## Rule

If a UI choice feels like a default AI UI move, ban it and pick the harder, cleaner option.
- Colors should stay calm, not fight.
- Use existing project colors first. If none, use one of the predefined palettes below. Do not invent random color combinations.

## Color Palettes

### Dark
| Palette | Background | Surface | Primary | Secondary | Accent | Text |
|--------|-----------|--------|--------|----------|--------|------|
| Midnight Canvas | #0a0e27 | #151b3d | #6c8eff | #a78bfa | #f472b6 | #e2e8f0 |
| Obsidian Depth | #0f0f0f | #1a1a1a | #00d4aa | #00a3cc | #ff6b9d | #f5f5f5 |
| Slate Noir | #0f172a | #1e293b | #38bdf8 | #818cf8 | #fb923c | #f1f5f9 |
| Carbon Elegance | #121212 | #1e1e1e | #bb86fc | #03dac6 | #cf6679 | #e1e1e1 |
| Charcoal Studio | #1c1c1e | #2c2c2e | #0a84ff | #5e5ce6 | #ff375f | #f2f2f7 |
| Void Space | #0d1117 | #161b22 | #58a6ff | #79c0ff | #f78166 | #c9d1d9 |

### Light
| Palette | Background | Surface | Primary | Secondary | Accent | Text |
|--------|-----------|--------|--------|----------|--------|------|
| Cloud Canvas | #fafafa | #ffffff | #2563eb | #7c3aed | #dc2626 | #0f172a |
| Pearl Minimal | #f8f9fa | #ffffff | #0066cc | #6610f2 | #ff6b35 | #212529 |
| Ivory Studio | #f5f5f4 | #fafaf9 | #0891b2 | #06b6d4 | #f59e0b | #1c1917 |
| Porcelain Clean | #f9fafb | #ffffff | #4f46e5 | #8b5cf6 | #ec4899 | #111827 |
| Frost Bright | #f1f5f9 | #f8fafc | #0f766e | #14b8a6 | #e11d48 | #0f172a |
