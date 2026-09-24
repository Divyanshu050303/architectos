import type { z } from "zod";

import type {
  AuthMethodSchema,
  AuthUserSchema,
  ForgotPasswordInputSchema,
  OAuthProviderSchema,
  ResetPasswordInputSchema,
  SignInInputSchema,
  SignUpInputSchema,
} from "@/schemas/auth";

export type OAuthProvider = z.infer<typeof OAuthProviderSchema>;
export type AuthMethod = z.infer<typeof AuthMethodSchema>;
export type AuthUser = z.infer<typeof AuthUserSchema>;
/** "signup" lets the API show terms or onboarding for a first sign-in; accounts are created either way. */
export type AuthIntent = "login" | "signup";
export type SignInInput = z.infer<typeof SignInInputSchema>;
export type SignUpInput = z.infer<typeof SignUpInputSchema>;
export type ForgotPasswordInput = z.infer<typeof ForgotPasswordInputSchema>;
export type ResetPasswordInput = z.infer<typeof ResetPasswordInputSchema>;
