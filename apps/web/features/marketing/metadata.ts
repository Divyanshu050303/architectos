import type { Metadata } from "next";

/**
 * Title + description + Open Graph basics for a marketing page. URLs are omitted until a
 * public site origin (metadataBase) is configured.
 */
export function marketingMetadata({
  title,
  description,
  absoluteTitle = false,
}: {
  title: string;
  description: string;
  absoluteTitle?: boolean;
}): Metadata {
  const fullTitle = absoluteTitle ? title : `${title} · ArchitectOS`;
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description,
    openGraph: {
      type: "website",
      siteName: "ArchitectOS",
      title: fullTitle,
      description,
      locale: "en_US",
    },
    twitter: { card: "summary", title: fullTitle, description },
  };
}
