"use client";

/** Everything I've reported, in any status, newest first. */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import ItemCard from "@/components/ItemCard";
import Pagination from "@/components/Pagination";
import { apiFetch } from "@/lib/api";
import browserStyles from "@/components/ItemBrowser.module.css";

export default function MyReportsPage() {
  return (
    <AppShell wide>
      <h1>My reports</h1>
      <p className="lede">Items you've reported lost or found, and where each one is now.</p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <MyReports />
      </Suspense>
    </AppShell>
  );
}

function MyReports() {
  const pathname = usePathname();
  const params = useSearchParams();
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch("/items/mine", { query: { page, page_size: 12 } })
      .then(setResult)
      .catch((err) => setError(err.message));
  }, [page]);

  if (error) {
    return (
      <div className="alert" role="alert">
        {error}
      </div>
    );
  }
  if (!result) return <p className="loading">Loading…</p>;

  // After "Cancel this report" on a found item's page.
  const cancelled = params.get("cancelled") && (
    <div className="success" role="status">
      Your found report was cancelled.
    </div>
  );

  if (result.total === 0) {
    return (
      <>
        {cancelled}
        <div className={browserStyles.empty}>
          <p>You haven't reported anything yet.</p>
          <Link href="/report" className="button">
            Report an item
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      {cancelled}
      <div className={browserStyles.grid}>
        {result.items.map((item) => (
          <ItemCard key={item.id} item={item} showStatus />
        ))}
      </div>
      <Pagination
        page={result.page}
        totalPages={result.total_pages}
        hrefFor={(p) => (p === 1 ? pathname : `${pathname}?page=${p}`)}
      />
    </>
  );
}
