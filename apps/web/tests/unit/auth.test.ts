import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getSession, oauthStartUrl } from "@/api/auth";
import { setTransport, type Transport } from "@/api/client";
import {
  MOCK_ACCOUNTS_STORAGE_KEY,
  MOCK_DEMO_ACCOUNT,
  MOCK_RESET_TOKEN,
  MOCK_SESSION_STORAGE_KEY,
  mockSignIn,
} from "@/api/mock/auth";
import { handleMockRequest } from "@/api/mock/handlers";
import { DEFAULT_AFTER_SIGN_IN, safeNextPath } from "@/lib/safe-redirect";

const noHeaders = { get: () => null };
const USER = { id: "usr_1", name: "Ada", email: "ada@example.com", avatarUrl: null, provider: "google" };

afterEach(() => setTransport(null));

describe("safeNextPath", () => {
  it.each([
    ["/project/proj_food/capacity?mode=capacity", "/project/proj_food/capacity?mode=capacity"],
    ["/dashboard", "/dashboard"],
  ])("keeps same-origin path %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    undefined,
    "",
    "https://evil.example/phish",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "/\t/evil.example",
  ])("falls back to the dashboard for %j", (input) => {
    expect(safeNextPath(input)).toBe(DEFAULT_AFTER_SIGN_IN);
  });

  it("uses the first value of a repeated parameter", () => {
    expect(safeNextPath(["/projects", "//evil.example"])).toBe("/projects");
  });
});

describe("auth API", () => {
  it("returns the user when a session exists", async () => {
    const transport: Transport = async () => ({ status: 200, body: { user: USER }, headers: noHeaders });
    setTransport(transport);
    await expect(getSession()).resolves.toEqual(USER);
  });

  it("returns null on 401 instead of throwing", async () => {
    const transport: Transport = async () => ({
      status: 401,
      body: { error: { code: "unauthenticated", message: "Sign in to continue." } },
      headers: noHeaders,
    });
    setTransport(transport);
    await expect(getSession()).resolves.toBeNull();
  });

  it("throws on other failures so the guard can offer a retry", async () => {
    const transport: Transport = async () => ({
      status: 500,
      body: { error: { code: "internal_error", message: "Boom" } },
      headers: noHeaders,
    });
    setTransport(transport);
    await expect(getSession()).rejects.toMatchObject({ status: 500 });
  });

  it("builds the provider start URL with a sanitised next path and intent", () => {
    const url = new URL(oauthStartUrl("github", { next: "//evil.example", intent: "signup" }));
    expect(url.pathname).toMatch(/\/auth\/oauth\/github\/start$/);
    expect(url.searchParams.get("next")).toBe(DEFAULT_AFTER_SIGN_IN);
    expect(url.searchParams.get("intent")).toBe("signup");
  });
});

describe("mock auth routes", () => {
  beforeEach(() => {
    window.sessionStorage.removeItem(MOCK_SESSION_STORAGE_KEY);
    window.sessionStorage.removeItem(MOCK_ACCOUNTS_STORAGE_KEY);
  });

  const post = (path: string, body: unknown) => handleMockRequest({ method: "POST", path, body }, "req");

  it("starts signed in, signs out, and signs back in with the chosen provider", () => {
    const session = () => handleMockRequest({ method: "GET", path: "/auth/session", body: undefined }, "req");

    expect(session()).toMatchObject({ status: 200, body: { user: { provider: "github" } } });
    expect(handleMockRequest({ method: "POST", path: "/auth/logout", body: undefined }, "req").status).toBe(
      204,
    );
    expect(session()).toMatchObject({ status: 401, body: { error: { code: "unauthenticated" } } });

    mockSignIn("google");
    expect(session()).toMatchObject({ status: 200, body: { user: { provider: "google" } } });
  });

  it("signs in with email and password and rejects a wrong password without saying which field", () => {
    const { email, password } = MOCK_DEMO_ACCOUNT;
    expect(post("/auth/login", { email, password: "wrong" })).toMatchObject({
      status: 401,
      body: { error: { code: "invalid_credentials" } },
    });
    expect(post("/auth/login", { email, password })).toMatchObject({
      status: 200,
      body: { user: { email, provider: "password" } },
    });
  });

  it("signs up once per email and signs the new account in", () => {
    const account = { name: "Ada", email: "ada@example.com", password: "correct horse" };
    expect(post("/auth/signup", account)).toMatchObject({ status: 201, body: { user: { name: "Ada" } } });
    expect(handleMockRequest({ method: "GET", path: "/auth/session", body: undefined }, "req")).toMatchObject(
      {
        body: { user: { email: "ada@example.com" } },
      },
    );
    expect(post("/auth/signup", account)).toMatchObject({
      status: 409,
      body: { error: { code: "email_taken" } },
    });
    expect(post("/auth/signup", { ...account, email: "b@example.com", password: "short" })).toMatchObject({
      status: 422,
    });
  });

  it("answers a reset request the same way whether or not the account exists", () => {
    expect(post("/auth/password/forgot", { email: "nobody@example.com" }).status).toBe(204);
    expect(post("/auth/password/forgot", { email: MOCK_DEMO_ACCOUNT.email }).status).toBe(204);
  });

  it("resets the password only with a valid token", () => {
    expect(post("/auth/password/reset", { token: "stale", password: "new password" })).toMatchObject({
      status: 400,
      body: { error: { code: "invalid_token" } },
    });
    expect(post("/auth/password/reset", { token: MOCK_RESET_TOKEN, password: "new password" }).status).toBe(
      204,
    );
    expect(post("/auth/login", { email: MOCK_DEMO_ACCOUNT.email, password: "new password" }).status).toBe(
      200,
    );
  });
});
