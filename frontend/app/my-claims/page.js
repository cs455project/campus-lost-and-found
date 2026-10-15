"use client";

/** Every claim I've made, newest first, with where each one stands. */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import ClaimList from "@/components/ClaimList";
import Pagination from "@/components/Pagination";
import { apiFetch } from "@/lib/api";
import styles from "@/app/staff/staff.module.css";

export default function MyClaimsPage() {
  return (
    <AppShell wide>
      <h1>My claims</h1>
      <p className="lede">Found items you've said are yours, and what happens next with each.</p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <MyClaims />
      </Suspense>
    </AppShell>
  );
}

function MyClaims() {
  const pathname = usePathname();
  const params = useSearchParams();
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch("/claims/mine", { query: { page, page_size: 20 } })
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

  if (result.total === 0) {
    return (
      <div className={styles.empty}>
        <p>You haven't claimed anything. If you lost something, look for it on the Found page.</p>
        <p>
          <Link href="/found" className="button">
            Browse found items
          </Link>
        </p>
      </div>
    );
  }

  return (
    <>
      <ClaimList claims={result.items} />
      <Pagination
        page={result.page}
        totalPages={result.total_pages}
        hrefFor={(p) => (p === 1 ? pathname : `${pathname}?page=${p}`)}
      />
    </>
  );
}
