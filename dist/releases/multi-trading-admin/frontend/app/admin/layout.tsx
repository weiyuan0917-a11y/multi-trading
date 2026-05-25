import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { ADMIN_EDITION_ENABLED } from "@/lib/edition";

export default function AdminLayout({ children }: { children: ReactNode }) {
  if (!ADMIN_EDITION_ENABLED) notFound();
  return <>{children}</>;
}
