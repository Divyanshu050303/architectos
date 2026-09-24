"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { useRequestPasswordReset, useResetPassword } from "@/hooks/use-auth";
import { ForgotPasswordInputSchema, ResetPasswordInputSchema } from "@/schemas/auth";

import { fieldErrors } from "./form-errors";
import { PasswordInput } from "./PasswordInput";

function Heading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="eyebrow">{eyebrow}</p>
      <h1 className="headline text-3xl text-fg">{title}</h1>
      <p className="text-base text-pretty text-fg-secondary">{description}</p>
    </div>
  );
}

const backToSignIn = (
  <Link href="/login" className="text-sm font-medium text-accent-fg underline-offset-4 hover:underline">
    Back to sign in
  </Link>
);

export function ForgotPasswordForm() {
  const requestReset = useRequestPasswordReset();
  const [error, setError] = useState<string>();
  const [sentTo, setSentTo] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") ?? "");
    const parsed = ForgotPasswordInputSchema.safeParse({ email });
    if (!parsed.success) return setError(fieldErrors(parsed.error, ["email"] as const).email);
    setError(undefined);
    requestReset.mutate(parsed.data, { onSuccess: () => setSentTo(parsed.data.email) });
  }

  if (sentTo) {
    return (
      <div className="flex w-full max-w-sm flex-col gap-8">
        <Heading
          eyebrow="Check your email"
          title="Reset link sent."
          description={`If an account exists for ${sentTo}, it will receive a link to choose a new password. The link expires in 30 minutes.`}
        />
        {backToSignIn}
      </div>
    );
  }

  const failure = requestReset.isError ? getErrorInfo(requestReset.error) : null;
  return (
    <div className="flex w-full max-w-sm flex-col gap-8">
      <Heading
        eyebrow="Password reset"
        title="Forgot your password?"
        description="Enter the email you signed up with and we will send you a link to choose a new one."
      />
      <form method="post" noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label="Email" error={error}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="email"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              name="email"
              autoComplete="email"
              inputMode="email"
              spellCheck={false}
              autoFocus
              className="h-10"
            />
          )}
        </Field>
        {failure ? (
          <Alert tone="danger" title="Could not send the link">
            {failure.message}
          </Alert>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          className="cta-depth h-11 w-full rounded-md text-sm"
          loading={requestReset.isPending}
        >
          Send reset link
        </Button>
      </form>
      {backToSignIn}
    </div>
  );
}

export function ResetPasswordForm({ token }: { token: string | undefined }) {
  const router = useRouter();
  const reset = useResetPassword();
  const [errors, setErrors] = useState<{ password?: string; confirm?: string }>({});

  if (!token) {
    return (
      <div className="flex w-full max-w-sm flex-col gap-8">
        <Heading
          eyebrow="Password reset"
          title="This link is incomplete."
          description="Open the link from the email again, or request a new one."
        />
        <Link
          href="/forgot-password"
          className="text-sm font-medium text-accent-fg underline-offset-4 hover:underline"
        >
          Request a new link
        </Link>
      </div>
    );
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") ?? "");
    const confirm = String(data.get("confirm") ?? "");
    const parsed = ResetPasswordInputSchema.safeParse({ token, password });
    const next: typeof errors = parsed.success ? {} : fieldErrors(parsed.error, ["password"] as const);
    if (password !== confirm) next.confirm = "The passwords do not match.";
    setErrors(next);
    if (!parsed.success || next.confirm) return;
    reset.mutate(parsed.data, { onSuccess: () => router.replace("/login?reset=done") });
  }

  const failure = reset.isError ? getErrorInfo(reset.error) : null;
  return (
    <div className="flex w-full max-w-sm flex-col gap-8">
      <Heading
        eyebrow="Password reset"
        title="Choose a new password."
        description="You will sign in with it from now on. Other devices stay signed in until they sign out."
      />
      <form method="post" noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label="New password" description="At least 8 characters." error={errors.password}>
          {({ id, describedBy, invalid }) => (
            <PasswordInput
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              name="password"
              autoComplete="new-password"
              autoFocus
            />
          )}
        </Field>
        <Field label="Confirm new password" error={errors.confirm}>
          {({ id, describedBy, invalid }) => (
            <PasswordInput
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              name="confirm"
              autoComplete="new-password"
            />
          )}
        </Field>
        {failure ? (
          <Alert tone="danger" title="Could not reset your password">
            {failure.code === "invalid_token" ? (
              <>
                This link has expired or was already used.{" "}
                <Link href="/forgot-password" className="font-medium underline underline-offset-4">
                  Request a new one
                </Link>
                .
              </>
            ) : (
              failure.message
            )}
          </Alert>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          className="cta-depth h-11 w-full rounded-md text-sm"
          loading={reset.isPending}
        >
          Set new password
        </Button>
      </form>
    </div>
  );
}
