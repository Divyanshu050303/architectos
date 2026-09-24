import {
  Command,
  FileClock,
  GitCompareArrows,
  Keyboard,
  type LucideIcon,
  Undo2,
  Waypoints,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { NodeStatesGallery } from "@/features/marketing/components/product-sections";
import { Section, SectionHeader } from "@/features/marketing/components/Section";
import { CtaSection, PageIntro, TechArchitectureSection } from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";

export const metadata = marketingMetadata({
  title: "Architecture",
  description:
    "A canvas for software architecture: components with live capacity, analysis modes, semantic commands with undo, versions and reviewable AI diffs.",
});

const MODES = [
  { name: "Topology", detail: "Components and dependencies.", available: true },
  { name: "Capacity", detail: "Utilisation per component and the next bottleneck.", available: true },
  { name: "Reliability", detail: "Single points of failure and missing timeouts.", available: true },
  { name: "Security", detail: "Trust boundaries and exposed surfaces.", available: false },
  { name: "Cost", detail: "Monthly cost per component and per change.", available: false },
  { name: "Simulation", detail: "Failure propagation and load pressure.", available: false },
] as const;

const EDITING: ReadonlyArray<{ title: string; body: string; Icon: LucideIcon }> = [
  {
    title: "Semantic commands",
    body: "Add component, connect, change replicas: every edit is a named command the backend understands.",
    Icon: Command,
  },
  {
    title: "Undo and redo",
    body: "Every command is reversible, including AI proposals you applied.",
    Icon: Undo2,
  },
  {
    title: "Versions",
    body: "Saving creates an architecture version. Layout moves never do.",
    Icon: FileClock,
  },
  {
    title: "Reviewable diffs",
    body: "Proposed changes are previewed on the canvas as additions, removals and changes.",
    Icon: GitCompareArrows,
  },
  {
    title: "Keyboard first",
    body: "Command palette, shortcuts for fit, mode switching and inspection.",
    Icon: Keyboard,
  },
  {
    title: "Automatic layout",
    body: "Layered layout for large graphs, grouped by domain when it gets dense.",
    Icon: Waypoints,
  },
];

export default function ArchitecturePage() {
  return (
    <>
      <PageIntro
        eyebrow="Architecture"
        title="A canvas that knows what each box can handle."
        description="Nodes carry their type, technology, key metric and status. Switch the analysis mode and the same graph shows capacity, reliability or cost."
      />
      <Section labelledBy="nodes-title">
        <SectionHeader
          id="nodes-title"
          eyebrow="Node states"
          title="Status is always spelled out, never colour alone."
          description="The same node component in four states, rendered live. Colour is never the only signal."
        />
        <NodeStatesGallery />
      </Section>
      <Section labelledBy="modes-title" tone="muted">
        <SectionHeader id="modes-title" eyebrow="Analysis modes" title="Same graph, different lens." />
        <ul className="grid gap-px overflow-hidden rounded-lg border border-default bg-default sm:grid-cols-2 lg:grid-cols-3">
          {MODES.map((mode) => (
            <li key={mode.name} className="flex flex-col gap-1.5 bg-surface p-5">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-fg">{mode.name}</h3>
                {mode.available ? null : <Badge>Planned</Badge>}
              </div>
              <p className="text-sm text-fg-secondary">{mode.detail}</p>
            </li>
          ))}
        </ul>
      </Section>
      <Section labelledBy="editing-title">
        <SectionHeader id="editing-title" eyebrow="Editing" title="Edits are commands, not pixels." />
        <ul className="grid gap-x-8 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
          {EDITING.map(({ title, body, Icon }) => (
            <li key={title} className="flex flex-col gap-2">
              <Icon aria-hidden className="size-4 text-accent-fg" />
              <h3 className="text-sm font-semibold text-fg">{title}</h3>
              <p className="text-sm text-fg-secondary">{body}</p>
            </li>
          ))}
        </ul>
      </Section>
      <TechArchitectureSection />
      <CtaSection />
    </>
  );
}
