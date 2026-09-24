"use client";

import { Calculator, ChevronRight, DollarSign } from "lucide-react";
import { Fragment, useCallback, useMemo, useState } from "react";

import { AnalysisSurface, LocateLink } from "@/components/feedback/AnalysisSurface";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, Td, Th } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useCalculateCost, useCost } from "@/hooks/use-cost";
import { useEvidence } from "@/hooks/use-evidence";
import { formatCurrency, formatPercent } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { CloudProvider, CostEstimate } from "@/types/cost";

const PROVIDER_LABEL: Record<CloudProvider, string> = { aws: "AWS", gcp: "Google Cloud", azure: "Azure" };

/** Cost page container (spec §71). Every figure comes from the backend cost engine (spec §111). */
export function CostView({ projectId }: { projectId: string }) {
  const cost = useCost(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate, isPending: running } = useCalculateCost(projectId);
  const hasArchitecture = Boolean(architecture.data);

  const calculate = useCallback(() => {
    mutate(undefined, {
      onSuccess: (result) =>
        toast("Cost calculated", {
          tone: "success",
          description: `${formatCurrency(result.total, result.currency)}/mo for architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Cost calculation failed. No changes were applied.", error),
    });
  }, [mutate]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.cost.run",
        label: "Calculate cost",
        group: "Analysis" as const,
        keywords: ["cost", "price", "monthly", "budget", "spend", "recalculate"],
        disabled: running || !hasArchitecture,
        run: calculate,
      },
    ],
    [running, hasArchitecture, calculate],
  );
  useRegisterCommands(commands);

  return (
    <AnalysisSurface<CostEstimate>
      projectId={projectId}
      title="Cost"
      description="Monthly cost per component calculated by the cost engine from provider price lists."
      icon={DollarSign}
      engineLabel="Cost engine"
      query={cost}
      timestamp={(data) => data.calculatedAt}
      timestampVerb="Calculated"
      run={{
        onRun: calculate,
        running,
        idleLabel: "Calculate cost",
        rerunLabel: "Recalculate",
        pendingLabel: "Calculating…",
      }}
      emptyDescription="Calculate cost to estimate the monthly bill for every component from your provider's price list."
      overlay={{ mode: "cost", label: "View cost overlay" }}
    >
      {(data, { nodeName }) => <CostResults projectId={projectId} estimate={data} nodeName={nodeName} />}
    </AnalysisSurface>
  );
}

export interface CostResultsProps {
  projectId: string;
  estimate: CostEstimate;
  nodeName: (id: string) => string;
}

export function CostResults({ projectId, estimate, nodeName }: CostResultsProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const money = (value: number) => formatCurrency(value, estimate.currency);
  // Display ordering only; the figures are the backend's.
  const nodes = useMemo(() => [...estimate.nodes].sort((a, b) => b.monthly - a.monthly), [estimate.nodes]);
  const categories = useMemo(
    () => [...estimate.byCategory].sort((a, b) => b.monthly - a.monthly),
    [estimate.byCategory],
  );
  const share = (monthly: number) => (estimate.total > 0 ? monthly / estimate.total : 0);

  const toggle = (nodeId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card role="region" aria-labelledby="cost-total-heading">
          <CardHeader>
            <CardTitle id="cost-total-heading">Total</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <p
              className="tabular text-4xl font-semibold text-fg"
              aria-label={`Total ${money(estimate.total)} per month`}
            >
              {money(estimate.total)}
              <span className="ml-1 text-base font-normal text-muted">/mo</span>
            </p>
            <p className="text-xs text-fg-secondary">
              {PROVIDER_LABEL[estimate.provider]} · {estimate.currency} · per {estimate.period}
            </p>
          </CardContent>
        </Card>

        <Card role="region" aria-labelledby="cost-category-heading">
          <CardHeader>
            <CardTitle id="cost-category-heading">By category</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent>
            {categories.length > 0 ? (
              <ul className="flex flex-col gap-2.5">
                {categories.map((row) => (
                  <li
                    key={row.category}
                    className="grid grid-cols-[7rem_minmax(0,1fr)_4.5rem] items-center gap-3"
                  >
                    <span className="truncate text-sm text-fg-secondary">{row.category}</span>
                    <Meter
                      value={share(row.monthly)}
                      tone="info"
                      label={`${row.category}: ${money(row.monthly)} per month, ${formatPercent(share(row.monthly))} of total`}
                    />
                    <span className="tabular text-right text-sm text-fg">{money(row.monthly)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No category breakdown was reported.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card role="region" aria-labelledby="cost-components-heading">
        <CardHeader>
          <CardTitle id="cost-components-heading">Components</CardTitle>
          <ProvenanceTag kind="calculated" />
        </CardHeader>
        <CardContent className="p-0">
          {nodes.length > 0 ? (
            <Table aria-label="Monthly cost by component">
              <thead>
                <tr>
                  <Th>Component</Th>
                  <Th className="text-right">Monthly</Th>
                  <Th className="w-[30%] min-w-32">Share of total</Th>
                  <Th>
                    <span className="sr-only">Locate</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => {
                  const name = nodeName(node.nodeId);
                  const open = expanded.has(node.nodeId);
                  const detailsId = `cost-breakdown-${node.nodeId}`;
                  return (
                    <Fragment key={node.nodeId}>
                      <tr>
                        <Td className="whitespace-nowrap">
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-controls={detailsId}
                            onClick={() => toggle(node.nodeId)}
                            className="inline-flex items-center gap-1 rounded-sm text-left text-fg hover:text-accent-fg focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:outline-none"
                          >
                            <ChevronRight
                              aria-hidden
                              className={cn("size-3.5 text-muted transition-transform", open && "rotate-90")}
                            />
                            {name}
                            <span className="sr-only">{open ? ", hide breakdown" : ", show breakdown"}</span>
                          </button>
                        </Td>
                        <Td className="tabular text-right whitespace-nowrap">{money(node.monthly)}/mo</Td>
                        <Td>
                          <div className="flex items-center gap-2">
                            <Meter
                              value={share(node.monthly)}
                              tone="info"
                              label={`${name} share of total cost`}
                              className="flex-1"
                            />
                            <span className="tabular w-10 text-right text-xs text-fg-secondary">
                              {formatPercent(share(node.monthly))}
                            </span>
                          </div>
                        </Td>
                        <Td className="text-right">
                          <LocateLink
                            projectId={projectId}
                            mode="cost"
                            nodeIds={[node.nodeId]}
                            subject={name}
                          />
                        </Td>
                      </tr>
                      {open ? (
                        <tr id={detailsId}>
                          <td colSpan={4} className="border-b border-default bg-surface-2 px-3 py-2">
                            <ul className="flex flex-col gap-1 pl-5" aria-label={`${name} cost breakdown`}>
                              {node.breakdown.map((line) => (
                                <li
                                  key={line.item}
                                  className="flex items-baseline justify-between gap-4 text-xs"
                                >
                                  <span className="text-fg-secondary">{line.item}</span>
                                  <span className="tabular text-fg">{money(line.monthly)}</span>
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <p className="px-4 py-3 text-sm text-muted">No component costs were reported.</p>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card role="region" aria-labelledby="cost-assumptions-heading">
          <CardHeader>
            <CardTitle id="cost-assumptions-heading">Assumptions</CardTitle>
            <ProvenanceTag kind="fact" label="Assumption" />
          </CardHeader>
          <CardContent>
            {estimate.assumptions.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {estimate.assumptions.map((assumption) => (
                  <li key={assumption.id} className="flex gap-3 text-sm">
                    <span className="tabular shrink-0 text-xs text-fg-secondary">{assumption.id}</span>
                    <span className="text-fg">{assumption.statement}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No assumptions were recorded.</p>
            )}
          </CardContent>
        </Card>

        <Card role="region" aria-labelledby="cost-evidence-heading">
          <CardHeader>
            <CardTitle id="cost-evidence-heading">Evidence</CardTitle>
            <ProvenanceTag kind="evidence" />
          </CardHeader>
          <CardContent className="p-0">
            {estimate.evidenceIds.length > 0 ? (
              <ul className="divide-y divide-default">
                {estimate.evidenceIds.map((id) => (
                  <EvidenceRow key={id} evidenceId={id} />
                ))}
              </ul>
            ) : (
              <p className="px-4 py-3 text-sm text-muted">No evidence was recorded for this estimate.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** One cited evidence record: its claim, with Why? opening the evidence drawer (spec §34). */
function EvidenceRow({ evidenceId }: { evidenceId: string }) {
  const { data, isPending } = useEvidence(evidenceId);
  return (
    <li className="flex items-center justify-between gap-3 px-4 py-2.5">
      <span className="flex min-w-0 items-center gap-2 text-sm text-fg">
        <Calculator aria-hidden className="size-3.5 shrink-0 text-muted" />
        {isPending ? (
          <Skeleton className="h-4 w-48" />
        ) : (
          <span className="truncate">{data?.claim ?? evidenceId}</span>
        )}
      </span>
      <WhyButton evidenceId={evidenceId} subject={data?.claim ?? evidenceId} />
    </li>
  );
}
