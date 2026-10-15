"use client";

import { Suspense } from "react";

import AppShell from "@/components/AppShell";
import ReportForm from "@/components/ReportForm";

export default function ReportFoundPage() {
  return (
    <AppShell>
      <h1>Hand in a found item</h1>
      <p className="lede">
        Describe what you found and choose a security office. Once you've handed it in and a guard confirms it,
        it appears on the Found page for its owner.
      </p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ReportForm kind="FOUND" />
      </Suspense>
    </AppShell>
  );
}
