"use client";

import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Textarea } from "@/components/ui/input";
import { RadioGroup, type RadioOption } from "@/components/ui/radio-group";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { THEME_OPTIONS } from "@/components/navigation/ThemeToggle";
import { config } from "@/config/env";
import { useProject, useUpdateProject } from "@/hooks/use-projects";
import { type ThemePreference, useTheme } from "@/providers/theme-provider";
import { ProjectInputSchema } from "@/schemas/projects";
import type { Project } from "@/types/project";

import { ProjectLoadError } from "./ProjectLoadError";

export function ProjectSettings({ projectId }: { projectId: string }) {
  const { data: project, isPending, isError, error, refetch } = useProject(projectId);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Settings" description="Project details, appearance and environment." />
      {isPending ? (
        <SkeletonGroup label="Loading settings">
          <Skeleton className="h-48" />
        </SkeletonGroup>
      ) : isError ? (
        <ProjectLoadError error={error} onRetry={() => void refetch()} />
      ) : (
        <ProjectDetailsForm key={project.updatedAt} project={project} />
      )}
      <AppearanceCard />
      <EnvironmentCard />
    </div>
  );
}

type DetailErrors = Partial<Record<"name" | "description", string>>;

function ProjectDetailsForm({ project }: { project: Project }) {
  const update = useUpdateProject(project.id);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [errors, setErrors] = useState<DetailErrors>({});
  const dirty = name !== project.name || description !== project.description;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = ProjectInputSchema.safeParse({ name, description });
    if (!parsed.success) {
      const next: DetailErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0];
        if ((key === "name" || key === "description") && !next[key]) next[key] = issue.message;
      }
      setErrors(next);
      return;
    }
    setErrors({});
    update.mutate(parsed.data, { onSuccess: () => toast("Project details saved", { tone: "success" }) });
  }

  const saveError = update.isError ? getErrorInfo(update.error) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Project</CardTitle>
        <span className="tabular text-2xs text-muted">{project.id}</span>
      </CardHeader>
      <CardContent>
        <form noValidate onSubmit={handleSubmit} className="flex max-w-xl flex-col gap-4">
          <Field label="Name" error={errors.name}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="off"
              />
            )}
          </Field>
          <Field label="Description" error={errors.description}>
            {({ id, describedBy, invalid }) => (
              <Textarea
                id={id}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            )}
          </Field>
          {saveError ? (
            <ErrorState
              title="Project details were not saved."
              message={saveError.message}
              requestId={saveError.requestId}
              noChangesApplied
            />
          ) : null}
          <div>
            <Button type="submit" variant="primary" disabled={!dirty} loading={update.isPending}>
              Save changes
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

const THEME_OPTIONS_ITEMS: ReadonlyArray<RadioOption<ThemePreference>> = THEME_OPTIONS.map(
  ({ value, label, Icon }) => ({ value, label, icon: <Icon aria-hidden /> }),
);

function AppearanceCard() {
  const { preference, setPreference } = useTheme();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Appearance</CardTitle>
      </CardHeader>
      <CardContent>
        <RadioGroup
          variant="segmented"
          label="Theme"
          value={preference}
          onValueChange={setPreference}
          options={THEME_OPTIONS_ITEMS}
          className="max-w-sm"
        />
      </CardContent>
    </Card>
  );
}

function EnvironmentCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Environment</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
          <dt className="text-muted">API URL</dt>
          <dd className="tabular truncate text-fg">{config.apiUrl}</dd>
          <dt className="text-muted">Mock mode</dt>
          <dd className="flex items-center gap-2">
            {config.useMocks ? (
              <>
                <Badge tone="info">On</Badge>
                <span className="text-xs text-fg-secondary">
                  Responses come from the in-browser mock backend.
                </span>
              </>
            ) : (
              <Badge tone="neutral">Off</Badge>
            )}
          </dd>
          <dt className="text-muted">App environment</dt>
          <dd className="tabular text-fg">{config.appEnv}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}
