"use client";

import { PackageCheck, SearchX } from "lucide-react";
import Link from "next/link";

import AppShell from "@/components/AppShell";
import styles from "./report.module.css";

export default function ReportChooserPage() {
  return (
    <AppShell>
      <span className="eyebrow">New report</span>
      <h1>Report an item</h1>
      <p className="lede">Did you lose something, or find something that isn't yours?</p>
      <div className={styles.choices}>
        <Link href="/report/lost" className={styles.choice} data-kind="LOST">
          <span className={styles.choiceIcon}>
            <SearchX size={22} strokeWidth={1.8} aria-hidden="true" />
          </span>
          <strong>I lost something</strong>
          <span>Describe it so finders and security staff can recognise it.</span>
        </Link>
        <Link href="/report/found" className={styles.choice} data-kind="FOUND">
          <span className={styles.choiceIcon}>
            <PackageCheck size={22} strokeWidth={1.8} aria-hidden="true" />
          </span>
          <strong>I found something</strong>
          <span>Describe it, then hand it in at a campus security office.</span>
        </Link>
      </div>
    </AppShell>
  );
}
