"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { beginOAuth } from "@/api/auth";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TwoToneTitle } from "@/features/marketing/components/Section";
import type { AuthIntent, OAuthProvider } from "@/types/auth";

import { PasswordForm } from "./PasswordForm";
import { GitHubIcon, GoogleIcon } from "./provider-icons";

const PROVIDERS: ReadonlyArray<{ id: OAuthProvider; label: string; Icon: typeof GoogleIcon }> = [
  { id: "google", label: "Google", Icon: GoogleIcon },
  { id: "github", label: "GitHub", Icon: GitHubIcon },
];

const COPY: Record<AuthIntent, { title: string; description: string }> = {
  login: {
    title: "Welcome back. Sign in.",
    description: "Pick up where you left off: your projects, findings and architecture versions.",
  },
  signup: {
    title: "Start designing. Create your account.",
    description: "Free while in early access. Your first capacity analysis takes minutes.",
  },
};

/** Codes the API puts in ?error= when the OAuth round trip fails. */
const ERRORS: Record<string, string> = {
  access_denied: "Sign-in was cancelled at the provider. You can try again or pick another provider.",
  session_missing: "The provider signed you in, but no session was created. Try again.",
  email_unverified: "Your provider account has no verified email address. Verify it, then try again.",
};
const GENERIC_ERROR = "Sign-in did not complete. Try again, or use the other provider.";

export function AuthPanel({
  intent,
  next,
  error,
  notice,
}: {
  intent: AuthIntent;
  next: string;
  /** ?error= code from a failed OAuth round trip. */
  error?: string;
  /** A confirmation to show above the form, e.g. after a password reset. */
  notice?: string;
}) {
  const router = useRouter();
  const [pending, setPending] = useState<OAuthProvider | null>(null);
  const [startFailed, setStartFailed] = useState(false);
  const copy = COPY[intent];
  const errorMessage = startFailed ? GENERIC_ERROR : error ? (ERRORS[error] ?? GENERIC_ERROR) : null;

  useEffect(() => {
    // Back from the provider restores this page from the bfcache with the buttons still busy.
    const reset = (event: PageTransitionEvent) => event.persisted && setPending(null);
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  async function continueWith(provider: OAuthProvider) {
    setPending(provider);
    setStartFailed(false);
    try {
      const target = await beginOAuth(provider, { next, intent });
      if (target.external) window.location.assign(target.href);
      else router.push(target.href);
    } catch {
      setPending(null);
      setStartFailed(true);
    }
  }

  const switchHref = `${intent === "login" ? "/signup" : "/login"}?${new URLSearchParams({ next })}`;

  return (
    <div className="flex w-full max-w-sm flex-col gap-6">
      <div className="flex flex-col gap-3">
        <p className="eyebrow">{intent === "login" ? "Sign in" : "Get started"}</p>
        <h1 className="headline text-3xl text-fg">
          <TwoToneTitle>{copy.title}</TwoToneTitle>
        </h1>
        <p className="text-base text-pretty text-fg-secondary">{copy.description}</p>
      </div>

      {notice && !errorMessage ? (
        <Alert tone="success" title="Done">
          {notice}
        </Alert>
      ) : null}

      {errorMessage ? (
        <Alert tone="danger" title="Sign-in failed">
          {errorMessage}
        </Alert>
      ) : null}

      <div className="flex flex-col gap-3">
        {PROVIDERS.map(({ id, label, Icon }) => (
          <Button
            key={id}
            variant="secondary"
            className="h-11 w-full gap-3 rounded-md text-sm"
            loading={pending === id}
            disabled={pending !== null}
            onClick={() => void continueWith(id)}
          >
            {pending === id ? null : <Icon className="size-4" />}
            Continue with {label}
          </Button>
        ))}
      </div>

      <div className="flex items-center gap-3 text-xs text-muted">
        <span aria-hidden className="h-px flex-1 bg-default" />
        or with email
        <span aria-hidden className="h-px flex-1 bg-default" />
      </div>

      <PasswordForm intent={intent} next={next} disabled={pending !== null} />

      <div className="flex flex-col gap-3 border-t border-default pt-6 text-sm text-fg-secondary">
        {intent === "login" ? (
          <p>
            New to ArchitectOS?{" "}
            <Link href={switchHref} className="font-medium text-accent-fg underline-offset-4 hover:underline">
              Create an account
            </Link>
          </p>
        ) : (
          <p>
            Already have an account?{" "}
            <Link href={switchHref} className="font-medium text-accent-fg underline-offset-4 hover:underline">
              Sign in
            </Link>
          </p>
        )}
        <p className="text-xs text-muted">
          {intent === "signup" ? "With Google or GitHub, your account is created on first sign-in. " : null}
          From Google and GitHub we only read your name, email address and avatar.
        </p>
      </div>
    </div>
  );
}
