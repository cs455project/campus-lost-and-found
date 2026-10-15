"use client";

/**
 * Returned items: found items that went back to their owners, most recent
 * first. Shows that the system works, and lets people check a returned
 * item instead of searching for it.
 *
 * Everyone sees what, where from and when. Staff also see who collected
 * it, the claim, and which guard handed it over (the backend only sends
 * those to staff). Records are deleted 60 days after the return.
 */

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import ItemCard from "@/components/ItemCard";
import Pagination from "@/components/Pagination";
import { apiFetch } from "@/lib/api";
import { formatDateTime } from "@/lib/labels";
import browserStyles from "@/components/ItemBrowser.module.css";
import styles from "./returned.module.css";

const PAGE_SIZE = 12;

export default function ReturnedPage() {
  return (
    <AppShell wide>
      <h1>Returned items</h1>
      <p className="lede">
        Found items that were handed back to their owners after an ID check. Each record is removed 60 days after the
        return.
      </p>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ReturnedList />
      </Suspense>
    </AppShell>
  );
}

function ReturnedList() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const category = params.get("category") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  const [categories, setCategories] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch("/categories").then(setCategories).catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError("");
    apiFetch("/items/returned", { query: { category, page, page_size: PAGE_SIZE } })
      .then((data) => !cancelled && setResult(data))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [category, page]);

  function hrefFor(changes) {
    const next = new URLSearchParams({ category, page: String(page), ...changes });
    if (!("page" in changes)) next.set("page", "1");
    for (const [key, value] of [...next.entries()]) {
      if (!value || (key === "page" && value === "1")) next.delete(key);
    }
    const text = next.toString();
    return text ? `${pathname}?${text}` : pathname;
  }

  return (
    <>
      <div className={styles.filter}>
        <div className="field">
          <label htmlFor="category">Category</label>
          <select id="category" value={category} onChange={(e) => router.push(hrefFor({ category: e.target.value }))}>
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {!result && !error && <p className="loading">Loading…</p>}

      {result && (
        <>
          <p className={browserStyles.count} aria-live="polite">
            {result.total === 0 ? "" : `${result.total} returned item${result.total === 1 ? "" : "s"}`}
          </p>
          {result.items.length === 0 ? (
            <div className={browserStyles.empty}>
              <p>{category ? "No returned items in this category." : "No items have been returned yet."}</p>
              {category && (
                <Link href={pathname} className="button button-secondary">
                  Show all categories
                </Link>
              )}
            </div>
          ) : (
            <div className={browserStyles.grid}>
              {result.items.map((item) => (
                <ItemCard key={item.id} item={item} fadeFinished={false} extraAction={<ReturnInfo item={item} />} />
              ))}
            </div>
          )}
          <Pagination page={result.page} totalPages={result.total_pages} hrefFor={(p) => hrefFor({ page: String(p) })} />
        </>
      )}
    </>
  );
}

function ReturnInfo({ item }) {
  const staff = item.staff;
  return (
    <div className={styles.info}>
      <p className={styles.returned}>
        Returned {formatDateTime(item.returned_at)}
        {item.returned_from && <> from {item.returned_from.name}</>}
      </p>
      {staff && (
        // Only staff receive this block from the API.
        <dl className={styles.staff}>
          <dt>Collected by</dt>
          <dd>
            {staff.collected_by}
            {staff.collected_by_roll && ` (${staff.collected_by_roll})`}
          </dd>
          <dt>Claim</dt>
          <dd>
            <Link href={`/claims/${staff.claim_id}`}>{staff.claim_ref}</Link>
          </dd>
          {staff.handed_over_by && (
            <>
              <dt>Handed over by</dt>
              <dd>{staff.handed_over_by}</dd>
            </>
          )}
          {staff.note && (
            <>
              <dt>Note</dt>
              <dd>{staff.note}</dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}
