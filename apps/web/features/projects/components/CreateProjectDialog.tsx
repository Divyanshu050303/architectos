"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toast";
import { projectHref } from "@/config/navigation";
import { useCreateProject } from "@/hooks/use-projects";
import { ProjectInputSchema } from "@/schemas/projects";

import { useCreateProjectDialog } from "../create-project-store";

type FieldErrors = Partial<Record<"name" | "description", string>>;

export interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateProjectDialog({ open, onOpenChange }: CreateProjectDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent
          title="Create system"
          description="Name the system you are designing. You will describe its requirements next."
        >
          <CreateProjectForm onDone={() => onOpenChange(false)} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function CreateProjectForm({ onDone }: { onDone: () => void }) {
  const router = useRouter();
  const createProject = useCreateProject();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = ProjectInputSchema.safeParse({ name, description });
    if (!parsed.success) {
      const next: FieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0];
        if ((key === "name" || key === "description") && !next[key]) next[key] = issue.message;
      }
      setErrors(next);
      return;
    }
    setErrors({});
    createProject.mutate(parsed.data, {
      onSuccess: (project) => {
        toast(`${project.name} created`, {
          tone: "success",
          description: "Describe its requirements to generate a starting architecture.",
        });
        onDone();
        router.push(projectHref(project.id, "requirements"));
      },
    });
  }

  const error = createProject.isError ? getErrorInfo(createProject.error) : null;

  return (
    <form noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Field label="Name" error={errors.name}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Food delivery platform"
            autoComplete="off"
            autoFocus
          />
        )}
      </Field>
      <Field
        label="Description"
        description="Optional. One or two sentences about what the system does."
        error={errors.description}
      >
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
          />
        )}
      </Field>
      {error ? (
        <ErrorState
          title="The system could not be created."
          message={error.message}
          requestId={error.requestId}
          noChangesApplied
        />
      ) : null}
      <DialogFooter className="mt-1">
        <DialogClose asChild>
          <Button type="button" variant="ghost">
            Cancel
          </Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={createProject.isPending}>
          Create system
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The single instance mounted by the app shells; opened via `openCreateProjectDialog()`. */
export function CreateProjectDialogHost() {
  const open = useCreateProjectDialog((s) => s.open);
  const setOpen = useCreateProjectDialog((s) => s.setOpen);
  return <CreateProjectDialog open={open} onOpenChange={setOpen} />;
}
