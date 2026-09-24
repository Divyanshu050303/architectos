# ArchitectOS design system: implementation reference

This page documents the design system as it is **implemented** in `apps/web`. The product rules live in [`design-system.md`](./design-system.md) (§11–18, §62, §96–102, §121–124), and this page does not restate them. Where the code departs from the starting values in the spec, the change and the reason are recorded here.

Visual reference: Storybook (`npm run storybook`). Stories cover UI/Button, UI/Dialog, UI/Badge, Feedback/*, Architecture/Node, Architecture/Inspector, Validation/FindingCard and Capacity/MetricCard, with a light/dark toolbar toggle.

## Where it lives

| Concern                                                                                              | File                                                                             |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Colour, shadow and canvas tokens (light + `.dark`)                                                   | `apps/web/styles/tokens.css`                                                     |
| Token → Tailwind utility mapping, radius, fonts, `tabular`, `label-caps`, focus ring, reduced motion | `apps/web/styles/globals.css`                                                    |
| React Flow chrome and node highlight animation                                                       | `apps/web/styles/architecture.css`                                               |
| Fonts (Inter, JetBrains Mono through `next/font`)                                                    | `apps/web/app/layout.tsx`                                                        |
| Theme preference, pre-hydration script                                                               | `apps/web/providers/theme-provider.tsx`, `components/navigation/ThemeToggle.tsx` |
| Primitives                                                                                           | `apps/web/components/ui/*`                                                       |
| Feedback: empty, error, loading steps, page header, provenance tag                                   | `apps/web/components/feedback/*`                                                 |
| Architecture node + states                                                                           | `apps/web/features/architecture/components/ArchitectureNode.tsx`                 |
| Component-type icons                                                                                 | `apps/web/features/architecture/constants.ts`                                    |
| Finding severity (icon + text + colour)                                                              | `apps/web/features/validation/severity.tsx`                                      |

Rule (§96–97): components use semantic utilities (`bg-surface`, `text-muted`, `border-default`, `text-warning-fg`). They never use raw hex values or `text-[#…]`.

## Colour

### Tokens

| Token                         | Tailwind utility                           | Light                             | Dark                              | Use                                                             |
| ----------------------------- | ------------------------------------------ | --------------------------------- | --------------------------------- | --------------------------------------------------------------- |
| `--background`                | `bg-background`                            | `#f7f8f6`                         | `#0b0e0d`                         | Page and canvas background                                      |
| `--surface`                   | `bg-surface`                               | `#ffffff`                         | `#111513`                         | Cards, panels, nodes, dialogs                                   |
| `--surface-secondary`         | `bg-surface-2`                             | `#f1f3f0`                         | `#171c19`                         | Hover fills, evidence panels, alternating bands                 |
| `--surface-sunken`            | `bg-sunken`                                | `#eceee9`                         | `#0e1210`                         | Inset wells, evidence tag                                       |
| `--border`                    | `border-default`                           | `#e2e5e1`                         | `#252c28`                         | Default separators (also the global `*` border colour)          |
| `--border-strong`             | `border-strong`                            | `#cfd4cf`                         | `#333c37`                         | Secondary buttons, diagram boxes, dashed placeholders           |
| `--border-control`            | `border-control`, `bg-control`             | `#8f978f`                         | `#5d6761`                         | Form-control outlines, edges, neutral status dot (3:1 non-text) |
| `--text-primary`              | `text-fg`                                  | `#171a18`                         | `#e8ece9`                         | Primary text, values                                            |
| `--text-secondary`            | `text-fg-secondary`                        | `#4f5752`                         | `#aab2ad`                         | Body copy, labels                                               |
| `--text-muted`                | `text-muted`                               | `#666e68`                         | `#7e8882`                         | Metadata, captions, `label-caps`                                |
| `--accent`                    | `bg-accent`                                | `#63d7a1`                         | `#63d7a1`                         | Primary button fill, selected/AI fills                          |
| `--accent-strong`             | `bg-accent-strong`, `border-accent-strong` | `#35bd7d`                         | `#4cc991`                         | Hover, selection border, healthy meter fill                     |
| `--accent-soft`               | `bg-accent-soft`                           | `#e5f8ee`                         | `#10271c`                         | AI proposal and selected backgrounds                            |
| `--accent-fg`                 | `text-accent-fg`                           | `#1b7a4f`                         | `#63d7a1`                         | Mint **text and icons**                                         |
| `--on-accent`                 | `text-on-accent`                           | `#0b2a1c`                         | `#0b2a1c`                         | Text on an accent fill                                          |
| `--warning` / `-soft` / `-fg` | `bg-warning` … `text-warning-fg`           | `#d99b35` / `#fff4dc` / `#8a5a10` | `#d99b35` / `#2a2112` / `#e6b566` | Warnings, high/medium findings                                  |
| `--danger` / `-soft` / `-fg`  | `bg-danger` … `text-danger-fg`             | `#d95757` / `#fdeaea` / `#b23a3a` | `#d95757` / `#2c1616` / `#f08a8a` | Critical findings, destructive actions                          |
| `--info` / `-soft` / `-fg`    | `bg-info` … `text-info-fg`                 | `#5c8edb` / `#eaf2ff` / `#2d62b0` | `#5c8edb` / `#131d2c` / `#8fb3ec` | Deterministic calculations, "Why?" links, loading               |
| `--ring`                      | `ring-ring`, `outline-ring`                | `#35bd7d`                         | `#63d7a1`                         | Focus outline (2px, 2px offset)                                 |
| `--overlay`                   | `bg-overlay`                               | `rgb(11 14 13 / .32)`             | `rgb(0 0 0 / .55)`                | Dialog and drawer scrim                                         |
| `--canvas-grid`               | (React Flow `Background`)                  | `#e3e6e2`                         | `#1b211e`                         | Canvas dot grid                                                 |

**Departures from the spec's starting tokens (§13).** The spec says to validate contrast before finalising, and did not include:

- `--text-muted` was darkened from `#747c76` to `#666e68` so it passes 4.5:1 on `--surface-secondary`.
- The spec's `accent`/`warning`/`danger`/`info` values fail AA as text on light surfaces (1.8–3.9:1). They are kept as **fills**, and each one has a `*-fg` companion for text and icons. **Rule: coloured text always uses `text-*-fg`, never `text-accent` or `text-warning`.**
- Added `--surface-sunken`, `--border-control`, `--on-accent`, `--ring`, `--overlay` and `--canvas-grid`. Dark mode is a separately tuned palette, not an inversion (§14).

### Measured contrast (WCAG 2.1)

These ratios were computed from `tokens.css` with the WCAG relative-luminance formula (script: parse both blocks, then compute `(L1+.05)/(L2+.05)`). Text needs ≥ 4.5; non-text UI needs ≥ 3.

| Foreground                       | Background            | Light | Dark  |
| -------------------------------- | --------------------- | ----- | ----- |
| `--text-primary`                 | `--background`        | 16.46 | 16.26 |
| `--text-primary`                 | `--surface`           | 17.54 | 15.44 |
| `--text-secondary`               | `--surface`           | 7.45  | 8.49  |
| `--text-secondary`               | `--surface-secondary` | 6.68  | 7.96  |
| `--text-muted`                   | `--surface`           | 5.26  | 5.03  |
| `--text-muted`                   | `--surface-secondary` | 4.71  | 4.71  |
| `--text-muted`                   | `--background`        | 4.93  | 5.29  |
| `--accent-fg`                    | `--surface`           | 5.33  | 10.32 |
| `--accent-fg`                    | `--accent-soft`       | 4.82  | 8.85  |
| `--warning-fg`                   | `--surface`           | 5.91  | 9.79  |
| `--warning-fg`                   | `--warning-soft`      | 5.41  | 8.43  |
| `--danger-fg`                    | `--surface`           | 5.90  | 7.63  |
| `--danger-fg`                    | `--danger-soft`       | 5.09  | 7.06  |
| `--info-fg`                      | `--surface`           | 6.02  | 8.62  |
| `--info-fg`                      | `--info-soft`         | 5.35  | 7.93  |
| `--on-accent`                    | `--accent`            | 8.64  | 8.64  |
| `--on-accent`                    | `--accent-strong`     | 6.41  | 7.39  |
| `--border-control` (non-text)    | `--surface`           | 3.00  | 3.14  |
| `--accent` (fill, **not text**)  | `--surface`           | 1.78  | 10.32 |
| `--warning` (fill, **not text**) | `--surface`           | 2.42  | 7.62  |
| `--danger` (fill, **not text**)  | `--surface`           | 3.85  | 4.78  |
| `--info` (fill, **not text**)    | `--surface`           | 3.31  | 5.57  |
| `--border-strong` (decorative)   | `--surface`           | 1.50  | 1.62  |

The bottom rows show why fills never carry text. `--border-strong` is decorative only; meaningful control outlines use `--border-control`.

### Use of mint (§12)

Mint marks primary actions, selection, healthy state, AI proposals and active navigation. A screen should show only a few mint elements. Body text, headings and backgrounds stay neutral.

## Typography (§15)

| Role           | Implementation                                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI font        | Inter via `next/font` → `--font-inter` → `font-sans`. `body` is 14px/20px (`text-sm`)                                                                                                       |
| Technical font | JetBrains Mono → `--font-jetbrains-mono` → `font-mono`                                                                                                                                      |
| `.tabular`     | Mono + `tabular-nums` + slashed zero. **Use for every metric, RPS, %, ID, rule ID, config value, version**                                                                                  |
| `.label-caps`  | 11px/16px, weight 600, `0.06em` tracking, uppercase, `text-muted`. Used for section labels, card titles and node categories                                                                 |
| Scale          | `text-2xs` (11px, custom), `text-xs` 12, `text-sm` 14 (default), `text-base` 16, `text-lg` 18. App page titles are `text-lg`. Marketing headings go up to `text-4xl`/`5xl` in the hero only |

Metric values combine the two: for example, `<span class="tabular text-2xl font-semibold">2.4M</span>` followed by a sans unit in `text-muted`.

## Spacing (§16)

Tailwind's 4px base covers the spec scale: `1`=4, `2`=8, `3`=12, `4`=16, `5`=20, `6`=24, `8`=32, `10`=40, `12`=48, `16`=64, `20`=80. The half steps `0.5`/`1.5`/`2.5` (2/6/10px) are allowed only inside dense controls such as badges, node internals and small buttons. Arbitrary `p-[17px]` values are not used.

Typical densities: card padding `px-4 py-3`, panel section gap `gap-4`, node padding `px-3 py-2.5`, marketing sections `py-16 sm:py-20`.

## Radius (§17)

| Utility        | Value | Use                                                 |
| -------------- | ----- | --------------------------------------------------- |
| `rounded-sm`   | 6px   | Buttons, inputs, icon buttons, provenance tags      |
| `rounded-md`   | 8px   | Cards, nodes, menus, alerts                         |
| `rounded-lg`   | 12px  | Dialogs, large panels, mock frames                  |
| `rounded-xl`   | 16px  | Bottom-sheet drawer top corners                     |
| `rounded-full` | pill  | **Status and tags only** (Badge, status dot, meter) |

## Shadows (§18)

| Utility         | Value (light)                       | Use                                                     |
| --------------- | ----------------------------------- | ------------------------------------------------------- |
| `shadow-subtle` | `0 1px 2px rgb(23 26 24/.05)`       | Nodes, framed panels                                    |
| `shadow-raised` | `0 4px 16px …/.08, 0 1px 2px …/.06` | Floating layers only: dialogs, drawers, menus, tooltips |

Dark mode uses stronger alpha values on black. Most separation comes from a background step plus a border, not from shadow.

## Buttons (`components/ui/button.tsx`, `icon-button.tsx`)

| Variant               | Look                                                                 | Use                                                               |
| --------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `primary`             | Mint fill, `on-accent` text                                          | One per view: Apply, Create, Start designing                      |
| `secondary` (default) | Surface, strong border                                               | Most actions                                                      |
| `ghost`               | Transparent, secondary text                                          | Toolbars, card actions (Explain, Locate, Ignore)                  |
| `danger`              | Surface, `danger-fg` text, danger-soft hover                         | Destructive actions                                               |
| `ai`                  | `accent-soft` fill, mint border, `accent-fg` text, prefixed with `✦` | Actions that ask the AI (Fix, Review change). No gradients (§102) |

Sizes: `sm` is 28px and `md` is 32px. Marketing CTAs use `md` with `h-10`. `loading` shows a spinner and sets `aria-busy`. `asChild` renders a `Link`. `IconButton` requires `label` (used for both `aria-label` and the tooltip) and accepts an optional `shortcut`. It supports an `active` state.

## Cards (`components/ui/card.tsx`)

A card is `rounded-md border border-default bg-surface`, with a `CardHeader` (bottom border, `px-4 py-3`), a `CardTitle` (`h2.label-caps`) and `CardContent` (`p-4`). There is no shadow. `MetricCard` renders a `dt`/`dd` pair, so it must sit inside a `<dl>`.

## Forms (`components/ui/input.tsx`)

`Input`, `Textarea` and `Select` share one control style: a 32px height, `border-strong`, `border-control` on hover, and a mint border plus a 2px `accent/30` ring on focus. When `aria-invalid="true"`, the border turns danger. `Field` wires `label[for]`, the description and the error through `aria-describedby`. Errors use `text-danger-fg`. Always use `Field` rather than a hand-made `<label>`.

## Dialogs and drawers

|       | Dialog (`dialog.tsx`)                               | Drawer (`drawer.tsx`)                                                              |
| ----- | --------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Shape | Centred, max-w-lg, `rounded-lg`, `shadow-raised`    | Right sheet (max-w-md), or a bottom sheet with `rounded-t-xl` on mobile            |
| Scrim | `bg-overlay`                                        | `bg-overlay`                                                                       |
| Title | `text-sm font-semibold`, optional muted description | `label-caps` title                                                                 |
| Tone  | —                                                   | `tone="evidence"` uses `bg-surface-2` (the trust model's "subtle secondary panel") |

Both are built on Radix Dialog, so they get focus trapping, `Esc` to close and focus return. `DialogFooter` right-aligns actions: Cancel is ghost, the confirming action is primary.

## Architecture nodes and graph states (`ArchitectureNode.tsx`)

A node is a 224×112 card (`NODE_WIDTH`/`NODE_HEIGHT`) with a subtle Lucide icon and a `label-caps` category, a name and technology, a tabular key metric, and a status dot plus text. In capacity mode it also shows a `Meter`. Badges sit top-right. State is exposed through `data-status`, `data-selected`, `data-highlighted`, `data-dimmed` and `data-preview` for tests and styling.

| State                             | Treatment                                                                                              |
| --------------------------------- | ------------------------------------------------------------------------------------------------------ |
| default                           | `border-default`, grey dot, "Not analyzed"                                                             |
| healthy                           | Mint dot + `accent-fg` text, accent meter                                                              |
| warning                           | `warning/60` border, amber dot + `warning-fg` text, amber meter                                        |
| critical                          | `danger/70` border, red dot + `danger-fg` text, red meter                                              |
| selected                          | `border-accent-strong` + `ring-2 ring-accent/25`, sr-only "Selected"                                   |
| highlighted (Locate / simulation) | Warning border + `arch-pulse` (2 pulses, neutralised under reduced motion)                             |
| dimmed                            | `opacity-40` (other nodes while something is highlighted)                                              |
| loading / simulating              | Info dot with `motion-safe:animate-pulse`; simulating adds an info border                              |
| disabled                          | `opacity-50`                                                                                           |
| AI preview: added / updated       | Dashed `accent-strong` border, "✦ Proposed" / "✦ Changed" badge; added nodes also get `bg-accent-soft` |
| AI preview: removed               | Dashed danger border, struck-through name, `opacity-60`                                                |
| Badges                            | `Bottleneck` (capacity mode, warning/danger), `SPOF` (reliability mode)                                |

Edges use `--border-control` at 1.25px (2px when selected). Critical edges use `--danger`, and dimmed edges drop to opacity 0.25 (`architecture.css`). Keyboard focus shows the ring on the card, and the node's accessible name comes from `describeNode()`.

## Status states: icon + text + colour (§62)

| Component                                   | Icon                                                                | Text                                             | Colour                                        |
| ------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------- |
| `StatusBadge` (`badge.tsx`)                 | CheckCircle2 / AlertTriangle / OctagonAlert / CircleDashed          | Healthy / Warning / Critical / Not analyzed      | accent / warning / danger / neutral           |
| `SeverityBadge` (`validation/severity.tsx`) | OctagonAlert / TriangleAlert / CircleAlert / CircleArrowDown / Info | Critical … Info                                  | danger → neutral                              |
| Node status                                 | Dot (decorative)                                                    | Always a label, with an sr-only "Status:" prefix | Per state                                     |
| `Meter`                                     | —                                                                   | `aria-label` includes resource + %               | Tone matches status; optional threshold tick  |
| `Alert`                                     | Icon per tone                                                       | Title + body                                     | `role="alert"` for danger, otherwise `status` |

Colour is never the only signal.

## Provenance and trust model (§123–124, `ProvenanceTag.tsx`)

| Kind         | Label       | Treatment                                            | Meaning                        |
| ------------ | ----------- | ---------------------------------------------------- | ------------------------------ |
| `fact`       | User data   | Neutral (`surface-2`, `fg-secondary`), FileText icon | What the system **is**         |
| `ai`         | AI proposal | Mint (`accent-soft`, `accent-fg`), **✦ mark**        | What the system **thinks**     |
| `calculated` | Calculated  | Blue (`info-soft`, `info-fg`), Calculator icon       | What the system **calculated** |
| `finding`    | Finding     | Amber, AlertTriangle                                 | Validator output               |
| `evidence`   | Evidence    | `sunken` panel, FileSearch                           | The inputs behind a claim      |

Tags are `rounded-sm` rectangles (not pills), so they read as provenance rather than status. The ✦ mark is the only AI signature: no sparkles, glows or gradients (§102). Illustrative numbers, such as the marketing demo, carry a neutral "Illustrative model" tag and never use the `calculated` treatment.

## Animation and reduced motion (§100–101)

The system has only a handful of motion primitives:

| Motion                                                      | Where                                                            |
| ----------------------------------------------------------- | ---------------------------------------------------------------- |
| `transition-colors`                                         | Buttons, links, icon buttons                                     |
| `transition-[border-color,box-shadow,opacity] duration-150` | Node state changes                                               |
| `arch-pulse` (1.6s × 2)                                     | Highlighted node after Locate                                    |
| `animate-pulse`                                             | Loading/simulating dots and skeletons, gated with `motion-safe:` |
| `animate-spin`                                              | Button loading spinner                                           |

`globals.css` sets animation and transition durations to 0.01ms and single iterations under `prefers-reduced-motion: reduce`, so state changes still happen but nothing moves. Cards, hovers, metrics and ordinary nodes are not animated.

## Accessibility rules (§62)

- Contrast is measured, not assumed (see the table above). Text uses `fg`, `fg-secondary`, `muted` or `*-fg`.
- `:focus-visible` shows a 2px `--ring` outline with a 2px offset. Custom controls (segmented radios, switches) forward it with `peer-focus-visible:`.
- Every icon-only control has a label (`IconButton` enforces this). Decorative icons carry `aria-hidden`.
- Semantic structure: landmarks (`header`, `nav[aria-label]`, `main`, `footer`), one `h1` per page, headings in order, `section[aria-labelledby]`, `dl` for label/value pairs, and `table` with `th[scope]`.
- Disabled actions that need an explanation stay focusable and give the reason in a tooltip (FindingCard).
- Status changes that users trigger are announced: the marketing demo summary is `aria-live="polite"`.
- Axe `wcag2a/aa` runs in `tests/e2e/golden-path.spec.ts`. The marketing pages pass it in light and dark at 1440px and 390px.
- Layouts work down to 360px with no horizontal scroll.

## Brand

Source artwork: the official logo files (mark with transparent background; horizontal and stacked lockups on white).

| Asset | Location | Use |
| --- | --- | --- |
| Mark master (1024, transparent) | `apps/web/public/brand/logo-mark.png` | Source for every icon. |
| Mark for UI (256) | `apps/web/public/brand/logo-mark-256.png` | `LogoMark` / `Wordmark` in `apps/web/components/brand/Logo.tsx`. |
| Horizontal / stacked lockups | `apps/web/public/brand/logo-horizontal.png`, `logo-stacked.png` | Light backgrounds only (they have a white background): docs, README, decks. |
| Open Graph image | `apps/web/app/opengraph-image.png` (1200×630) | Link previews. |
| Favicon | `apps/web/app/favicon.ico` (16/32/48), `apps/web/app/icon.png` | Browser tabs. |
| Apple touch icon | `apps/web/app/apple-icon.png` (180, full-bleed) | iOS home screen. |
| App icons | `apps/web/public/brand/app-icon-1024.png`, `app-icon-dark-1024.png` | Stores / marketing. |
| Web manifest icons | `apps/web/public/brand/icon-192.png`, `icon-512.png`, `icon-maskable-512.png` via `apps/web/app/manifest.ts` | Installed PWA. |

- In the UI the wordmark is the mark + live text ("Architect" in `text-fg`, "OS" in `text-accent-fg`), so it adapts to light and dark themes. The lockup PNGs are not used inside the app.
- The mark is used flat in the product UI: no glow or drop shadow (spec §11, §18, §102).
- Tagline: "Design today. Scale tomorrow." (`Wordmark tagline`).
