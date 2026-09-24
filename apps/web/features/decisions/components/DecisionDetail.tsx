import Link from "next/link";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import type { Decision } from "@/types/architecture";

import { DecisionStatusBadge, formatAdrDate, formatAdrNumber } from "../format";

export interface DecisionDetailProps {
  projectId: string;
  decision: Decision;
  nodeName: (nodeId: string) => string;
}

export function DecisionDetail({ projectId, decision, nodeName }: DecisionDetailProps) {
  const base = `/project/${encodeURIComponent(projectId)}`;
  return (
    <article aria-labelledby="adr-title" className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="tabular text-fg-secondary">{formatAdrNumber(decision.number)}</span>
          <DecisionStatusBadge status={decision.status} />
          <ProvenanceTag kind="fact" label="Recorded decision" />
          <time dateTime={decision.date}>{formatAdrDate(decision.date)}</time>
        </div>
        <h2 id="adr-title" className="text-base font-semibold text-fg">
          {decision.title}
        </h2>
        {decision.sourceFindingId ? (
          <p className="text-xs text-fg-secondary">
            Created from finding{" "}
            <Link href={`${base}/validation`} className="tabular text-fg underline underline-offset-2">
              {decision.sourceFindingId}
            </Link>
          </p>
        ) : null}
      </header>

      <Section title="Context" body={decision.context} />
      <Section title="Decision" body={decision.decision} />
      <Section title="Consequences" body={decision.consequences || "No consequences recorded."} />

      <section className="flex flex-col gap-2">
        <h3 className="label-caps">Related components</h3>
        {decision.relatedNodeIds.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {decision.relatedNodeIds.map((id) => (
              <li key={id}>
                <Link
                  href={`${base}/architecture?highlight=${encodeURIComponent(id)}&node=${encodeURIComponent(id)}`}
                  className="inline-flex h-6 items-center rounded-sm border border-default bg-surface-2 px-2 text-xs text-fg hover:border-strong"
                >
                  {nodeName(id)}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No components linked.</p>
        )}
      </section>
    </article>
  );
}

function Section({ title, body }: { title: string; body: string }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="label-caps">{title}</h3>
      <p className="text-sm whitespace-pre-line text-fg">{body}</p>
    </section>
  );
}
