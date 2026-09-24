export const DEFAULT_AFTER_SIGN_IN = "/dashboard";

/**
 * Where to go after sign-in. Only same-origin paths are accepted: `?next=` comes from the URL,
 * so anything absolute or protocol-relative ("//evil.example", "/\evil.example") would be an
 * open redirect.
 */
export function safeNextPath(value: string | string[] | null | undefined): string {
  const next = Array.isArray(value) ? value[0] : value;
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return DEFAULT_AFTER_SIGN_IN;
  }
  // Control characters can be normalised away by the browser into "//".
  if (/[\u0000-\u001f]/.test(next)) return DEFAULT_AFTER_SIGN_IN;
  return next;
}
