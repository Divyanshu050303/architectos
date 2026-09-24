/** Brownfield discovery section for the marketing pages (spec §123). */
import { ArrowRight, Cloud, FileCode2, Layers3 } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { MockFrame, Section, SectionHeader } from "./Section";

// --- Brownfield discovery ---------------------------------------------------

const SOURCES = [
  { label: "AWS", detail: "account 4412…", Icon: Cloud },
  { label: "Kubernetes", detail: "prod-eu-1", Icon: Layers3 },
  { label: "Terraform", detail: "infra/main.tf", Icon: FileCode2 },
] as const;

const DRIFT = [
  { field: "API replicas", expected: "3", actual: "5", drift: true },
  { field: "Redis", expected: "enabled", actual: "enabled", drift: false },
  { field: "DB replicas", expected: "2", actual: "1", drift: true },
] as const;

export function BrownfieldSection() {
  return (
    <Section labelledBy="brownfield-title" tone="muted">
      <SectionHeader
        id="brownfield-title"
        eyebrow="Brownfield discovery"
        title="Start from what is already running."
        description="Connect cloud accounts, clusters and infrastructure code. ArchitectOS normalises what it finds into the same architecture model, then keeps comparing the design with reality."
        aside={<Badge>Planned</Badge>}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <MockFrame title="Discovery: connect, discover, normalise, review." path="discovery / scan">
          <div className="flex flex-col gap-4 p-4">
            <ul className="grid gap-2 sm:grid-cols-3">
              {SOURCES.map(({ label, detail, Icon }) => (
                <li
                  key={label}
                  className="flex items-center gap-2 rounded-md border border-default px-3 py-2"
                >
                  <Icon aria-hidden className="size-4 text-muted" />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-fg">{label}</span>
                    <span className="tabular block truncate text-2xs text-muted">{detail}</span>
                  </span>
                </li>
              ))}
            </ul>
            <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-secondary">
              {["Connect", "Discover", "Normalise", "Generate", "Review"].map((step, i, all) => (
                <li key={step} className="flex items-center gap-2">
                  <span className={cn(i < 3 ? "font-medium text-fg" : "text-muted")}>{step}</span>
                  {i < all.length - 1 ? <ArrowRight aria-hidden className="size-3 text-muted" /> : null}
                </li>
              ))}
            </ol>
            <dl className="grid grid-cols-3 gap-2">
              {[
                ["Resources", "183"],
                ["Components", "24"],
                ["Unmapped", "7"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-md bg-surface-2 px-3 py-2">
                  <dt className="label-caps">{label}</dt>
                  <dd className="tabular text-lg font-semibold text-fg">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </MockFrame>
        <MockFrame title="Drift: the design compared with what is deployed." path="discovery / drift">
          <table className="w-full text-sm">
            <caption className="sr-only">Expected configuration compared with actual</caption>
            <thead>
              <tr className="border-b border-default text-left">
                <th scope="col" className="label-caps px-4 py-2 font-semibold">
                  Setting
                </th>
                <th scope="col" className="label-caps px-4 py-2 font-semibold">
                  Expected
                </th>
                <th scope="col" className="label-caps px-4 py-2 font-semibold">
                  Actual
                </th>
              </tr>
            </thead>
            <tbody>
              {DRIFT.map((row) => (
                <tr key={row.field} className="border-b border-default last:border-0">
                  <th scope="row" className="px-4 py-2.5 text-left font-normal text-fg-secondary">
                    {row.field}
                  </th>
                  <td className="tabular px-4 py-2.5 text-fg">{row.expected}</td>
                  <td className="px-4 py-2.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn("tabular", row.drift ? "font-semibold text-warning-fg" : "text-fg")}
                      >
                        {row.actual}
                      </span>
                      {row.drift ? (
                        <StatusBadge status="warning" label="Drift" />
                      ) : (
                        <StatusBadge status="healthy" label="Match" />
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </MockFrame>
      </div>
    </Section>
  );
}
