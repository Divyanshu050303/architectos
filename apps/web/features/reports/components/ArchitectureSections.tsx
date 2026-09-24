import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, Td, Th } from "@/components/ui/table";
import { formatNumber, formatPercent } from "@/lib/formatting";
import { describeEdge } from "@/lib/graph";
import type { Architecture } from "@/types/architecture";
import type { Requirements } from "@/types/project";

import { ASSUMPTION_SOURCE, humanize, replicas } from "../utils";
import { Missing, ReportSection, Stat } from "./ReportPrimitives";

/** Report sections describing what the architecture is: requirements, components, connections, assumptions. */

export function RequirementsSection({ pending, reqs }: { pending: boolean; reqs: Requirements | null }) {
  return (
    <ReportSection title="Requirements" tag={<ProvenanceTag kind="fact" />}>
      {pending ? (
        <Skeleton className="h-24" />
      ) : !reqs ? (
        <Missing text="No requirements recorded." />
      ) : (
        <div className="flex flex-col gap-4">
          {reqs.description ? <p className="text-sm whitespace-pre-line">{reqs.description}</p> : null}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Stat label="Daily active users" value={formatNumber(reqs.nonFunctional.dailyActiveUsers, 0)} />
            <Stat label="Peak RPS" value={formatNumber(reqs.nonFunctional.peakRps, 0)} />
            <Stat label="Availability" value={formatPercent(reqs.nonFunctional.availabilityTarget, 3)} />
            <Stat
              label="P99 latency"
              value={
                reqs.nonFunctional.p99LatencyMs === null
                  ? "—"
                  : `${formatNumber(reqs.nonFunctional.p99LatencyMs)} ms`
              }
            />
            <Stat
              label="Data retention"
              value={
                reqs.nonFunctional.dataRetentionDays === null
                  ? "—"
                  : `${formatNumber(reqs.nonFunctional.dataRetentionDays, 0)} days`
              }
            />
            <Stat label="Regions" value={reqs.nonFunctional.regions.join(", ") || "—"} />
          </dl>
          {reqs.functional.length > 0 ? (
            <div className="flex flex-col gap-1">
              <h3 className="label-caps">Functional</h3>
              <ul className="list-disc pl-5 text-sm">
                {reqs.functional.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </ReportSection>
  );
}

export function ComponentsSection({ arch }: { arch: Architecture | null }) {
  return (
    <ReportSection title="Components">
      {!arch ? <Missing text="No architecture yet." /> : <ComponentsTable architecture={arch} />}
    </ReportSection>
  );
}

export function ConnectionsSection({ arch }: { arch: Architecture | null }) {
  return (
    <ReportSection title="Connections">
      {!arch ? (
        <Missing text="No architecture yet." />
      ) : arch.edges.length === 0 ? (
        <Missing text="No connections." />
      ) : (
        <Table aria-label="Connections">
          <thead>
            <tr>
              <Th>Connection</Th>
              <Th>Protocol</Th>
              <Th>Mode</Th>
              <Th>Critical path</Th>
            </tr>
          </thead>
          <tbody>
            {arch.edges.map((edge) => (
              <tr key={edge.id}>
                <Td>{describeEdge(arch, edge)}</Td>
                <Td className="tabular text-xs">{edge.protocol ?? "—"}</Td>
                <Td>{edge.synchronous ? "Synchronous" : "Asynchronous"}</Td>
                <Td>{edge.critical ? "Yes" : "No"}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </ReportSection>
  );
}

export function AssumptionsSection({ arch }: { arch: Architecture | null }) {
  return (
    <ReportSection title="Assumptions">
      {!arch ? (
        <Missing text="No architecture yet." />
      ) : arch.assumptions.length === 0 ? (
        <Missing text="No assumptions recorded." />
      ) : (
        <ul className="flex flex-col gap-2">
          {arch.assumptions.map((assumption) => {
            const source = ASSUMPTION_SOURCE[assumption.source];
            return (
              <li key={assumption.id} className="flex flex-wrap items-start gap-3 text-sm">
                <span className="tabular w-14 shrink-0 text-xs text-fg-secondary">{assumption.id}</span>
                <span className="min-w-0 flex-1">{assumption.statement}</span>
                <ProvenanceTag kind={source.kind} label={source.label} />
              </li>
            );
          })}
        </ul>
      )}
    </ReportSection>
  );
}

function ComponentsTable({ architecture }: { architecture: Architecture }) {
  if (architecture.nodes.length === 0) return <Missing text="No components." />;
  return (
    <Table aria-label="Components">
      <thead>
        <tr>
          <Th>Name</Th>
          <Th>Category</Th>
          <Th>Technology</Th>
          <Th className="text-right">Replicas</Th>
        </tr>
      </thead>
      <tbody>
        {architecture.nodes.map((node) => (
          <tr key={node.id}>
            <Td className="font-medium">{node.name}</Td>
            <Td>{humanize(node.type)}</Td>
            <Td className="text-fg-secondary">{node.technology || "—"}</Td>
            <Td className="tabular text-right">{replicas(node.configuration)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
