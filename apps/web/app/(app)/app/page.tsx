import { redirect } from "next/navigation";

/** Application entry (spec §117): /app opens the workspace dashboard. */
export default function AppEntry() {
  redirect("/dashboard");
}
