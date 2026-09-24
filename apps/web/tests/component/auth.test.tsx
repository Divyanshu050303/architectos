import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { ApiError } from "@/api/client";
import { AuthPanel } from "@/features/auth/components/AuthPanel";
import { ForgotPasswordForm, ResetPasswordForm } from "@/features/auth/components/PasswordResetForms";

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const api = vi.hoisted(() => ({
  beginOAuth: vi.fn(),
  getSession: vi.fn(),
  signOut: vi.fn(),
  signIn: vi.fn(),
  signUp: vi.fn(),
  requestPasswordReset: vi.fn(),
  resetPassword: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => nav }));
vi.mock("@/api/auth", () => api);

const USER = { id: "usr_1", name: "Ada", email: "ada@example.com", avatarUrl: null, provider: "github" };

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("AuthPanel", () => {
  it("offers Google and GitHub and links to sign-up with the same destination", () => {
    renderWithQuery(<AuthPanel intent="login" next="/projects" />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back. Sign in.");
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Continue with GitHub" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/signup?next=%2Fprojects",
    );
  });

  it("leaves for the provider with the intent and next path", async () => {
    api.beginOAuth.mockResolvedValue({ href: "/auth/callback?next=%2Fprojects", external: false });
    renderWithQuery(<AuthPanel intent="signup" next="/projects" />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue with GitHub" }));
    });

    expect(api.beginOAuth).toHaveBeenCalledWith("github", { next: "/projects", intent: "signup" });
    expect(nav.push).toHaveBeenCalledWith("/auth/callback?next=%2Fprojects");
  });

  it("disables both providers while one is starting", async () => {
    api.beginOAuth.mockReturnValue(new Promise(() => {}));
    renderWithQuery(<AuthPanel intent="login" next="/dashboard" />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));
    });

    expect(screen.getByRole("button", { name: /Google/ })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Continue with GitHub" })).toBeDisabled();
  });

  it("explains a cancelled sign-in and shows a generic message for unknown codes", () => {
    renderWithQuery(<AuthPanel intent="login" next="/dashboard" error="access_denied" />);
    expect(screen.getByText(/cancelled at the provider/)).toBeInTheDocument();

    cleanup();
    renderWithQuery(<AuthPanel intent="login" next="/dashboard" error="something_new" />);
    expect(screen.getByText(/Sign-in did not complete/)).toBeInTheDocument();
  });
});

describe("RequireAuth", () => {
  it("renders the application for a signed-in user", async () => {
    api.getSession.mockResolvedValue(USER);
    renderWithQuery(<RequireAuth>workspace</RequireAuth>);

    expect(await screen.findByText("workspace")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("sends a signed-out visitor to /login and remembers where they were going", async () => {
    window.history.replaceState(null, "", "/project/proj_food/capacity?mode=capacity");
    api.getSession.mockResolvedValue(null);
    renderWithQuery(<RequireAuth>workspace</RequireAuth>);

    expect(await screen.findByText("Redirecting to sign in…")).toBeInTheDocument();
    expect(nav.replace).toHaveBeenCalledWith(
      `/login?${new URLSearchParams({ next: "/project/proj_food/capacity?mode=capacity" })}`,
    );
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
  });

  it("offers a retry when the session check fails", async () => {
    api.getSession.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(USER);
    renderWithQuery(<RequireAuth>workspace</RequireAuth>);

    fireEvent.click(await screen.findByRole("button", { name: /Retry|Try again/ }));
    expect(await screen.findByText("workspace")).toBeInTheDocument();
  });
});

function apiError(status: number, code: string, message: string) {
  return new ApiError({ status, code, message, requestId: "req_test" });
}

describe("email and password", () => {
  it("validates before calling the API", async () => {
    renderWithQuery(<AuthPanel intent="signup" next="/dashboard" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Use at least 8 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(api.signUp).not.toHaveBeenCalled();
  });

  it("signs in and goes to the requested page", async () => {
    api.signIn.mockResolvedValue({ ...USER, provider: "password" });
    renderWithQuery(<AuthPanel intent="login" next="/projects" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: " Ada@Example.com " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await vi.waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/projects"));
    expect(api.signIn.mock.calls[0]?.[0]).toEqual({ email: "ada@example.com", password: "hunter22" });
  });

  it("shows one message for wrong credentials", async () => {
    api.signIn.mockRejectedValue(apiError(401, "invalid_credentials", "Nope"));
    renderWithQuery(<AuthPanel intent="login" next="/dashboard" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Email or password is incorrect.")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("puts a taken email on the email field", async () => {
    api.signUp.mockRejectedValue(apiError(409, "email_taken", "Taken"));
    renderWithQuery(<AuthPanel intent="signup" next="/dashboard" />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long enough" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
  });

  it("toggles password visibility", () => {
    renderWithQuery(<AuthPanel intent="login" next="/dashboard" />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(input).toHaveAttribute("type", "text");
  });

  it("confirms a reset request without revealing whether the account exists", async () => {
    api.requestPasswordReset.mockResolvedValue(undefined);
    renderWithQuery(<ForgotPasswordForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText(/If an account exists for ada@example.com/)).toBeInTheDocument();
  });

  it("requires matching new passwords and returns to sign-in after a reset", async () => {
    api.resetPassword.mockResolvedValue(undefined);
    renderWithQuery(<ResetPasswordForm token="tok_1" />);

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new password" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different" } });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(api.resetPassword).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new password" } });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    await vi.waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?reset=done"));
    expect(api.resetPassword.mock.calls[0]?.[0]).toEqual({ token: "tok_1", password: "new password" });
  });
});
