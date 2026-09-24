# ArchitectOS

## Frontend Engineering, Design System & Code Generation Specification

**Purpose:** Master instruction/specification for implementing the ArchitectOS frontend with an AI coding agent such as Claude.

**Frontend Stack**

* Next.js
* TypeScript
* React
* Tailwind CSS
* React Flow
* TanStack Query
* Zustand
* Zod
* Vitest
* React Testing Library
* Playwright
* Storybook
* ESLint
* Prettier

---

# 1. Frontend Mission

ArchitectOS is not a conventional SaaS dashboard.

The frontend represents a **living software architecture model**.

The primary user experience is:

```text
Requirements
      ↓
Architecture
      ↓
Inspect
      ↓
Validate
      ↓
Calculate
      ↓
Simulate
      ↓
Operate
      ↓
Scale
      ↓
Evolve
      ↓
Migrate
```

The UI must make this lifecycle understandable without overwhelming the user.

The frontend should feel like:

> **Linear + Figma + Vercel + Datadog**

with the visual language of a serious engineering product.

It must NOT feel like:

* a generic admin dashboard
* a ChatGPT clone
* a Miro clone
* a colorful startup landing page
* an AI wrapper
* a diagramming toy

---

# 2. Primary UX Principle

The architecture canvas is the center of the product.

Everything else is contextual.

```text
                 ARCHITECTURE
                      │
       ┌──────────────┼──────────────┐
       │              │              │
    Capacity       Reliability     Security
       │              │              │
       └──────────────┼──────────────┘
                      │
                 Evolution
                      │
                 Migration
```

Do not build a dashboard where the architecture is just one page.

The architecture is the product.

---

# 3. Core Product Surfaces

The frontend must eventually support:

```text
1. Dashboard
2. Project
3. Requirements
4. Architecture Workspace
5. Component Inspector
6. Capacity Analysis
7. Validation
8. Simulation
9. Reliability
10. Security
11. Observability
12. Cost
13. Architecture Evolution
14. Migration
15. Brownfield Discovery
16. Architecture Drift
17. ADRs
18. Evidence
19. Settings
```

---

# 4. V1 Frontend Scope

Do not implement the entire product initially.

V1 must contain:

```text
Dashboard
Project creation
Requirements
Architecture workspace
Component inspector
Architecture canvas
Architecture command bar
Capacity analysis
Validation
Operating envelope
Architecture report
ADR view
```

V2 and later:

```text
Simulation
Reliability
Security
Observability
Cost
Evolution
Migration
Discovery
Drift
```

---

# 5. Technology Architecture

Recommended structure:

```text
apps/web/

├── app/
│
├── components/
│
├── features/
│
├── lib/
│
├── hooks/
│
├── stores/
│
├── types/
│
├── schemas/
│
├── api/
│
├── providers/
│
├── config/
│
├── styles/
│
└── tests/
```

Do not put the entire application into:

```text
components/
```

Use feature-oriented organization.

---

# 6. Recommended Folder Structure

```text
apps/web/

├── app/
│   ├── layout.tsx
│   ├── page.tsx
│   │
│   ├── dashboard/
│   │   └── page.tsx
│   │
│   ├── projects/
│   │   └── page.tsx
│   │
│   └── project/
│       └── [projectId]/
│           ├── layout.tsx
│           ├── page.tsx
│           │
│           ├── requirements/
│           │   └── page.tsx
│           │
│           ├── architecture/
│           │   └── page.tsx
│           │
│           ├── capacity/
│           │   └── page.tsx
│           │
│           ├── validation/
│           │   └── page.tsx
│           │
│           ├── simulation/
│           │   └── page.tsx
│           │
│           ├── evolution/
│           │   └── page.tsx
│           │
│           ├── migration/
│           │   └── page.tsx
│           │
│           └── settings/
│               └── page.tsx
│
├── components/
│   ├── ui/
│   ├── layout/
│   ├── navigation/
│   ├── command/
│   └── feedback/
│
├── features/
│   ├── architecture/
│   ├── requirements/
│   ├── capacity/
│   ├── validation/
│   ├── simulation/
│   ├── evolution/
│   ├── migration/
│   ├── health/
│   ├── evidence/
│   └── projects/
│
├── api/
│   ├── client.ts
│   ├── projects.ts
│   ├── architectures.ts
│   ├── requirements.ts
│   ├── capacity.ts
│   ├── validation.ts
│   └── simulations.ts
│
├── stores/
│   ├── architecture-store.ts
│   ├── workspace-store.ts
│   ├── ui-store.ts
│   └── command-store.ts
│
├── hooks/
│   ├── use-architecture.ts
│   ├── use-capacity.ts
│   ├── use-validation.ts
│   └── use-command.ts
│
├── schemas/
│   ├── architecture.ts
│   ├── requirements.ts
│   └── api.ts
│
├── types/
│   ├── architecture.ts
│   ├── component.ts
│   ├── validation.ts
│   └── capacity.ts
│
├── lib/
│   ├── utils.ts
│   ├── formatting.ts
│   ├── graph.ts
│   └── keyboard.ts
│
├── providers/
│   ├── query-provider.tsx
│   └── theme-provider.tsx
│
└── styles/
    ├── globals.css
    ├── tokens.css
    └── architecture.css
```

---

# 7. Architecture Feature Structure

The architecture feature should be isolated.

```text
features/architecture/

├── components/
│   ├── ArchitectureCanvas.tsx
│   ├── ArchitectureNode.tsx
│   ├── ArchitectureEdge.tsx
│   ├── ArchitectureToolbar.tsx
│   ├── ArchitectureMiniMap.tsx
│   ├── ArchitectureInspector.tsx
│   ├── NodeInspector.tsx
│   ├── NodeToolbar.tsx
│   ├── ConnectionEditor.tsx
│   └── CanvasEmptyState.tsx
│
├── hooks/
│   ├── useArchitectureCanvas.ts
│   ├── useNodeSelection.ts
│   ├── useArchitectureHistory.ts
│   └── useArchitectureCommands.ts
│
├── utils/
│   ├── graph-layout.ts
│   ├── node-transform.ts
│   └── edge-transform.ts
│
├── types.ts
└── constants.ts
```

---

# 8. Architecture IR Boundary

The frontend must not invent its own architecture model.

Backend Architecture IR is the source of truth.

Frontend receives:

```typescript
interface Architecture {
  id: string;
  version: number;
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
  assumptions: Assumption[];
}
```

Example:

```typescript
interface ArchitectureNode {
  id: string;
  type: ComponentType;
  name: string;
  technology: string;
  configuration: Record<string, unknown>;
  position: {
    x: number;
    y: number;
  };
}
```

---

# 9. Never Mix API Models With UI Models

Use adapters.

```text
API response
     ↓
API model
     ↓
Adapter
     ↓
UI model
     ↓
React Flow
```

Example:

```typescript
const nodes = architecture.nodes.map(toReactFlowNode);
```

Do not make the backend API response itself the React Flow object.

This prevents vendor/library coupling.

---

# 10. React Flow Rule

React Flow is an implementation detail.

Do not let React Flow types leak throughout the application.

Bad:

```typescript
function updateNode(node: ReactFlowNode) {}
```

Good:

```typescript
function updateArchitectureNode(
  node: ArchitectureNode
) {}
```

The architecture domain should remain independent.

---

# 11. Design Philosophy

Use:

```text
Minimal
Technical
Dense
Calm
Precise
Elegant
```

Avoid:

```text
Excessive gradients
Large rounded cards
Heavy shadows
Huge typography everywhere
Unnecessary animations
Excessive colors
AI sparkle effects
Dashboard clutter
```

---

# 12. Visual Identity

ArchitectOS should use a restrained mint-based design system.

Primary accent:

```text
Mint / Emerald
```

Use mint for:

```text
Primary actions
Selected state
Healthy state
AI-generated state
Active navigation
Positive system signals
```

Do not make the entire interface green.

---

# 13. Color Tokens

Initial design tokens:

```css
:root {
  --background: #f7f8f6;
  --surface: #ffffff;
  --surface-secondary: #f1f3f0;

  --border: #e2e5e1;
  --border-strong: #cfd4cf;

  --text-primary: #171a18;
  --text-secondary: #4f5752;
  --text-muted: #747c76;

  --accent: #63d7a1;
  --accent-strong: #35bd7d;
  --accent-soft: #e5f8ee;

  --warning: #d99b35;
  --warning-soft: #fff4dc;

  --danger: #d95757;
  --danger-soft: #fdeaea;

  --info: #5c8edb;
  --info-soft: #eaf2ff;
}
```

These are starting tokens, not immutable branding.

Validate contrast before finalizing.

---

# 14. Dark Mode

Dark mode must be a first-class design system.

```css
.dark {
  --background: #0b0e0d;
  --surface: #111513;
  --surface-secondary: #171c19;

  --border: #252c28;

  --text-primary: #e8ece9;
  --text-secondary: #aab2ad;
  --text-muted: #7e8882;

  --accent: #63d7a1;
}
```

Do not implement dark mode by simply inverting colors.

---

# 15. Typography

Use:

```text
Inter
```

for UI.

Use:

```text
JetBrains Mono
```

for:

```text
Metrics
RPS
CPU
Latency
Configuration
IDs
Code
Technical values
```

Typography should prioritize readability over visual novelty.

---

# 16. Spacing

Use a consistent spacing scale.

```text
4
8
12
16
20
24
32
40
48
64
80
```

Do not randomly use:

```text
13px
17px
23px
31px
```

unless there is a specific reason.

---

# 17. Border Radius

Keep it restrained.

```text
Small controls: 6px
Cards: 8px
Dialogs: 12px
Large surfaces: 12–16px
```

Avoid excessive pill-shaped UI.

Pills should primarily communicate:

```text
Status
Tags
Filters
```

---

# 18. Shadows

Use subtle shadows only.

Most surfaces should be separated using:

```text
background contrast
+
border
```

rather than heavy shadows.

---

# 19. Main Application Layout

Desktop-first architecture workspace:

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Navigation                                               │
├─────────────┬───────────────────────────────────┬────────────┤
│             │                                   │            │
│ Sidebar     │                                   │ Inspector  │
│             │          Canvas                   │            │
│             │                                   │            │
│             │                                   │            │
│             │                                   │            │
├─────────────┴───────────────────────────────────┴────────────┤
│                 Command Bar                                  │
└──────────────────────────────────────────────────────────────┘
```

---

# 20. Top Navigation

Top navigation:

```text
ArchitectOS

Project Name

Architecture Version

⌘K

Share

Export

User
```

Keep it compact.

Do not turn it into a traditional SaaS navbar.

---

# 21. Sidebar

Navigation:

```text
PROJECT

Overview

DESIGN
Requirements
Architecture

ANALYZE
Capacity
Validation
Simulation

OPERATE
Reliability
Security
Observability
Cost

EVOLVE
Evolution
Migration

DISCOVER
Infrastructure
Drift

DOCUMENT
Decisions
Evidence
Reports
```

Use grouped navigation.

---

# 22. Architecture Canvas

The canvas should occupy most of the viewport.

Use:

* React Flow
* zoom
* pan
* minimap
* grid
* keyboard shortcuts
* selection
* multi-selection
* edge creation
* node positioning

Canvas controls:

```text
Zoom +
Zoom -
Fit
Grid
Fullscreen
Undo
Redo
```

---

# 23. Canvas Background

Use an extremely subtle grid.

Avoid:

```text
large dots
strong lines
heavy colors
```

The architecture nodes should remain visually dominant.

---

# 24. Architecture Node

Every node has:

```text
Category
Icon
Technology
Name
Health
Key metric
```

Example:

```text
┌──────────────────────────────┐
│ DATABASE                     │
│                              │
│ ◉ PostgreSQL                 │
│ Primary Store                │
│                              │
│ 42% CPU                      │
│ ● Healthy                    │
└──────────────────────────────┘
```

---

# 25. Node States

Every node must support:

```text
default
hover
selected
focused
warning
critical
healthy
disabled
loading
simulating
```

These states should be represented visually.

---

# 26. Selected Node

When selected:

```text
accent border
subtle accent glow
inspector opens
context toolbar appears
```

Do not use huge glowing borders.

---

# 27. Node Context Toolbar

On selection:

```text
+ Add connection
Configure
Duplicate
Explain
Simulate failure
View constraints
Delete
```

This should be contextual.

---

# 28. Component Inspector

The right panel should be contextual.

Example:

```text
POSTGRESQL

Primary datastore

────────────────────

Configuration

CPU
4 vCPU

Memory
16 GB

Storage
500 GB

Replicas
2

────────────────────

Capacity

Reads
8.2K / 12K

Writes
2.1K / 4K

Connections
184 / 500

────────────────────

Status

● Healthy
```

---

# 29. Inspector Tabs

For each component:

```text
Overview
Configuration
Capacity
Constraints
Failure Modes
Security
Observability
Cost
Evidence
```

Only show relevant tabs.

---

# 30. Architecture Command Bar

This is a signature interaction.

Bottom of workspace:

```text
┌──────────────────────────────────────────────────────────────┐
│ ✦ Ask ArchitectOS                                           │
│                                                              │
│ Describe an architecture change...                           │
│                                                              │
│                                           ⌘ Enter            │
└──────────────────────────────────────────────────────────────┘
```

Placeholder examples:

```text
Add Redis caching.

What breaks at 10M users?

Simulate PostgreSQL failure.

Reduce infrastructure cost.

Explain this architecture.

Add a queue between these services.
```

---

# 31. AI Must Propose Changes

Never directly mutate the architecture from an AI response.

Flow:

```text
User command
     ↓
AI proposal
     ↓
Architecture diff
     ↓
Impact analysis
     ↓
User approval
     ↓
Apply
```

---

# 32. Architecture Diff

Example:

```text
ARCHITECTURE CHANGE

+ Redis

~ API → Redis

~ Redis → PostgreSQL

Database reads
18K → 6K/sec

Estimated cost
$210 → $247/month

[Apply]
[Edit]
[Reject]
```

This is mandatory for AI-driven mutations.

---

# 33. AI Response Design

Do not build a ChatGPT-style endless chat window.

AI responses should be structured around:

```text
Recommendation
Reason
Impact
Evidence
Changes
Actions
```

Example:

```text
RECOMMENDATION

Introduce Redis.

WHY

PostgreSQL read utilization exceeds the configured threshold.

IMPACT

DB reads:
18K → 6K/sec

COST

+$37/month

EVIDENCE

3 calculations
2 component constraints

[Review Change]
```

---

# 34. Evidence Drawer

Every important AI claim should support:

```text
Why?
```

Clicking opens:

```text
EVIDENCE

Claim

PostgreSQL is approaching connection capacity.

Calculation

Expected connections:
438

Configured maximum:
500

Warning threshold:
350

Source

Capacity model CM-182

Assumptions

A-001
A-007
```

---

# 35. Capacity View

Create a dedicated capacity page.

```text
CURRENT LOAD

2.4M DAU
31K Peak RPS
8.2K Writes/sec

────────────────────────

SYSTEM UTILIZATION

API             61%
Redis           42%
PostgreSQL      82% ⚠
Kafka            31%

────────────────────────

NEXT BOTTLENECK

PostgreSQL connections

Expected threshold:
~7.8M DAU
```

---

# 36. Operating Envelope Chart

Use a visual chart.

```text
DAU

10M ┤                          ×
 8M ┤                     ⚠
 5M ┤                ● CURRENT
 1M ┤          ×
100K┤     ×
    └────────────────────────────
       V1     V2      V3
```

Use clear semantic markers.

---

# 37. System Health

Health must be explainable.

```text
SYSTEM HEALTH

87

Capacity       91
Reliability    84
Security       93
Observability  72
Cost           81
```

Clicking each category opens underlying findings.

Never present an arbitrary score without explanation.

---

# 38. Validation View

Findings should be grouped by severity.

```text
CRITICAL
1 finding

HIGH
2 findings

MEDIUM
4 findings

LOW
3 findings
```

Finding:

```text
⚠ Missing timeout

API → Payment Service

Why it matters

External dependency can block request workers.

Recommendation

Add timeout <= configured SLA.

[Fix]
[Explain]
[Ignore]
```

---

# 39. Finding Interaction

Each finding must support:

```text
Explain
Locate
Fix
Ignore
Create ADR
```

"Locate" should highlight the affected nodes on the canvas.

---

# 40. Simulation View

Simulation UI:

```text
SIMULATION

Scenario

[ PostgreSQL failure ]

Traffic

[ Current ]

Duration

[ 5 minutes ]

Environment

[ Production-like ]

[ Run Simulation ]
```

Results:

```text
SIMULATION RESULT

Impact
HIGH

Affected components

API
PostgreSQL
Order Service

P99
320ms → 1.8s

Error rate
0.2% → 12.4%

Cascading failure
Potential
```

---

# 41. Simulation Animation

When a simulation runs, visually show:

```text
Failure
 ↓
Dependency
 ↓
Load increase
 ↓
Resource pressure
 ↓
Latency
 ↓
Potential failure
```

Do not use flashy animations.

Use subtle graph state transitions.

---

# 42. Evolution View

Use a horizontal architecture timeline:

```text
V1                 V2                 V3

100K DAU           5M DAU             50M DAU

●──────────────────●──────────────────●
```

Each version should show:

```text
Architecture
Trigger
Changes
Cost
Risk
Migration
```

---

# 43. Version Comparison

Allow:

```text
Compare V1 vs V2
```

Display:

```text
COMPONENTS

+ Redis
+ CDN

~ PostgreSQL replicas 1 → 3

CAPACITY

2M → 7M DAU

COST

$410 → $720/month
```

---

# 44. Brownfield Discovery UI

Flow:

```text
CONNECT

AWS
Kubernetes
Terraform

↓

DISCOVER

183 resources

↓

NORMALIZE

↓

GENERATE ARCHITECTURE

↓

REVIEW

↓

SAVE
```

The discovery experience should feel like an infrastructure scanner.

---

# 45. Drift UI

Use a two-column comparison.

```text
EXPECTED                    ACTUAL

API replicas: 3             API replicas: 5

Redis: enabled              Redis: disabled

DB replicas: 2              DB replicas: 1
```

Highlight differences.

---

# 46. Dashboard

Do not build a generic analytics dashboard.

Show systems.

```text
YOUR SYSTEMS

┌────────────────────────────────────────┐
│ Food Delivery                          │
│ ● Healthy                              │
│                                        │
│ 2.4M DAU       31K RPS                │
│ Capacity 63%                           │
│                                        │
│ ⚠ PostgreSQL approaching threshold     │
└────────────────────────────────────────┘
```

---

# 47. Empty States

Every empty state should explain what to do next.

Bad:

```text
No data.
```

Good:

```text
No architecture yet.

Describe your system and ArchitectOS
will generate a starting architecture.

[Describe System]
```

---

# 48. Loading States

Never use blank screens.

Use skeletons for:

```text
Cards
Inspector
Reports
Tables
Metrics
```

For AI operations use explicit progress:

```text
Analyzing requirements
✓

Building architecture
✓

Calculating capacity
●

Running validation
○
```

---

# 49. Error States

Errors must be actionable.

Bad:

```text
Something went wrong.
```

Good:

```text
Architecture generation failed.

The AI response did not satisfy the
Architecture IR schema.

No changes were applied.

Request ID:
req_8d2f

[Retry]
[View details]
```

---

# 50. Optimistic UI

Use optimistic updates only when safe.

Good:

```text
Rename component
Move node
Change visual preference
```

Avoid optimistic updates for:

```text
AI architecture mutations
Infrastructure discovery
Architecture validation
Simulation
Cost calculation
```

Those should await authoritative backend results.

---

# 51. State Management

Use **TanStack Query** for server state.

Use **Zustand** for local workspace state.

### Server state

```text
projects
architectures
requirements
validation
capacity
simulation
```

### Client state

```text
selected node
canvas mode
zoom
pan
open inspector
active analysis mode
command bar state
temporary UI state
```

Do not put server data into Zustand unnecessarily.

---

# 52. URL State

Use URL/query state for shareable views.

Examples:

```text
/project/123/architecture?node=postgres

/project/123/architecture?mode=capacity

/project/123/validation?severity=critical
```

This makes the workspace deep-linkable.

---

# 53. API Layer

Never call fetch directly from components.

Bad:

```typescript
const data = await fetch(...)
```

inside UI components.

Use:

```text
api/
hooks/
```

Example:

```typescript
const { data, isLoading } =
  useArchitecture(projectId);
```

---

# 54. API Client

Create a centralized API client.

Responsibilities:

```text
Base URL
Authentication
Headers
Request ID
Error normalization
Timeout
Retries
```

---

# 55. Schema Validation

Use Zod at the frontend API boundary.

```typescript
const ArchitectureSchema = z.object({
  id: z.string(),
  version: z.number(),
  nodes: z.array(...),
  edges: z.array(...)
});
```

Never blindly trust API responses.

---

# 56. TypeScript Rules

Use strict TypeScript.

```json
{
  "compilerOptions": {
    "strict": true
  }
}
```

Do not use:

```typescript
any
```

unless there is a documented boundary requiring it.

Prefer:

```typescript
unknown
```

and narrow it.

---

# 57. Component Design

Components should have one clear responsibility.

Bad:

```text
ArchitecturePage.tsx
```

containing:

* API calls
* graph calculations
* state management
* dialogs
* node rendering
* validation
* business logic

Good:

```text
ArchitecturePage
 ├── ArchitectureCanvas
 ├── ArchitectureToolbar
 ├── ArchitectureInspector
 ├── CommandBar
 └── ValidationDrawer
```

---

# 58. Smart vs Presentational Components

Separate:

```text
Container logic
```

from:

```text
Visual components
```

Example:

```text
useArchitecture()
       ↓
ArchitectureWorkspace
       ↓
ArchitectureCanvas
```

The canvas should not know how API requests work.

---

# 59. Custom Hooks

Create hooks around behavior.

Examples:

```text
useArchitecture()
useArchitectureSelection()
useArchitectureCommands()
useArchitectureHistory()
useCapacityAnalysis()
useValidation()
useSimulation()
useEvidence()
```

Avoid hooks that become 1,000-line business-logic containers.

---

# 60. Keyboard Shortcuts

This should feel like a professional desktop tool.

Recommended:

```text
⌘ K
Command bar

⌘ Z
Undo

⌘ Shift Z
Redo

⌘ S
Save

F
Fit architecture

Delete
Delete selected node

Esc
Close inspector

Space + drag
Pan canvas
```

Show shortcuts in tooltips.

---

# 61. Command Palette

`⌘K` opens:

```text
Search commands

Generate architecture
Add component
Validate architecture
Run simulation
Analyze capacity
Explain architecture
Export report
Compare versions
Open settings
```

This becomes the universal navigation layer.

---

# 62. Accessibility

The frontend must meet strong accessibility standards.

Requirements:

```text
Keyboard navigation
Visible focus states
ARIA labels
Semantic HTML
Color-independent status
Reduced motion support
Readable contrast
Screen reader labels
```

Never communicate:

```text
red = failure
```

alone.

Use:

```text
icon + text + color
```

---

# 63. Responsive Strategy

The architecture workspace is desktop-first.

Support:

```text
Desktop
Tablet
Mobile
```

But mobile should not attempt to reproduce the full canvas experience.

Mobile should prioritize:

```text
Architecture overview
Findings
Capacity
Component inspection
Reports
```

Canvas editing can be limited on small screens.

---

# 64. Performance

Architecture diagrams can become large.

Plan for:

```text
100+
500+
1000+
```

nodes eventually.

Avoid unnecessary React renders.

Use:

```text
memoization
stable callbacks
selectors
virtualization where appropriate
React Flow performance patterns
```

Do not prematurely optimize every component.

Measure first.

---

# 65. Large Graph Strategy

For large architectures:

```text
Overview mode
Detailed mode
Focused mode
```

Example:

```text
100 services
```

Initially show:

```text
Domains
```

Click:

```text
Payments
```

then expand:

```text
Payment Service
Payment DB
Payment Queue
Fraud Service
```

This prevents canvas overload.

---

# 66. Architecture Layout

Provide:

```text
Auto Layout
```

Options:

```text
Hierarchical
Force-directed
Domain grouped
Manual
```

The backend architecture model should not depend on the visual layout.

---

# 67. Graph Layout Rule

Store:

```text
semantic architecture
```

separately from:

```text
visual position
```

Example:

```text
Architecture IR
      +
Canvas Layout
```

This allows multiple visualizations of the same architecture.

---

# 68. Analysis Modes

The canvas should support:

```text
Topology
Capacity
Reliability
Security
Cost
Observability
Simulation
```

Same graph.

Different overlays.

---

# 69. Capacity Overlay

Node:

```text
PostgreSQL

82%

████████████████░░░
```

Edge:

```text
8.2K RPS
```

---

# 70. Reliability Overlay

Node:

```text
PostgreSQL

⚠ SPOF
```

Edge:

```text
Critical dependency
```

---

# 71. Cost Overlay

Node:

```text
PostgreSQL

$184/mo
```

Architecture:

```text
Total
$1,240/mo
```

---

# 72. Simulation Overlay

During simulation:

```text
Normal
↓
Failure
↓
Impact
↓
Cascade
```

Use animation sparingly.

---

# 73. Design System Components

Create a small internal design system.

```text
components/ui/

Button
IconButton
Input
Textarea
Select
Combobox
Tabs
Dialog
Drawer
Popover
Tooltip
Badge
Card
Table
Dropdown
Command
Progress
Skeleton
Toast
Alert
Separator
```

Do not duplicate these components inside features.

---

# 74. Avoid Component Explosion

Do not create:

```text
GreenButton.tsx
SmallButton.tsx
PrimarySmallButton.tsx
ArchitectureGreenButton.tsx
```

Create:

```text
Button
```

with variants.

---

# 75. Storybook

Every reusable UI primitive should have a Storybook story.

Examples:

```text
Button
Dialog
Badge
Node
FindingCard
MetricCard
Inspector
```

Storybook becomes the visual regression reference.

---

# 76. Testing Strategy

Frontend tests:

```text
Unit
Component
Integration
End-to-end
Visual regression
Accessibility
```

---

# 77. Unit Tests

Test:

```text
graph transformations
formatters
capacity display calculations
node mapping
URL state
command parsing
```

---

# 78. Component Tests

Test:

```text
Node rendering
Inspector behavior
Finding interaction
Command bar
Dialogs
Tabs
```

Use React Testing Library.

Test behavior rather than implementation details.

Bad:

```text
expect(component.state.foo).toBe(...)
```

Good:

```text
expect(screen.getByText("PostgreSQL")).toBeVisible()
```

---

# 79. E2E Tests

Use Playwright.

Critical journey:

```text
Create project
 ↓
Enter requirements
 ↓
Generate architecture
 ↓
View architecture
 ↓
Select PostgreSQL
 ↓
Inspect capacity
 ↓
Run validation
 ↓
View finding
 ↓
Run capacity analysis
 ↓
View operating envelope
```

This should be a golden-path E2E test.

---

# 80. Accessibility Testing

Run automated accessibility checks.

Test:

```text
keyboard navigation
dialogs
focus traps
labels
contrast
ARIA
```

---

# 81. Visual Regression

Capture:

```text
Architecture workspace
Component inspector
Validation
Capacity
Simulation
Dashboard
Dark mode
```

This prevents accidental design degradation.

---

# 82. Error Boundaries

Use route-level and feature-level error boundaries.

A simulation failure must not crash the entire workspace.

---

# 83. Security

Never expose:

```text
API secrets
LLM API keys
Cloud credentials
Internal tokens
```

in client-side code.

Only public configuration should be exposed.

---

# 84. Environment Variables

Frontend:

```text
NEXT_PUBLIC_API_URL
NEXT_PUBLIC_APP_ENV
```

Private secrets belong on the backend.

Never:

```text
NEXT_PUBLIC_OPENAI_API_KEY
```

---

# 85. Analytics

Track product behavior carefully.

Useful events:

```text
project_created
architecture_generated
node_selected
architecture_validated
simulation_started
simulation_completed
capacity_analysis_completed
architecture_change_applied
architecture_change_rejected
```

Do not track sensitive infrastructure configuration unnecessarily.

---

# 86. Logging

Frontend logs should be structured and minimal.

Never log entire:

```text
Architecture IR
Cloud credentials
LLM prompts containing secrets
Sensitive infrastructure data
```

---

# 87. Loading UX for AI

AI operations may take several seconds.

Do not display:

```text
Loading...
```

Instead:

```text
Analyzing requirements
✓

Checking assumptions
✓

Selecting components
✓

Building architecture
●

Validating architecture
○
```

The user should understand what is happening.

---

# 88. AI Streaming

Where useful, stream explanatory text.

Do not stream raw architecture mutations.

Architecture changes should be applied atomically after validation.

---

# 89. Architecture Mutation Protocol

Frontend should treat AI changes as:

```text
Proposal
```

not:

```text
Command
```

Flow:

```text
AI proposal
 ↓
Validate
 ↓
Preview
 ↓
User approval
 ↓
Apply
 ↓
New architecture version
```

---

# 90. Undo/Redo

Architecture modifications must support undo/redo.

Maintain:

```text
history
past
present
future
```

Do not rely entirely on browser history.

---

# 91. Autosave

Use debounced autosave for safe UI changes.

Example:

```text
User moves node
 ↓
300–800ms debounce
 ↓
save layout
```

For semantic architecture changes:

```text
explicit save/apply
```

is preferable.

---

# 92. Versioning

Every architecture mutation that changes semantic architecture should create:

```text
Architecture Version
```

Example:

```text
v1
v2
v3
```

Frontend must make version state visible.

---

# 93. Optimistic Version Conflict Handling

If another user changes the architecture:

```text
ARCHITECTURE UPDATED

Someone changed this architecture.

Your version:
v12

Latest:
v13

[Compare]
[Reload]
[Create branch]
```

Do not silently overwrite changes.

---

# 94. Collaboration Future-Proofing

Do not build real-time collaboration in V1.

But structure state so it can later support:

```text
Presence
Comments
Cursors
Concurrent editing
Version branches
```

---

# 95. Comments Future Feature

Architecture nodes should eventually support:

```text
Comments
Mentions
Decision threads
```

Do not tightly couple the node component to comments now.

---

# 96. Design Tokens Must Be Centralized

Do not scatter colors:

Bad:

```tsx
className="text-[#63D7A1]"
```

throughout hundreds of components.

Use semantic tokens.

```text
text-accent
bg-surface
border-default
text-muted
status-warning
```

---

# 97. Tailwind Rule

Tailwind should express design-system tokens.

Avoid arbitrary values everywhere.

Bad:

```text
p-[17px]
text-[#123456]
rounded-[11px]
```

Prefer:

```text
p-4
text-muted
rounded-md
```

Use arbitrary values only when truly necessary.

---

# 98. Iconography

Use one consistent icon system.

Recommended:

```text
Lucide
```

Do not mix:

```text
Lucide
Font Awesome
Material Icons
custom SVG
```

without a strong reason.

---

# 99. Component Icons

Infrastructure components should have recognizable icons.

Examples:

```text
PostgreSQL
Redis
Kafka
API
Load Balancer
S3
Kubernetes
```

Icons should be subtle.

Do not turn architecture nodes into huge colorful logos.

---

# 100. Animation Guidelines

Use Framer Motion only where it adds meaning.

Good:

```text
panel opening
finding appearing
architecture diff
simulation transition
version transition
```

Avoid animating:

```text
every card
every hover
every metric
every node
```

---

# 101. Reduced Motion

Respect:

```text
prefers-reduced-motion
```

When enabled:

```text
disable graph animations
disable unnecessary transitions
keep state changes understandable
```

---

# 102. AI Visual Language

AI should have a subtle visual identity.

Use:

```text
✦
```

or another consistent mark.

But do not create:

```text
sparkles everywhere
AI rainbow gradients
glowing chatbot bubbles
```

AI is a capability of ArchitectOS, not the visual product identity.

---

# 103. Frontend Data Flow

The canonical flow:

```text
Backend
   ↓
API client
   ↓
Zod validation
   ↓
TanStack Query
   ↓
Feature hook
   ↓
Feature component
   ↓
UI
```

For local state:

```text
UI
 ↓
Zustand
 ↓
Feature behavior
```

---

# 104. Architecture Data Flow

```text
GET /architectures/:id
          ↓
ArchitectureSchema
          ↓
Architecture API Model
          ↓
Adapter
          ↓
Domain UI Model
          ↓
React Flow
```

---

# 105. Mutation Data Flow

```text
User action
     ↓
Command
     ↓
API mutation
     ↓
Backend validation
     ↓
New Architecture Version
     ↓
Query invalidation
     ↓
UI update
```

---

# 106. Command Architecture

Use commands for semantic changes.

Examples:

```typescript
addComponent()
removeComponent()
connectComponents()
updateConfiguration()
changeReplicaCount()
applyArchitectureProposal()
```

Avoid directly mutating React state from many unrelated components.

---

# 107. Architecture Commands

Example:

```typescript
interface AddComponentCommand {
  type: "ADD_COMPONENT";
  component: ComponentDefinition;
}
```

Later:

```typescript
interface ChangeReplicaCommand {
  type: "CHANGE_REPLICAS";
  nodeId: string;
  replicas: number;
}
```

This makes architecture editing testable.

---

# 108. Code Generation Rules for Claude

When Claude generates frontend code, it must follow these rules.

## Rule 1

Inspect the existing repository before creating files.

## Rule 2

Do not overwrite existing architecture without understanding it.

## Rule 3

Reuse existing components.

## Rule 4

Do not introduce a new library unless necessary.

## Rule 5

Do not create duplicate utility functions.

## Rule 6

Do not use `any`.

## Rule 7

Do not bypass API schemas.

## Rule 8

Do not place API calls directly in presentational components.

## Rule 9

Do not create giant components.

## Rule 10

Write tests for non-trivial behavior.

---

# 109. Claude Implementation Workflow

Claude should follow:

```text
1. Inspect repository
2. Identify existing architecture
3. Identify relevant feature
4. Read types
5. Read API contracts
6. Read design tokens
7. Plan change
8. Implement smallest coherent change
9. Run formatter
10. Run lint
11. Run typecheck
12. Run tests
13. Review diff
14. Fix regressions
15. Report changes
```

Never blindly generate 20 files at once.

---

# 110. Claude Must Not Guess

If an API contract is unknown:

```text
DO NOT INVENT IT.
```

Instead:

```text
Inspect backend schema/API.

If unavailable:
create a typed interface boundary
and clearly mark the integration point.
```

Never fabricate backend responses.

---

# 111. Claude Must Preserve Domain Boundaries

Do not put:

```text
capacity calculation
architecture validation
AI prompting
API business logic
```

inside React components.

Frontend consumes backend results.

---

# 112. Claude Should Prefer Small PRs

Each implementation task should preferably correspond to:

```text
one feature
+
tests
+
documentation if required
```

Example:

```text
feat: add architecture node inspector
```

rather than:

```text
feat: build entire architecture workspace
```

---

# 113. Claude Code Review Checklist

Before considering code complete:

```text
[ ] TypeScript strict
[ ] No unnecessary any
[ ] No duplicated components
[ ] API calls isolated
[ ] Server state uses TanStack Query
[ ] Local state uses appropriate store
[ ] Components have clear responsibilities
[ ] Loading state exists
[ ] Error state exists
[ ] Empty state exists
[ ] Keyboard interaction works
[ ] Accessibility considered
[ ] Responsive behavior considered
[ ] Tests added
[ ] Lint passes
[ ] Typecheck passes
[ ] Build passes
```

---

# 114. Claude Visual Review Checklist

Claude should also review:

```text
[ ] Spacing consistent
[ ] Typography hierarchy clear
[ ] Borders consistent
[ ] Colors use tokens
[ ] No excessive shadows
[ ] No unnecessary gradients
[ ] Buttons consistent
[ ] Icons consistent
[ ] Empty states useful
[ ] Loading states informative
[ ] Error states actionable
[ ] Dark mode works
[ ] Focus states visible
```

---

# 115. Frontend Acceptance Criteria

The architecture workspace is considered complete only when:

### Canvas

```text
[ ] Pan
[ ] Zoom
[ ] Select
[ ] Multi-select
[ ] Add node
[ ] Delete node
[ ] Connect nodes
[ ] Auto-layout
[ ] Fit view
[ ] Undo
[ ] Redo
```

### Inspector

```text
[ ] Component details
[ ] Configuration
[ ] Capacity
[ ] Constraints
[ ] Failure modes
[ ] Evidence
```

### AI

```text
[ ] Command bar
[ ] Structured response
[ ] Architecture diff
[ ] Impact preview
[ ] Apply/reject
[ ] Evidence
```

### Analysis

```text
[ ] Capacity
[ ] Validation
[ ] Operating envelope
[ ] Bottleneck
[ ] Health
```

---

# 116. Performance Acceptance Criteria

Initial targets:

```text
Initial application render:
fast on normal broadband

Canvas:
smooth with 100+ nodes

Inspector:
instant for local state changes

Navigation:
no full-page reloads

API:
loading state visible immediately

AI:
progress state visible within interaction start
```

Measure actual performance rather than claiming arbitrary benchmarks.

---

# 117. SEO / Landing Page

Marketing pages should be separate from the authenticated application.

```text
app/
├── (marketing)/
└── (app)/
```

Marketing pages:

```text
/
 /product
 /architecture
 /simulation
 /pricing
 /docs
```

Application:

```text
/app
```

---

# 118. Landing Page Structure

```text
Hero

Interactive architecture demo

Problem

Architecture lifecycle

Design

Validate

Simulate

Scale

Evolve

Brownfield discovery

Evidence-driven AI

Product screenshots

Technical architecture

Pricing

CTA
```

---

# 119. Hero Copy Direction

Use:

```text
Design systems before they break.

Turn requirements into architectures
you can measure, validate, simulate,
and evolve.

[ Start designing ]
```

Avoid:

```text
The world's most revolutionary AI
architecture platform.
```

Technical credibility is more important than hype.

---

# 120. Product Demo

The landing page should contain an interactive miniature architecture.

User can change:

```text
DAU
Peak RPS
Database
Cache
Replicas
```

Then the diagram updates.

Example:

```text
100K DAU
```

↓

```text
1M DAU
```

↓

```text
PostgreSQL warning
```

This demonstrates the actual product.

---

# 121. Design System Documentation

Maintain:

```text
docs/frontend/design-system.md
```

Document:

```text
Colors
Typography
Spacing
Buttons
Cards
Forms
Dialogs
Architecture nodes
Graph states
Status states
Animations
Accessibility
```

---

# 122. Frontend Architecture Diagram

```text
                         Next.js
                            │
             ┌──────────────┼──────────────┐
             │              │              │
             ▼              ▼              ▼
         App Router      Components      Features
                            │              │
                            │              ├── Architecture
                            │              ├── Capacity
                            │              ├── Validation
                            │              ├── Simulation
                            │              └── Evolution
                            │
                            ▼
                       UI System
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
        TanStack Query    Zustand         Hooks
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                        API Client
                            │
                            ▼
                         FastAPI
```

---

# 123. Most Important Frontend Principle

The UI must always distinguish:

```text
WHAT THE SYSTEM IS
```

from:

```text
WHAT THE SYSTEM THINKS
```

and:

```text
WHAT THE SYSTEM CALCULATED
```

For example:

```text
Architecture
    ↓
Facts

AI recommendation
    ↓
Proposal

Capacity engine
    ↓
Calculation

Validator
    ↓
Finding

Simulation
    ↓
Result
```

These should never visually blur together.

---

# 124. Visual Trust Model

Use different visual treatments.

### User data

```text
neutral
```

### AI proposal

```text
mint accent
```

### Deterministic calculation

```text
blue/technical
```

### Warning

```text
amber
```

### Critical finding

```text
red
```

### Evidence

```text
subtle secondary panel
```

This allows users to understand where information came from.

---

# 125. Final UX Principle

ArchitectOS should feel like:

> **A professional engineering instrument.**

When an engineer sees it, they should think:

```text
"This is where I understand my system."
```

not:

```text
"This is another AI chatbot."
```

The architecture canvas should feel authoritative.

The AI should feel assistive.

The calculations should feel deterministic.

The evidence should feel inspectable.

The simulations should feel experimental.

The evolution timeline should feel strategic.

---

# 126. Claude Master Implementation Instruction

When using this document with Claude, prepend the following instruction:

> You are implementing the ArchitectOS frontend, a production-grade architecture engineering platform. Treat this document as the frontend engineering specification. Before modifying code, inspect the repository and understand the existing architecture. Follow the established design system and domain boundaries. Do not invent backend contracts, infrastructure constraints, or product behavior. Prefer small, composable, strongly typed changes. Use existing abstractions before creating new ones. Keep React components focused on presentation and interaction; keep API communication, domain transformations, and state management in their designated layers. Never use the LLM as a source of deterministic frontend facts. Every AI-driven architecture mutation must be represented as a proposal/diff and require explicit user confirmation. Run formatting, linting, type checking, and relevant tests after implementation. Treat accessibility, loading states, empty states, error states, responsive behavior, keyboard navigation, and dark mode as part of the feature—not optional polish. Preserve the visual language: technical minimalism, restrained mint accent, high information density, strong typography, subtle borders, minimal shadows, and purposeful animation. Do not introduce visual clutter or generic SaaS dashboard patterns.

---

# 127. Claude Task Execution Template

For every feature, use:

```text
TASK

Implement:
[feature]

CONTEXT

[why this feature exists]

FILES TO INSPECT

[relevant files]

REQUIREMENTS

1.
2.
3.

DESIGN

[visual behavior]

STATE

[loading / success / error / empty]

API

[endpoint / schema]

INTERACTION

[keyboard / mouse / command]

TESTING

[unit / component / E2E]

ACCEPTANCE CRITERIA

[checklist]
```

This gives Claude enough structure to generate production-quality code instead of improvising.

---

# 128. First Frontend Build Sequence

Claude should implement the frontend in this exact order:

```text
01. Next.js application shell
02. Design tokens
03. Typography
04. UI primitives
05. Application layout
06. Sidebar
07. Top navigation
08. Dashboard
09. Project creation
10. Requirements page
11. Architecture workspace shell
12. React Flow canvas
13. Architecture node system
14. Architecture edge system
15. Node inspector
16. Architecture toolbar
17. Architecture command bar
18. AI proposal/diff UI
19. Capacity view
20. Operating envelope
21. Validation view
22. Finding system
23. Evidence drawer
24. ADR view
25. Dark mode
26. Accessibility pass
27. Performance pass
28. Playwright golden path
29. Visual regression
30. Production build
```

Do not begin with animations or marketing pages.

Build the **engineering workspace first**.

---

# 129. Final Frontend Architecture Goal

The finished application should feel like:

```text
                 ARCHITECTOS
                      │
        ┌─────────────┴─────────────┐
        │                           │
     THINK                        BUILD
        │                           │
 Requirements                 Architecture
        │                           │
        └─────────────┬─────────────┘
                      │
                    PROVE
                      │
             Capacity + Validation
                      │
                    BREAK
                      │
                  Simulation
                      │
                   OPERATE
                      │
            Security + Observability
                      │
                    SCALE
                      │
                 Evolution
                      │
                  MIGRATE
                      │
                   DISCOVER
                      │
                    DRIFT
```

The frontend should make that entire lifecycle feel like **one coherent engineering environment**, rather than a collection of unrelated pages.

---

# 130. Definition of "Excellent"

The frontend is excellent when:

* A new engineer understands the workspace within 30 seconds.
* An experienced engineer can inspect a complex architecture without feeling lost.
* AI suggestions are clearly distinguishable from deterministic results.
* Every important recommendation can be traced to evidence.
* Architecture changes are reviewable before being applied.
* Large architectures remain navigable.
* Capacity and reliability are visual, not buried in tables.
* The UI remains calm despite high information density.
* Keyboard shortcuts make the product fast.
* Dark mode feels native.
* The interface is accessible.
* The application feels fast.
* The design is recognizable without relying on a logo.
* The product looks like engineering infrastructure, not an AI demo.

## The guiding sentence

> **Make complexity visible without making the interface complex.**
