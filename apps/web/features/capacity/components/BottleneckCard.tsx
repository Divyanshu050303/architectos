import { CircleCheck, LocateFixed, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { formatCompact } from "@/lib/formatting";
import type { Bottleneck } from "@/types/capacity";

export interface BottleneckCardProps {
  projectId: string;
  bottleneck: Bottleneck | null;
  nodeName: (nodeId: string) => string;
}

/** The first resource the capacity engine expects to saturate as load grows (spec §35). */
export function BottleneckCard({ projectId, bottleneck, nodeName }: BottleneckCardProps) {
  return (
    <Card role="region" aria-labelledby="bottleneck-heading">
      <CardHeader>
        <CardTitle id="bottleneck-heading">Next bottleneck</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {bottleneck ? (
          <>
            <p className="flex items-start gap-2 text-sm font-semibold text-fg">
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warning-fg" />
              <span>
                {nodeName(bottleneck.nodeId)} {bottleneck.resource}
              </span>
            </p>
            <p className="text-sm text-fg-secondary">{bottleneck.description}</p>
            <div className="flex flex-col gap-0.5">
              <span className="label-caps">Expected threshold</span>
              <span className="tabular text-lg font-semibold text-fg">
                ~{formatCompact(bottleneck.thresholdDailyActiveUsers)}{" "}
                <span className="text-xs font-normal text-muted">DAU</span>
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <WhyButton
                evidenceId={bottleneck.evidenceId}
                subject={`${nodeName(bottleneck.nodeId)} ${bottleneck.resource} bottleneck`}
              />
              <Button asChild variant="ghost" size="sm" className="h-6 px-1.5">
                <Link
                  href={`/project/${encodeURIComponent(projectId)}/architecture?highlight=${encodeURIComponent(bottleneck.nodeId)}&node=${encodeURIComponent(bottleneck.nodeId)}`}
                >
                  <LocateFixed aria-hidden className="size-3.5" />
                  Locate
                </Link>
              </Button>
            </div>
          </>
        ) : (
          <p className="flex items-start gap-2 text-sm text-fg-secondary">
            <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
            No bottleneck within the analyzed load range.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
