"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { LoadingSteps } from "@/components/feedback/LoadingSteps";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toast";
import { projectHref } from "@/config/navigation";
import { useArchitecture, useGenerateArchitecture, useJob } from "@/hooks/use-architecture";
import { usePageAction, useRegisterCommands } from "@/hooks/use-command";

export interface GenerationPanelProps {
  projectId: string;
  /** Requirements have been saved at least once. */
  requirementsSaved: boolean;
  /** The requirements form has unsaved edits. */
  requirementsDirty: boolean;
}

/** "Generate architecture" with explicit job progress (spec §31, §48, §87). */
export function GenerationPanel({ projectId, requirementsSaved, requirementsDirty }: GenerationPanelProps) {
  const router = useRouter();
  const architecture = useArchitecture(projectId);
  const generate = useGenerateArchitecture(projectId);
  const [jobId, setJobId] = useState<string | null>(null);
  const jobQuery = useJob(jobId);
  const job = jobQuery.data;

  const running = generate.isPending || job?.status === "queued" || job?.status === "running";
  const succeeded = job?.status === "succeeded";
  const blockedReason = !requirementsSaved
    ? "Save your requirements first. Generation reads the saved version."
    : requirementsDirty
      ? "You have unsaved changes. Save them so the architecture reflects them."
      : null;

  function start() {
    if (running || blockedReason) return;
    setJobId(null);
    generate.mutate(undefined, { onSuccess: (started) => setJobId(started.id) });
  }

  // Announce success and move to the workspace once.
  const handledJob = useRef<string | null>(null);
  const version = job?.result?.architectureVersion;
  useEffect(() => {
    if (!job || job.status !== "succeeded" || handledJob.current === job.id) return;
    handledJob.current = job.id;
    toast(version ? `Architecture v${version} generated` : "Architecture generated", {
      tone: "success",
      description: "Review the AI proposal on the canvas.",
    });
    router.push(projectHref(projectId, "architecture"));
  }, [job, version, projectId, router]);

  const generateFromCommand = () => {
    if (blockedReason) toast("Architecture not generated", { description: blockedReason });
    else start();
  };

  useRegisterCommands([
    {
      id: "architecture.generate",
      label: "Generate architecture",
      group: "Architecture",
      keywords: ["ai", "create", "requirements"],
      run: generateFromCommand,
    },
  ]);

  // "Generate architecture" from the palette on any project page lands here with ?action=generate.
  usePageAction("generate", generateFromCommand, !running);

  const current = architecture.data;
  const startError = generate.isError ? getErrorInfo(generate.error) : null;
  const pollError = jobQuery.isError ? getErrorInfo(jobQuery.error) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Architecture</CardTitle>
        <ProvenanceTag kind="ai" label="AI generated" />
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-sm text-fg-secondary">
          ArchitectOS proposes a starting architecture from your saved requirements. Every assumption it makes
          is labelled so you can review it.
        </p>

        {current ? (
          <Alert tone="warning" title={`Generating creates v${current.version + 1}`}>
            The current v{current.version} stays in version history and is not modified.
          </Alert>
        ) : null}

        {blockedReason && !running ? <p className="text-xs text-muted">{blockedReason}</p> : null}

        {job && job.status !== "failed" ? (
          <LoadingSteps
            title={succeeded ? "Architecture generated" : "Generating architecture"}
            steps={job.steps}
          />
        ) : null}

        {job?.status === "failed" ? (
          <ErrorState
            title="Architecture generation failed."
            message={job.error?.message ?? "The generation job did not complete."}
            noChangesApplied
            onRetry={start}
            details={`Job ID: ${job.id}${job.error ? `\nCode: ${job.error.code}` : ""}`}
          />
        ) : null}

        {startError ? (
          <ErrorState
            title="Architecture generation failed."
            message={startError.message}
            requestId={startError.requestId}
            noChangesApplied
            onRetry={start}
          />
        ) : null}

        {pollError && !succeeded ? (
          <ErrorState
            title="Lost track of generation progress."
            message={`${pollError.message} Generation may still be running on the server.`}
            requestId={pollError.requestId}
            onRetry={() => void jobQuery.refetch()}
          />
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {succeeded ? (
            <Button asChild variant="primary">
              <Link href={projectHref(projectId, "architecture")}>
                View architecture
                <ArrowRight aria-hidden className="size-4" />
              </Link>
            </Button>
          ) : (
            <Button variant="ai" onClick={start} loading={running} disabled={Boolean(blockedReason)}>
              {running ? null : <span aria-hidden>✦</span>}
              {running ? "Generating…" : current ? "Generate new version" : "Generate architecture"}
            </Button>
          )}
          {current && !succeeded ? (
            <Button asChild variant="ghost">
              <Link href={projectHref(projectId, "architecture")}>Open current v{current.version}</Link>
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
