"use client";

import { Suspense } from "react";

import AppShell from "@/components/AppShell";
import ReportForm from "@/components/ReportForm";

export default function ReportLostPage() {
  return (
    <AppShell>
      <h1>Report a lost item</h1>
      <p className="lede">
        Your report appears on the Lost page, so anyone who finds it knows to hand it in.
      </p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ReportForm kind="LOST" />
      </Suspense>
    </AppShell>
  );
}
