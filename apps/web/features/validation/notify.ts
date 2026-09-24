import { getErrorInfo } from "@/api/client";
import { toast } from "@/components/ui/toast";

/** Error toast that always carries the request id when there is one (spec §49). */
export function toastError(title: string, error: unknown): void {
  const info = getErrorInfo(error);
  const description = info.requestId ? `${info.message} Request ID: ${info.requestId}` : info.message;
  toast(title, { tone: "danger", description });
}
