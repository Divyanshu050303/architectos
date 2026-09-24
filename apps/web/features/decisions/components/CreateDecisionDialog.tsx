"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toast";
import { useArchitecture } from "@/hooks/use-architecture";
import { useCreateDecision } from "@/hooks/use-decisions";
import { nodeName } from "@/lib/graph";
import { DecisionInputSchema } from "@/schemas/decisions";
import type { DecisionInput } from "@/types/architecture";

import { formatAdrNumber } from "../format";

export interface CreateDecisionDialogProps {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Prefill, e.g. from a validation finding. */
  initial?: Partial<DecisionInput>;
}

type TextField = "title" | "context" | "decision" | "consequences";
type FieldErrors = Partial<Record<TextField, string>>;

/** Record an Architecture Decision Record (ADR). */
export function CreateDecisionDialog({ projectId, open, onOpenChange, initial }: CreateDecisionDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          title="New architecture decision"
          description="Record the context, the decision and its consequences."
          className="max-w-xl"
        >
          <DecisionForm projectId={projectId} initial={initial} onDone={() => onOpenChange(false)} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function DecisionForm({
  projectId,
  initial,
  onDone,
}: {
  projectId: string;
  initial?: Partial<DecisionInput>;
  onDone: () => void;
}) {
  const router = useRouter();
  const create = useCreateDecision(projectId);
  const architecture = useArchitecture(projectId);
  const [values, setValues] = useState<Record<TextField, string>>({
    title: initial?.title ?? "",
    context: initial?.context ?? "",
    decision: initial?.decision ?? "",
    consequences: initial?.consequences ?? "",
  });
  const [errors, setErrors] = useState<FieldErrors>({});
  const relatedNodeIds = initial?.relatedNodeIds ?? [];
  const sourceFindingId = initial?.sourceFindingId ?? null;

  const update = (field: TextField) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const value = event.target.value;
    setValues((v) => ({ ...v, [field]: value }));
    if (errors[field]) setErrors((e) => ({ ...e, [field]: undefined }));
  };

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = DecisionInputSchema.safeParse({ ...values, relatedNodeIds, sourceFindingId });
    if (!parsed.success) {
      const next: FieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0];
        if (typeof key === "string" && key in values && !next[key as TextField]) {
          next[key as TextField] = issue.message;
        }
      }
      setErrors(next);
      return;
    }
    create.mutate(parsed.data, {
      onSuccess: (decision) => {
        toast(`${formatAdrNumber(decision.number)} recorded`, {
          tone: "success",
          description: decision.title,
        });
        onDone();
        router.push(
          `/project/${encodeURIComponent(projectId)}/decisions?adr=${encodeURIComponent(decision.id)}`,
        );
      },
    });
  };

  const arch = architecture.data ?? null;
  const submitError = create.isError ? getErrorInfo(create.error) : null;

  return (
    <form noValidate onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field label="Title" error={errors.title}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={values.title}
            onChange={update("title")}
            placeholder="Use a managed PostgreSQL primary with a standby"
            autoFocus
          />
        )}
      </Field>
      <Field
        label="Context"
        description="The forces at play and why a decision is needed."
        error={errors.context}
      >
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={values.context}
            onChange={update("context")}
          />
        )}
      </Field>
      <Field label="Decision" error={errors.decision}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={values.decision}
            onChange={update("decision")}
          />
        )}
      </Field>
      <Field
        label="Consequences"
        description="Optional. Trade-offs and follow-up work."
        error={errors.consequences}
      >
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={values.consequences}
            onChange={update("consequences")}
          />
        )}
      </Field>

      {relatedNodeIds.length > 0 || sourceFindingId ? (
        <div className="flex flex-col gap-1.5 text-xs text-fg-secondary">
          {relatedNodeIds.length > 0 ? (
            <p>
              Related components:{" "}
              <span className="text-fg">
                {relatedNodeIds.map((id) => (arch ? nodeName(arch, id) : id)).join(", ")}
              </span>
            </p>
          ) : null}
          {sourceFindingId ? (
            <p>
              From finding <span className="tabular text-fg">{sourceFindingId}</span>
            </p>
          ) : null}
        </div>
      ) : null}

      {submitError ? (
        <ErrorState
          title="The decision was not saved."
          message={submitError.message}
          requestId={submitError.requestId}
        />
      ) : null}

      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="ghost">
            Cancel
          </Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create ADR
        </Button>
      </DialogFooter>
    </form>
  );
}
