"use client";

import { Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { toast } from "@/components/ui/toast";
import { useSaveRequirements } from "@/hooks/use-requirements";
import { matchesShortcut } from "@/lib/keyboard";
import { RequirementsFormSchema } from "@/schemas/requirements";
import type { Requirements } from "@/types/project";

import {
  type RequirementsFormErrors,
  type RequirementsFormField,
  sameValues,
  toFormValues,
  toRequirementsInput,
  wholeNumberErrors,
} from "../form-values";

const SAVE_SHORTCUT = "mod+s";

interface NumberFieldSpec {
  field: RequirementsFormField;
  label: string;
  description: string;
  placeholder: string;
}

const NUMBER_FIELDS: readonly NumberFieldSpec[] = [
  {
    field: "dailyActiveUsers",
    label: "Daily active users",
    description: "Users per day.",
    placeholder: "2,400,000",
  },
  { field: "peakRps", label: "Peak RPS", description: "Requests per second at peak.", placeholder: "31,000" },
  {
    field: "availabilityTarget",
    label: "Availability (%)",
    description: "e.g. 99.9 or 99.95.",
    placeholder: "99.9",
  },
  {
    field: "p99LatencyMs",
    label: "P99 latency (ms)",
    description: "Target for API responses.",
    placeholder: "300",
  },
  {
    field: "dataRetentionDays",
    label: "Data retention (days)",
    description: "How long records are kept.",
    placeholder: "365",
  },
];

export interface RequirementsFormProps {
  projectId: string;
  requirements: Requirements;
  onDirtyChange?: (dirty: boolean) => void;
}

/**
 * Requirements editor. Re-key it on `requirements.updatedAt` so a save (or a refetch)
 * resets the baseline.
 */
export function RequirementsForm({ projectId, requirements, onDirtyChange }: RequirementsFormProps) {
  const [initial] = useState(() => toFormValues(requirements));
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<RequirementsFormErrors>({});
  const save = useSaveRequirements(projectId);
  const formRef = useRef<HTMLFormElement>(null);
  const dirty = !sameValues(values, initial);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  // Dirty-state guard for reloads and tab closes.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  function update(field: RequirementsFormField, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = RequirementsFormSchema.safeParse(values);
    if (!parsed.success) {
      const next: RequirementsFormErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0];
        if (typeof key === "string" && key in values && !next[key as RequirementsFormField]) {
          next[key as RequirementsFormField] = issue.message;
        }
      }
      setErrors(next);
      focusFirstError(next);
      return;
    }
    const integerErrors = wholeNumberErrors(parsed.data);
    if (Object.keys(integerErrors).length > 0) {
      setErrors(integerErrors);
      focusFirstError(integerErrors);
      return;
    }
    setErrors({});
    save.mutate(toRequirementsInput(parsed.data), {
      onSuccess: () => toast("Requirements saved", { tone: "success" }),
    });
  }

  function focusFirstError(next: RequirementsFormErrors) {
    const first = (Object.keys(values) as RequirementsFormField[]).find((key) => next[key]);
    if (first) formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`)?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLFormElement>) {
    if (matchesShortcut(event, SAVE_SHORTCUT)) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  const saveError = save.isError ? getErrorInfo(save.error) : null;

  return (
    <form
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      onKeyDown={onKeyDown}
      aria-label="Requirements"
      className="flex flex-col gap-6"
    >
      <section className="flex flex-col gap-4">
        <h2 className="label-caps">System</h2>
        <Field
          label="Description"
          description="What the system does, who uses it and anything unusual about its workload."
          error={errors.description}
        >
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              name="description"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              rows={6}
              value={values.description}
              onChange={(e) => update("description", e.target.value)}
              placeholder="A food delivery platform where customers order from nearby restaurants and couriers deliver in under 40 minutes…"
            />
          )}
        </Field>
        <Field label="Functional requirements" description="One per line." error={errors.functional}>
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              name="functional"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              rows={5}
              value={values.functional}
              onChange={(e) => update("functional", e.target.value)}
              placeholder={
                "Customers browse restaurants\nCustomers place and pay for orders\nCouriers see live assignments"
              }
            />
          )}
        </Field>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="label-caps">Scale and targets</h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {NUMBER_FIELDS.map((spec) => (
            <Field
              key={spec.field}
              label={spec.label}
              description={spec.description}
              error={errors[spec.field]}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  name={spec.field}
                  inputMode="decimal"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  className="tabular"
                  value={values[spec.field]}
                  onChange={(e) => update(spec.field, e.target.value)}
                  placeholder={spec.placeholder}
                />
              )}
            </Field>
          ))}
          <Field
            label="Regions"
            description="Comma-separated, e.g. us-east-1, eu-west-1."
            error={errors.regions}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                name="regions"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                className="tabular"
                value={values.regions}
                onChange={(e) => update("regions", e.target.value)}
                placeholder="us-east-1"
              />
            )}
          </Field>
        </div>
      </section>

      {saveError ? (
        <ErrorState
          title="Requirements were not saved."
          message={saveError.message}
          requestId={saveError.requestId}
          noChangesApplied
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-3 border-t border-default pt-4">
        <Button type="submit" variant="primary" loading={save.isPending} disabled={!dirty && !save.isError}>
          <Save aria-hidden className="size-4" />
          Save requirements
        </Button>
        <Kbd shortcut={SAVE_SHORTCUT} />
        <span role="status" className="text-xs text-muted">
          {dirty ? "Unsaved changes" : requirements.updatedAt ? "All changes saved" : "Not saved yet"}
        </span>
        {dirty ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setValues(initial);
              setErrors({});
            }}
          >
            Discard changes
          </Button>
        ) : null}
      </div>
    </form>
  );
}
