import type { LucideIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/feedback/EmptyState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { projectHref } from "@/config/navigation";

export interface PlannedSurfaceProps {
  projectId: string;
  title: string;
  description: string;
  icon: LucideIcon;
  /** What the surface will do once it ships. */
  emptyTitle: string;
  emptyDescription: string;
  capabilities: readonly string[];
}

/** Placeholder for a surface planned for V2 (spec §4). Explains the purpose; shows no data. */
export function PlannedSurface({
  projectId,
  title,
  description,
  icon,
  emptyTitle,
  emptyDescription,
  capabilities,
}: PlannedSurfaceProps) {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-6 sm:px-6">
      <PageHeader
        title={title}
        description={description}
        meta={<Badge tone="neutral">Planned for V2</Badge>}
      />
      <EmptyState
        icon={icon}
        title={emptyTitle}
        description={emptyDescription}
        action={
          <Button asChild variant="secondary">
            <Link href={projectHref(projectId, "architecture")}>Back to architecture</Link>
          </Button>
        }
      />
      <section aria-labelledby="planned-capabilities" className="flex flex-col gap-2">
        <h2 id="planned-capabilities" className="label-caps">
          What it will do
        </h2>
        <ul className="flex flex-col gap-1.5 text-sm text-fg-secondary">
          {capabilities.map((capability) => (
            <li key={capability} className="flex gap-2">
              <span aria-hidden className="text-muted">
                –
              </span>
              {capability}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
