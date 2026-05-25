import { redirect } from "next/navigation";
import { CLERK_ENABLED, LOCAL_FIRST_AUTH } from "@/lib/clerk-mode";

export default function Home() {
  redirect(CLERK_ENABLED || LOCAL_FIRST_AUTH ? "/dashboard" : "/auth");
}
