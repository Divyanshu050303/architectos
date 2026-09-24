"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";

import { Input } from "@/components/ui/input";

import type { NodeInspectorProps } from "./types";

/** Inline rename of a component; emits RENAME_COMPONENT. */
export function NameEditor({
  node,
  editable,
  onCommand,
}: Pick<NodeInspectorProps, "node" | "editable" | "onCommand">) {
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <div className="group flex min-w-0 items-center gap-1.5">
        <h2 className="truncate text-base font-semibold text-fg">{node.name}</h2>
        {editable ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            aria-label={`Rename ${node.name}`}
            className="rounded-sm p-1 text-muted opacity-60 group-hover:opacity-100 hover:bg-surface-2 hover:text-fg focus-visible:opacity-100"
          >
            <Pencil aria-hidden className="size-3.5" />
          </button>
        ) : null}
      </div>
    );
  }

  function commit(value: string) {
    setEditing(false);
    const name = value.trim();
    if (name && name !== node.name) onCommand({ type: "RENAME_COMPONENT", nodeId: node.id, name });
  }

  return (
    <Input
      autoFocus
      aria-label="Component name"
      defaultValue={node.name}
      className="h-8 text-base font-semibold"
      onBlur={(event) => commit(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") commit(event.currentTarget.value);
        if (event.key === "Escape") {
          event.stopPropagation();
          setEditing(false);
        }
      }}
    />
  );
}
