"use client";

import { useAuth } from "@clerk/nextjs";
import { ConvexReactClient } from "convex/react";
import { ConvexProviderWithClerk } from "convex/react-clerk";
import { useMemo } from "react";

type ConvexClientProviderProps = {
  children: React.ReactNode;
  url: string;
};

export function ConvexClientProvider({ children, url }: ConvexClientProviderProps) {
  const convex = useMemo(() => new ConvexReactClient(url), [url]);
  return (
    <ConvexProviderWithClerk client={convex} useAuth={useAuth}>
      {children}
    </ConvexProviderWithClerk>
  );
}

