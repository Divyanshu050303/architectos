import type { Metadata } from "next";

import { ResetPasswordForm } from "@/features/auth/components/PasswordResetForms";

// The token is a credential: keep the page out of indexes and the URL out of referrers.
export const metadata: Metadata = {
  title: "Choose a new password",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default async function ResetPasswordPage({ searchParams }: PageProps<"/reset-password">) {
  const { token } = await searchParams;
  return <ResetPasswordForm token={typeof token === "string" ? token : undefined} />;
}
