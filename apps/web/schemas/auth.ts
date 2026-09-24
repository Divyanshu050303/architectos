/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/auth.py. Sessions are an
 * httpOnly cookie set by the API after sign-in; the browser never sees a token.
 */
import { z } from "zod";

export const OAuthProviderSchema = z.enum(["google", "github"]);
/** How the account signed in: an OAuth provider or email and password. */
export const AuthMethodSchema = z.enum(["google", "github", "password"]);

export const AuthUserSchema = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string(),
  avatarUrl: z.string().nullable(),
  provider: AuthMethodSchema,
});

export const SessionSchema = z.object({ user: AuthUserSchema });

const email = z.email("Enter a valid email address.").trim().toLowerCase();
/** NIST 800-63B: length over composition rules; the API also checks breached-password lists. */
const newPassword = z.string().min(8, "Use at least 8 characters.").max(128, "Use at most 128 characters.");

export const SignInInputSchema = z.object({
  email,
  password: z.string().min(1, "Enter your password."),
});

export const SignUpInputSchema = z.object({
  name: z.string().trim().min(1, "Enter your name.").max(80, "Use at most 80 characters."),
  email,
  password: newPassword,
});

export const ForgotPasswordInputSchema = z.object({ email });

export const ResetPasswordInputSchema = z.object({
  token: z.string().min(1),
  password: newPassword,
});
