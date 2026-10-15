"use client";

import { Suspense } from "react";

import AppShell from "@/components/AppShell";
import ItemBrowser from "@/components/ItemBrowser";

export default function LostPage() {
  return (
    <AppShell wide>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ItemBrowser kind="LOST" />
      </Suspense>
    </AppShell>
  );
}
