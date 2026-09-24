import { ProvenanceTag, type ProvenanceKind } from "@/components/feedback/ProvenanceTag";
import { formatNumber } from "@/lib/formatting";
import type { Evidence } from "@/types/architecture";

export const EVIDENCE_KIND_META: Record<Evidence["kind"], { kind: ProvenanceKind; label: string }> = {
  calculation: { kind: "calculated", label: "Calculation" },
  constraint: { kind: "evidence", label: "Component constraint" },
  rule: { kind: "evidence", label: "Validation rule" },
  benchmark: { kind: "evidence", label: "Benchmark" },
};

/** Presentational evidence record (spec §34): claim, calculation steps, source and assumptions. */
export function EvidenceDetail({ evidence, headingLevel = 3 }: { evidence: Evidence; headingLevel?: 2 | 3 }) {
  const tag = EVIDENCE_KIND_META[evidence.kind];
  return (
    <article className="flex flex-col gap-5" aria-label={`Evidence: ${evidence.claim}`}>
      <div className="flex flex-wrap items-center gap-2">
        <ProvenanceTag kind={tag.kind} label={tag.label} />
        <span className="tabular text-2xs text-muted">{evidence.id}</span>
      </div>

      <Section title="Claim" level={headingLevel}>
        <p className="text-sm font-medium text-fg">{evidence.claim}</p>
      </Section>

      <Section title="Calculation" level={headingLevel}>
        {evidence.calculations.length > 0 ? (
          <dl className="flex flex-col divide-y divide-default rounded-md border border-default bg-surface">
            {evidence.calculations.map((row, index) => (
              <div
                key={`${row.label}-${index}`}
                className="flex items-baseline justify-between gap-4 px-3 py-2"
              >
                <dt className="text-sm text-fg-secondary">{row.label}</dt>
                <dd className="tabular text-right text-sm text-fg">
                  {typeof row.value === "number" ? formatNumber(row.value) : row.value}
                  {row.unit ? <span className="ml-1 text-muted">{row.unit}</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted">No calculation steps were recorded.</p>
        )}
      </Section>

      <Section title="Source" level={headingLevel}>
        <p className="text-sm text-fg">{evidence.source}</p>
      </Section>

      <Section title="Assumptions" level={headingLevel}>
        {evidence.assumptions.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {evidence.assumptions.map((assumption) => (
              <li key={assumption.id} className="flex gap-3 text-sm">
                <span className="tabular shrink-0 text-xs text-fg-secondary">{assumption.id}</span>
                <span className="text-fg">{assumption.statement}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No assumptions.</p>
        )}
      </Section>
    </article>
  );
}

function Section({ title, level, children }: { title: string; level: 2 | 3; children: React.ReactNode }) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section className="flex flex-col gap-2">
      <Heading className="label-caps">{title}</Heading>
      {children}
    </section>
  );
}
