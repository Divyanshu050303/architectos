"use client";

import { CheckCircle2, CircleDashed, ScanSearch } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import type { DiscoveryConnector, DiscoveryOptions } from "@/types/discovery";

import { CONNECTOR_META } from "../meta";

export interface ConnectorCardProps {
  connector: DiscoveryConnector;
  starting: boolean;
  /** Another scan is starting: block concurrent starts. */
  disabled: boolean;
  onStart: (options: DiscoveryOptions) => void;
}

/** One discovery source with its status, scan option and [Start scan] (spec §44 CONNECT). */
export function ConnectorCard({ connector, starting, disabled, onStart }: ConnectorCardProps) {
  const meta = CONNECTOR_META[connector.kind];
  const [value, setValue] = useState("");
  const connected = connector.status === "connected";
  const headingId = `connector-${connector.kind}`;

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-4 rounded-md border border-default bg-surface p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-sm border border-default bg-surface-2 text-fg-secondary">
            <meta.Icon aria-hidden className="size-4" />
          </span>
          <div className="flex min-w-0 flex-col">
            <h3 id={headingId} className="text-sm font-semibold text-fg">
              {connector.label}
            </h3>
            <p className="tabular truncate text-2xs text-muted">
              {connector.details ?? "No credentials configured"}
            </p>
          </div>
        </div>
        {connected ? (
          <Badge tone="accent">
            <CheckCircle2 aria-hidden />
            Connected
          </Badge>
        ) : (
          <Badge tone="neutral">
            <CircleDashed aria-hidden />
            Not connected
          </Badge>
        )}
      </div>

      <p className="text-sm text-fg-secondary">{connector.description}</p>

      <form
        className="mt-auto flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          onStart({ [meta.option.key]: value });
        }}
      >
        <Field label={meta.option.label} description={meta.option.description}>
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              className="tabular"
              placeholder={`e.g. ${meta.option.placeholder}`}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          )}
        </Field>
        <Button
          type="submit"
          variant={connected ? "primary" : "secondary"}
          loading={starting}
          disabled={disabled}
          aria-label={`Start ${connector.label} scan`}
        >
          {starting ? null : <ScanSearch aria-hidden className="size-4" />}
          {starting ? "Starting…" : "Start scan"}
        </Button>
        {connected ? null : (
          <p className="text-xs text-muted">
            Connect {connector.label} with read-only credentials before scanning.
          </p>
        )}
      </form>
    </section>
  );
}
