import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { CLERK_ENABLED } from "@/lib/clerk-mode";
import { ADMIN_EDITION_ENABLED } from "@/lib/edition";

const clerk = clerkMiddleware();

export default function proxy(request: NextRequest, event: unknown) {
  const pathname = request.nextUrl.pathname;
  const adminPath = pathname === "/admin" || pathname.startsWith("/admin/") || pathname === "/api/admin" || pathname.startsWith("/api/admin/");
  if (!ADMIN_EDITION_ENABLED && adminPath) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ ok: false, error: "not_found" }, { status: 404 });
    }
    return new NextResponse(null, { status: 404 });
  }
  if (!CLERK_ENABLED) return NextResponse.next();
  return clerk(request, event as never);
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
