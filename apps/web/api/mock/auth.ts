/**
 * MOCK AUTH — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * The mock backend starts signed in as a demo user so every route works without signing in.
 * The session and any accounts created with email and password live in sessionStorage.
 * Passwords are compared in plain text: this is a fixture, not an auth implementation.
 */
import type { AuthMethod, AuthUser, OAuthProvider } from "@/types/auth";

export const MOCK_SESSION_STORAGE_KEY = "architectos-mock-session";
export const MOCK_ACCOUNTS_STORAGE_KEY = "architectos-mock-accounts";

/** Seeded account for email sign-in in development. */
export const MOCK_DEMO_ACCOUNT = {
  name: "Demo Engineer",
  email: "demo@architectos.dev",
  password: "architectos",
};
/** The only reset token the mock accepts, as if it came from the emailed link. */
export const MOCK_RESET_TOKEN = "mock-reset-token";

const SIGNED_OUT = "signed-out";

interface MockAccount {
  name: string;
  email: string;
  password: string;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function userFor(account: Pick<MockAccount, "name" | "email">, provider: AuthMethod): AuthUser {
  const id =
    account.email === MOCK_DEMO_ACCOUNT.email ? "usr_demo" : `usr_${account.email.replace(/\W/g, "_")}`;
  return { id, name: account.name, email: account.email, avatarUrl: null, provider };
}

export function mockUser(provider: AuthMethod = "github"): AuthUser {
  return userFor(MOCK_DEMO_ACCOUNT, provider);
}

function accounts(): MockAccount[] {
  try {
    const raw = storage()?.getItem(MOCK_ACCOUNTS_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) return parsed as MockAccount[];
  } catch {
    // Corrupt storage: fall back to the seed.
  }
  return [MOCK_DEMO_ACCOUNT];
}

function saveAccounts(list: MockAccount[]): void {
  storage()?.setItem(MOCK_ACCOUNTS_STORAGE_KEY, JSON.stringify(list));
}

function setSession(user: AuthUser): void {
  storage()?.setItem(MOCK_SESSION_STORAGE_KEY, JSON.stringify(user));
}

export function mockSession(): AuthUser | null {
  const raw = storage()?.getItem(MOCK_SESSION_STORAGE_KEY);
  if (raw === SIGNED_OUT) return null;
  if (raw) {
    try {
      return JSON.parse(raw) as AuthUser;
    } catch {
      // Fall through to the default demo session.
    }
  }
  return mockUser();
}

export function mockSignIn(provider: OAuthProvider): void {
  setSession(mockUser(provider));
}

/** Null when the email and password do not match an account. */
export function mockPasswordSignIn(email: string, password: string): AuthUser | null {
  const account = accounts().find((a) => a.email === email && a.password === password);
  if (!account) return null;
  const user = userFor(account, "password");
  setSession(user);
  return user;
}

/** Null when the email is already registered. */
export function mockSignUp(name: string, email: string, password: string): AuthUser | null {
  const list = accounts();
  if (list.some((a) => a.email === email)) return null;
  saveAccounts([...list, { name, email, password }]);
  const user = userFor({ name, email }, "password");
  setSession(user);
  return user;
}

/** Resets the demo account's password: the mock sends no email, so there is no other target. */
export function mockResetPassword(token: string, password: string): boolean {
  if (token !== MOCK_RESET_TOKEN) return false;
  saveAccounts(accounts().map((a) => (a.email === MOCK_DEMO_ACCOUNT.email ? { ...a, password } : a)));
  return true;
}

export function mockSignOut(): void {
  storage()?.setItem(MOCK_SESSION_STORAGE_KEY, SIGNED_OUT);
}
