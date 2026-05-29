import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { CLERK_ENABLED } from "@/lib/clerk-mode";

const clerk = clerkMiddleware();

export default function proxy(request: NextRequest, event: unknown) {
  if (!CLERK_ENABLED) return NextResponse.next();
  return clerk(request, event as never);
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};

