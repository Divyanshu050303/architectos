import { StatusBadge } from "@/components/ui/badge";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, Td, Th } from "@/components/ui/table";
import { DecisionStatusBadge, formatAdrDate, formatAdrNumber } from "@/features/decisions/format";
import { CATEGORY_LABEL, pluralFindings, SeverityBadge } from "@/features/validation/severity";
import { formatCompact, formatDateTime, formatNumber, formatPercent } from "@/lib/formatting";
import { nodeName } from "@/lib/graph";
import { SEVERITIES } from "@/schemas/validation";
import type { Architecture, Decision } from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { ValidationReport } from "@/types/validation";

import { humanize, NOT_ANALYZED } from "../utils";
import { Missing, ReportSection, Stat } from "./ReportPrimitives";

/** Report sections summarising analysis results: capacity, health and findings, decisions. */

export function CapacitySection({
  pending,
  cap,
  arch,
}: {
  pending: boolean;
  cap: CapacityAnalysis | null;
  arch: Architecture | null;
}) {
  const name = (id: string) => (arch ? nodeName(arch, id) : id);
  return (
    <ReportSection title="Capacity" tag={cap ? <ProvenanceTag kind="calculated" /> : null}>
      {pending ? (
        <Skeleton className="h-32" />
      ) : !cap ? (
        <Missing text={NOT_ANALYZED} />
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-xs text-muted">
            Calculated for <span className="tabular">v{cap.architectureVersion}</span> on{" "}
            {formatDateTime(cap.calculatedAt)}
            {arch && arch.version !== cap.architectureVersion
              ? " (out of date: re-run capacity analysis)"
              : ""}
            .
          </p>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <Stat label="Daily active users" value={formatCompact(cap.load.dailyActiveUsers)} />
            <Stat label="Peak RPS" value={formatCompact(cap.load.peakRps)} />
            <Stat label="Writes/s" value={formatCompact(cap.load.writesPerSecond)} />
            <Stat
              label="Max supported DAU"
              value={`~${formatCompact(cap.envelope.maxSupportedDailyActiveUsers)}`}
            />
          </dl>
          <p className="text-sm">
            <span className="label-caps mr-2">Next bottleneck</span>
            {cap.bottleneck
              ? `${name(cap.bottleneck.nodeId)} ${cap.bottleneck.resource} at ~${formatCompact(cap.bottleneck.thresholdDailyActiveUsers)} DAU. ${cap.bottleneck.description}`
              : "None within the analyzed range."}
          </p>
          {cap.utilization.length > 0 ? (
            <Table aria-label="Utilization">
              <thead>
                <tr>
                  <Th>Component</Th>
                  <Th>Resource</Th>
                  <Th className="text-right">Used / limit</Th>
                  <Th className="text-right">Utilization</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {cap.utilization.map((row) => (
                  <tr key={`${row.nodeId}-${row.resource}`}>
                    <Td>{name(row.nodeId)}</Td>
                    <Td>{humanize(row.resource)}</Td>
                    <Td className="tabular text-right text-xs">
                      {formatNumber(row.used)} / {formatNumber(row.limit)} {row.unit}
                    </Td>
                    <Td className="tabular text-right text-xs">{formatPercent(row.utilization)}</Td>
                    <Td>
                      <StatusBadge status={row.status} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : null}
        </div>
      )}
    </ReportSection>
  );
}

export function HealthSection({
  pending,
  report,
  arch,
}: {
  pending: boolean;
  report: ValidationReport | null;
  arch: Architecture | null;
}) {
  return (
    <ReportSection title="Health and findings" tag={report ? <ProvenanceTag kind="finding" /> : null}>
      {pending ? (
        <Skeleton className="h-32" />
      ) : !report ? (
        <Missing text={NOT_ANALYZED} />
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-xs text-muted">
            Validated <span className="tabular">v{report.architectureVersion}</span> on{" "}
            {formatDateTime(report.validatedAt)}
            {arch && arch.version !== report.architectureVersion ? " (out of date: re-run validation)" : ""}.
          </p>
          <Table aria-label="Health by category">
            <thead>
              <tr>
                <Th>Category</Th>
                <Th className="text-right">Score</Th>
                <Th>Summary</Th>
                <Th className="text-right">Findings</Th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <Td className="font-semibold">Overall</Td>
                <Td className="tabular text-right font-semibold">{report.health.overall}</Td>
                <Td className="text-fg-secondary">Based on {pluralFindings(report.findings.length)}</Td>
                <Td className="tabular text-right">{report.findings.length}</Td>
              </tr>
              {report.health.categories.map((c) => (
                <tr key={c.category}>
                  <Td>{CATEGORY_LABEL[c.category]}</Td>
                  <Td className="tabular text-right">{c.score}</Td>
                  <Td className="text-fg-secondary">{c.summary}</Td>
                  <Td className="tabular text-right">{c.findingIds.length}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {SEVERITIES.map((severity) => {
            const findings = report.findings.filter((f) => f.severity === severity && f.status === "open");
            if (findings.length === 0) return null;
            return (
              <div key={severity} className="flex flex-col gap-2 break-inside-avoid">
                <h3 className="flex items-center gap-2">
                  <SeverityBadge severity={severity} />
                  <span className="text-xs text-muted">{pluralFindings(findings.length)}</span>
                </h3>
                <ul className="flex flex-col gap-2">
                  {findings.map((f) => (
                    <li key={f.id} className="text-sm">
                      <p className="font-medium">
                        {f.title}{" "}
                        <span className="tabular text-xs font-normal text-fg-secondary">· {f.location}</span>
                      </p>
                      <p className="text-fg-secondary">{f.recommendation}</p>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {report.findings.some((f) => f.status === "ignored") ? (
            <p className="text-xs text-muted">
              {pluralFindings(report.findings.filter((f) => f.status === "ignored").length)} ignored and not
              listed.
            </p>
          ) : null}
        </div>
      )}
    </ReportSection>
  );
}

export function DecisionsSection({
  pending,
  failed,
  adrs,
}: {
  pending: boolean;
  failed: boolean;
  adrs: readonly Decision[];
}) {
  return (
    <ReportSection title="Architecture decisions">
      {pending ? (
        <Skeleton className="h-24" />
      ) : failed ? (
        <Missing text="Decisions could not be loaded." />
      ) : adrs.length === 0 ? (
        <Missing text="No decisions recorded." />
      ) : (
        <ul className="flex flex-col gap-3">
          {adrs.map((d) => (
            <li key={d.id} className="flex flex-col gap-1 break-inside-avoid text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <span className="tabular text-xs text-fg-secondary">{formatAdrNumber(d.number)}</span>
                <span className="font-medium">{d.title}</span>
                <DecisionStatusBadge status={d.status} />
                <span className="text-xs text-muted">{formatAdrDate(d.date)}</span>
              </p>
              <p className="text-fg-secondary">{d.decision}</p>
            </li>
          ))}
        </ul>
      )}
    </ReportSection>
  );
}
