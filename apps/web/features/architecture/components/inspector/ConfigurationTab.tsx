"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { ArchitectureNode } from "@/types/architecture";

import { formatConfigValue, humanizeKey } from "./shared";
import type { NodeInspectorProps } from "./types";

function ConfigValueEditor({
  node,
  configKey,
  value,
  onCommand,
}: {
  node: ArchitectureNode;
  configKey: string;
  value: unknown;
  onCommand: NodeInspectorProps["onCommand"];
}) {
  const label = humanizeKey(configKey);
  // Keyed by value so an undo/redo resets the uncontrolled input.
  const key = `${node.id}:${configKey}:${String(value)}`;

  if (typeof value === "boolean") {
    return (
      <Checkbox
        key={key}
        aria-label={label}
        defaultChecked={value}
        className="align-middle"
        onCheckedChange={(checked) =>
          onCommand({
            type: "UPDATE_CONFIGURATION",
            nodeId: node.id,
            configuration: { [configKey]: checked === true },
          })
        }
      />
    );
  }

  const numeric = typeof value === "number";
  function commit(raw: string) {
    if (numeric) {
      const next = Number(raw);
      if (raw.trim() === "" || !Number.isFinite(next) || next === value) return;
      if (configKey === "replicas") onCommand({ type: "CHANGE_REPLICAS", nodeId: node.id, replicas: next });
      else onCommand({ type: "UPDATE_CONFIGURATION", nodeId: node.id, configuration: { [configKey]: next } });
      return;
    }
    if (raw === value) return;
    onCommand({ type: "UPDATE_CONFIGURATION", nodeId: node.id, configuration: { [configKey]: raw } });
  }

  return (
    <Input
      key={key}
      aria-label={label}
      type={numeric ? "number" : "text"}
      inputMode={numeric ? "numeric" : undefined}
      min={configKey === "replicas" ? 0 : undefined}
      step={configKey === "replicas" ? 1 : undefined}
      defaultValue={String(value)}
      className="tabular h-7 w-28 text-right text-xs"
      onBlur={(event) => commit(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}

export function ConfigurationTab({
  node,
  editable,
  onCommand,
}: Pick<NodeInspectorProps, "node" | "editable" | "onCommand">) {
  const entries = Object.entries(node.configuration);
  return (
    <dl className="divide-y divide-default">
      {entries.map(([key, value]) => {
        const scalar = typeof value === "number" || typeof value === "string" || typeof value === "boolean";
        return (
          <div key={key} className="flex items-center justify-between gap-3 py-1.5">
            <dt className="text-sm text-fg-secondary">{humanizeKey(key)}</dt>
            <dd className="tabular min-w-0 text-right text-xs text-fg">
              {editable && scalar ? (
                <ConfigValueEditor node={node} configKey={key} value={value} onCommand={onCommand} />
              ) : (
                <span className="break-all">{formatConfigValue(value)}</span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
