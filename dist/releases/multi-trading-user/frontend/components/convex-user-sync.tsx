"use client";

import { useUser } from "@clerk/nextjs";
import { useConvexAuth, useMutation } from "convex/react";
import { useEffect, useRef } from "react";
import { convexFunctions } from "@/lib/convex-api";

export function ConvexUserSync() {
  const { isLoaded, isSignedIn, user } = useUser();
  const { isAuthenticated } = useConvexAuth();
  const upsertCurrentUser = useMutation(convexFunctions.users.upsertCurrentUser);
  const lastKeyRef = useRef("");

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !isAuthenticated || !user) return;
    const email = user.primaryEmailAddress?.emailAddress || user.emailAddresses?.[0]?.emailAddress || "";
    const name = user.fullName || user.username || email || "";
    const imageUrl = user.imageUrl || "";
    const key = `${user.id}|${email}|${name}|${imageUrl}`;
    if (lastKeyRef.current === key) return;
    lastKeyRef.current = key;
    void upsertCurrentUser({ email, name, imageUrl }).catch((err) => {
      lastKeyRef.current = "";
      console.warn("Convex user sync failed", err);
    });
  }, [isAuthenticated, isLoaded, isSignedIn, upsertCurrentUser, user]);

  return null;
}
