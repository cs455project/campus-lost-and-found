"use client";

import { Suspense } from "react";

import AppShell from "@/components/AppShell";
import ItemBrowser from "@/components/ItemBrowser";

// Suspense is required around components that read the address bar
// (useSearchParams), so Next.js can build the rest of the page first.
export default function FoundPage() {
  return (
    <AppShell wide>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ItemBrowser kind="FOUND" />
      </Suspense>
    </AppShell>
  );
}
