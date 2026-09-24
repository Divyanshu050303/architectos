import type { Metadata } from "next";

import { ForgotPasswordForm } from "@/features/auth/components/PasswordResetForms";

export const metadata: Metadata = { title: "Reset your password" };

export default function ForgotPasswordPage() {
  return <ForgotPasswordForm />;
}
