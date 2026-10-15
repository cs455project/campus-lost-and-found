"use client";

/**
 * The Claims desk (guards and the admin). Four tabs, one per stage:
 *
 *   To review          new claims, and claims being reviewed (oldest first)
 *   Office visit       claimants asked to come in person
 *   Ready to collect   approved, waiting for the owner (soonest deadline first)
 *   Closed             collected, rejected, withdrawn, expired or closed
 *
 * The tab and page number live in the address bar, like the Staff desk.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import ClaimList from "@/components/ClaimList";
import Pagination from "@/components/Pagination";
import { apiFetch } from "@/lib/api";
import styles from "@/app/staff/staff.module.css";

const TABS = [
  { stage: "review", label: "To review", empty: "No claims are waiting for review." },
  { stage: "visit", label: "Office visit", empty: "Nobody has been asked to visit an office." },
  { stage: "collect", label: "Ready to collect", empty: "No approved items are waiting to be collected." },
  { stage: "closed", label: "Closed", empty: "No claims have been closed yet." },
];

export default function ClaimsDeskPage() {
  return (
    <AppShell wide require="staff">
      <h1>Claims desk</h1>
      <p className="lede">Students who say a found item is theirs, at every security office.</p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ClaimsDesk />
      </Suspense>
    </AppShell>
  );
}

function ClaimsDesk() {
  const pathname = usePathname();
  const params = useSearchParams();
  const tab = TABS.find((t) => t.stage === params.get("stage")) || TABS[0];
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError("");
    apiFetch("/staff/claims", { query: { stage: tab.stage, page, page_size: 20 } })
      .then((data) => !cancelled && setResult(data))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [tab.stage, page]);

  function hrefFor(stage, p = 1) {
    const next = new URLSearchParams();
    if (stage !== "review") next.set("stage", stage);
    if (p > 1) next.set("page", String(p));
    const text = next.toString();
    return text ? `${pathname}?${text}` : pathname;
  }

  return (
    <>
      <nav className={styles.tabs} aria-label="Claim stages">
        {TABS.map((t) => (
          <Link
            key={t.stage}
            href={hrefFor(t.stage)}
            className={styles.tab}
            aria-current={t.stage === tab.stage ? "page" : undefined}
          >
            {t.label}
          </Link>
        ))}
      </nav>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {!result && !error && <p className="loading">Loading…</p>}

      {result && (
        <>
          <p className={styles.count} aria-live="polite">
            {result.total === 0 ? "" : `${result.total} claim${result.total === 1 ? "" : "s"}`}
          </p>
          {result.items.length === 0 ? (
            <div className={styles.empty}>
              <p>{tab.empty}</p>
            </div>
          ) : (
            <ClaimList claims={result.items} staffView />
          )}
          <Pagination page={result.page} totalPages={result.total_pages} hrefFor={(p) => hrefFor(tab.stage, p)} />
        </>
      )}
    </>
  );
}
