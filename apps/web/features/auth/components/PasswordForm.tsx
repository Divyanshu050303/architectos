"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { usePasswordSignIn, useSignUp } from "@/hooks/use-auth";
import { SignInInputSchema, SignUpInputSchema } from "@/schemas/auth";
import type { AuthIntent } from "@/types/auth";

import { fieldErrors } from "./form-errors";
import { PasswordInput } from "./PasswordInput";

const KEYS = ["name", "email", "password"] as const;
type Errors = Partial<Record<(typeof KEYS)[number], string>>;

/** Email and password sign-in or sign-up; on success the session is cached and we go to `next`. */
export function PasswordForm({
  intent,
  next,
  disabled = false,
}: {
  intent: AuthIntent;
  next: string;
  disabled?: boolean;
}) {
  const router = useRouter();
  const signIn = usePasswordSignIn();
  const signUp = useSignUp();
  const mutation = intent === "login" ? signIn : signUp;
  const [errors, setErrors] = useState<Errors>({});

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Uncontrolled inputs: anything typed before hydration is still in the DOM, so read it from there.
    const data = new FormData(event.currentTarget);
    const [name, email, password] = KEYS.map((key) => String(data.get(key) ?? ""));
    const onSuccess = () => router.replace(next);
    const onError = (error: unknown) => {
      if (getErrorInfo(error).code === "email_taken") {
        setErrors({ email: "An account with this email already exists. Sign in instead." });
      }
    };
    if (intent === "login") {
      const parsed = SignInInputSchema.safeParse({ email, password });
      if (!parsed.success) return setErrors(fieldErrors(parsed.error, KEYS));
      setErrors({});
      signIn.mutate(parsed.data, { onSuccess, onError });
    } else {
      const parsed = SignUpInputSchema.safeParse({ name, email, password });
      if (!parsed.success) return setErrors(fieldErrors(parsed.error, KEYS));
      setErrors({});
      signUp.mutate(parsed.data, { onSuccess, onError });
    }
  }

  const error = mutation.isError ? getErrorInfo(mutation.error) : null;
  // email_taken is shown on the field; everything else as one message above the button.
  const formError =
    error && error.code !== "email_taken"
      ? error.code === "invalid_credentials"
        ? "Email or password is incorrect."
        : error.message
      : null;

  return (
    <form method="post" noValidate onSubmit={handleSubmit} className="flex flex-col gap-4">
      {intent === "signup" ? (
        <Field label="Name" error={errors.name}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              name="name"
              autoComplete="name"
              className="h-10"
            />
          )}
        </Field>
      ) : null}
      <Field label="Email" error={errors.email}>
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
            className="h-10"
          />
        )}
      </Field>
      <Field
        label="Password"
        description={intent === "signup" ? "At least 8 characters." : undefined}
        error={errors.password}
      >
        {({ id, describedBy, invalid }) => (
          <PasswordInput
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            name="password"
            autoComplete={intent === "login" ? "current-password" : "new-password"}
          />
        )}
      </Field>
      {intent === "login" ? (
        <Link
          href="/forgot-password"
          className="-mt-2 self-end text-xs font-medium text-accent-fg underline-offset-4 hover:underline"
        >
          Forgot password?
        </Link>
      ) : null}
      {formError ? (
        <Alert
          tone="danger"
          title={intent === "login" ? "Could not sign in" : "Could not create your account"}
        >
          {formError}
        </Alert>
      ) : null}
      <Button
        type="submit"
        variant="primary"
        className="cta-depth h-11 w-full rounded-md text-sm"
        loading={mutation.isPending}
        disabled={disabled}
      >
        {intent === "login" ? "Sign in" : "Create account"}
      </Button>
    </form>
  );
}
