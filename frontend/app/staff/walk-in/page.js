"use client";

import { Suspense } from "react";

import AppShell from "@/components/AppShell";
import ReportForm from "@/components/ReportForm";

export default function WalkInPage() {
  return (
    <AppShell require="staff">
      <h1>Register a walk-in item</h1>
      <p className="lede">
        For an item handed in at the desk without an online report. You&apos;re recorded as the person who received
        it, and it appears on the Found page straight away.
      </p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ReportForm kind="WALK_IN" />
      </Suspense>
    </AppShell>
  );
}
