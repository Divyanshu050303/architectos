import Link from "next/link";

import { SkipLink } from "@/components/layout/AppShell";
import { ThemeToggle } from "@/components/navigation/ThemeToggle";
import { HERO_FACTS } from "@/features/marketing/components/Hero";
import { Wordmark } from "@/features/marketing/components/Wordmark";

/** Sign-in, sign-up and the OAuth callback: the form on one side, what you are signing in to on the other. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-2">
      <SkipLink />
      <div className="flex min-w-0 flex-col px-4 sm:px-6">
        <header className="flex h-14 items-center justify-between">
          <Link href="/" className="rounded-sm" aria-label="ArchitectOS home">
            <Wordmark />
          </Link>
          <ThemeToggle />
        </header>
        <main id="main" className="flex flex-1 items-center justify-center py-8">
          {children}
        </main>
      </div>

      <aside
        aria-label="About ArchitectOS"
        className="relative isolate hidden overflow-hidden border-l border-default bg-surface lg:flex"
      >
        <div aria-hidden className="hero-grid absolute inset-0 -z-10 opacity-60" />
        <div className="flex max-w-lg flex-col justify-center gap-10 px-12">
          <p className="headline text-4xl text-fg">
            Find the bottleneck <span className="text-muted">before your users do.</span>
          </p>
          <dl className="flex flex-col gap-6 border-t border-default pt-6">
            {HERO_FACTS.map((fact) => (
              <div key={fact.term} className="flex flex-col gap-1.5">
                <dt className="eyebrow">{fact.term}</dt>
                <dd className="text-sm text-fg-secondary">{fact.detail}</dd>
              </div>
            ))}
          </dl>
        </div>
      </aside>
    </div>
  );
}
