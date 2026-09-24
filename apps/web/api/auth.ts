/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/auth.py.
 *   GET  /auth/oauth/{provider}/start?next=&intent=   browser navigation, not XHR: redirects to
 *        Google / GitHub, handles the callback and state check, sets the session cookie, then
 *        redirects to {app}/auth/callback?next= (or ?error=<code> on failure).
 *   POST /auth/login             { email, password }  → { user }, sets the cookie; 401 invalid_credentials
 *   POST /auth/signup            { name, email, password } → 201 { user }, sets the cookie; 409 email_taken
 *   POST /auth/password/forgot   { email } → 204 always (no account enumeration); emails a reset link
 *        to {app}/reset-password?token=
 *   POST /auth/password/reset    { token, password } → 204; 400 invalid_token (expired or used)
 *   GET  /auth/session           { user } or 401
 *   POST /auth/logout            clears the session cookie
 */
import { config } from "@/config/env";
import { safeNextPath } from "@/lib/safe-redirect";
import { SessionSchema } from "@/schemas/auth";
import type {
  AuthIntent,
  AuthUser,
  ForgotPasswordInput,
  OAuthProvider,
  ResetPasswordInput,
  SignInInput,
  SignUpInput,
} from "@/types/auth";

import { isApiError, request, requestVoid } from "./client";

/** The signed-in user, or null when there is no session. Other failures throw. */
export async function getSession(signal?: AbortSignal): Promise<AuthUser | null> {
  try {
    const session = await request(SessionSchema, {
      method: "GET",
      path: "/auth/session",
      signal,
      retries: 0,
    });
    return session.user;
  } catch (error) {
    if (isApiError(error) && error.status === 401) return null;
    throw error;
  }
}

export async function signIn(input: SignInInput): Promise<AuthUser> {
  const session = await request(SessionSchema, { method: "POST", path: "/auth/login", body: input });
  return session.user;
}

export async function signUp(input: SignUpInput): Promise<AuthUser> {
  const session = await request(SessionSchema, { method: "POST", path: "/auth/signup", body: input });
  return session.user;
}

export function requestPasswordReset(input: ForgotPasswordInput): Promise<void> {
  return requestVoid({ method: "POST", path: "/auth/password/forgot", body: input });
}

export function resetPassword(input: ResetPasswordInput): Promise<void> {
  return requestVoid({ method: "POST", path: "/auth/password/reset", body: input });
}

export function signOut(): Promise<void> {
  return requestVoid({ method: "POST", path: "/auth/logout" });
}

export function oauthStartUrl(
  provider: OAuthProvider,
  options: { next: string; intent: AuthIntent },
): string {
  const params = new URLSearchParams({ next: safeNextPath(options.next), intent: options.intent });
  return `${config.apiUrl}/auth/oauth/${provider}/start?${params.toString()}`;
}

/**
 * Where "Continue with …" goes: the API's OAuth start URL (a full-page navigation to the
 * provider), or, with mocks, straight to the in-app callback after a mock sign-in.
 */
export async function beginOAuth(
  provider: OAuthProvider,
  options: { next: string; intent: AuthIntent },
): Promise<{ href: string; external: boolean }> {
  if (config.useMocks) {
    const { mockSignIn } = await import("./mock/auth");
    mockSignIn(provider);
    return {
      href: `/auth/callback?${new URLSearchParams({ next: safeNextPath(options.next) })}`,
      external: false,
    };
  }
  return { href: oauthStartUrl(provider, options), external: true };
}
