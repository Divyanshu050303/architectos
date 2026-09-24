"use client";

import { ArrowRight, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatCompact } from "@/lib/formatting";
import { nodeName } from "@/lib/graph";
import type { Architecture, ArchitectureEdge } from "@/types/architecture";

import type { ArchitectureCommand } from "../types";

export interface ConnectionEditorProps {
  edge: ArchitectureEdge;
  architecture: Pick<Architecture, "nodes" | "edges">;
  /** Backend throughput for this connection, when capacity has been analyzed. */
  rps: number | null;
  editable: boolean;
  onCommand: (command: ArchitectureCommand) => void;
  onSelectNode: (nodeId: string) => void;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <dt className="text-fg-secondary">{label}</dt>
      <dd className="text-right text-fg">{children}</dd>
    </div>
  );
}

/** Details of a selected connection. Flags are read-only until the IR gains edit commands. */
export function ConnectionEditor({
  edge,
  architecture,
  rps,
  editable,
  onCommand,
  onSelectNode,
}: ConnectionEditorProps) {
  const source = nodeName(architecture, edge.source);
  const target = nodeName(architecture, edge.target);

  return (
    <div className="flex flex-col gap-4 px-4 py-3">
      <header className="flex flex-col gap-1.5">
        <span className="label-caps">Connection</span>
        <h2 className="flex flex-wrap items-center gap-1.5 text-base font-semibold text-fg">
          <button type="button" className="hover:underline" onClick={() => onSelectNode(edge.source)}>
            {source}
          </button>
          <ArrowRight aria-label="to" className="size-4 text-muted" />
          <button type="button" className="hover:underline" onClick={() => onSelectNode(edge.target)}>
            {target}
          </button>
        </h2>
        {edge.label ? <p className="text-xs text-fg-secondary">{edge.label}</p> : null}
      </header>

      <dl className="divide-y divide-default">
        <Row label="Protocol">
          <span className="tabular">{edge.protocol ?? "—"}</span>
        </Row>
        <Row label="Call style">{edge.synchronous ? "Synchronous" : "Asynchronous"}</Row>
        <Row label="Dependency">
          {edge.critical ? <Badge tone="warning">Critical</Badge> : <Badge>Non-critical</Badge>}
        </Row>
        {rps !== null ? (
          <Row label="Throughput">
            <span className="tabular">{formatCompact(rps)} RPS</span>
          </Row>
        ) : null}
      </dl>

      {editable ? (
        <div>
          <Button
            size="sm"
            variant="danger"
            onClick={() => onCommand({ type: "REMOVE_CONNECTIONS", edgeIds: [edge.id] })}
          >
            <Trash2 aria-hidden className="size-3.5" />
            Delete connection
          </Button>
        </div>
      ) : null}
    </div>
  );
}
