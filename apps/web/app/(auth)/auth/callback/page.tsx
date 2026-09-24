import type { Metadata } from "next";

import { AuthCallback } from "@/features/auth/components/AuthCallback";
import { safeNextPath } from "@/lib/safe-redirect";

export const metadata: Metadata = { title: "Signing in", robots: { index: false, follow: false } };

export default async function AuthCallbackPage({ searchParams }: PageProps<"/auth/callback">) {
  const { next, error } = await searchParams;
  return <AuthCallback next={safeNextPath(next)} error={typeof error === "string" ? error : undefined} />;
}
