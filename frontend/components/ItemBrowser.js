"use client";

/**
 * The Found page and the Lost page: category filter, search, a grid of
 * items, and page links. Used as <ItemBrowser kind="FOUND" /> or "LOST".
 *
 * The filters live in the address bar (?category=bags&q=wallet&page=2):
 * - refreshing the page keeps them,
 * - the Back button undoes a filter change,
 * - a filtered list can be bookmarked or shared.
 * Whenever the address changes, we fetch the matching page from the API.
 */

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import ItemCard from "@/components/ItemCard";
import Pagination from "@/components/Pagination";
import { apiFetch } from "@/lib/api";
import styles from "./ItemBrowser.module.css";

const PAGE_SIZE = 12;

const TEXT = {
  FOUND: {
    title: "Found items",
    intro: "Items handed in at campus security offices. Open one to see where it's held.",
    noun: "found item",
    emptyAll: "No found items are waiting at the security offices right now.",
  },
  LOST: {
    title: "Lost items",
    intro: "Things people on campus are looking for. If you've found one, hand it in at a security office.",
    noun: "lost item",
    emptyAll: "Nobody has reported a lost item yet.",
  },
};

export default function ItemBrowser({ kind }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const text = TEXT[kind];

  // Current filters, read from the address bar.
  const category = params.get("category") || "";
  const query = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  const [categories, setCategories] = useState([]);
  const [searchText, setSearchText] = useState(query);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  // Load the category list once, for the dropdown.
  useEffect(() => {
    apiFetch("/categories").then(setCategories).catch(() => setCategories([]));
  }, []);

  // Keep the search box in step when the address changes (e.g. Back button).
  useEffect(() => {
    setSearchText(query);
  }, [query]);

  // Fetch the items whenever a filter or the page changes.
  useEffect(() => {
    let cancelled = false; // ignore answers to requests we no longer need
    setError("");
    apiFetch("/items", { query: { kind, category, q: query, page, page_size: PAGE_SIZE } })
      .then((data) => !cancelled && setResult(data))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [kind, category, query, page]);

  /** Build an address with some filters changed. Changing a filter goes back to page 1. */
  function hrefWith(changes) {
    const next = new URLSearchParams({ category, q: query, page: String(page), ...changes });
    if (!("page" in changes)) next.set("page", "1");
    for (const [key, value] of [...next.entries()]) {
      if (!value || (key === "page" && value === "1")) next.delete(key);
    }
    const text = next.toString();
    return text ? `${pathname}?${text}` : pathname;
  }

  function submitSearch(event) {
    event.preventDefault();
    router.push(hrefWith({ q: searchText.trim() }));
  }

  const filtered = Boolean(category || query);
  const categoryName = categories.find((c) => c.slug === category)?.name;

  return (
    <>
      <div className={styles.heading}>
        <div>
          <h1>{text.title}</h1>
          <p className="lede">{text.intro}</p>
        </div>
        <Link href={kind === "FOUND" ? "/report/found" : "/report/lost"} className="button button-secondary">
          {kind === "FOUND" ? "Hand in a found item" : "Report a lost item"}
        </Link>
      </div>

      <form className={styles.filters} onSubmit={submitSearch} role="search">
        <div className="field">
          <label htmlFor="category">Category</label>
          <select
            id="category"
            value={category}
            onChange={(e) => router.push(hrefWith({ category: e.target.value }))}
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="q">Search</label>
          <input
            id="q"
            type="search"
            placeholder="e.g. blue bottle"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </div>
        <button type="submit" className="button">
          Search
        </button>
      </form>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      {!result && !error && <p className="loading">Loading…</p>}

      {result && (
        <>
          {/* aria-live: screen readers announce the new count after filtering. */}
          <p className={styles.count} aria-live="polite">
            {result.total === 0
              ? ""
              : `${result.total} ${text.noun}${result.total === 1 ? "" : "s"}` +
                (categoryName ? ` in ${categoryName}` : "") +
                (query ? ` matching "${query}"` : "") +
                (result.total_pages > 1 ? `, page ${result.page} of ${result.total_pages}` : "")}
          </p>

          {result.items.length === 0 ? (
            <div className={styles.empty}>
              <p>
                {filtered
                  ? `No ${text.noun}s match these filters.`
                  : result.total > 0
                    ? "This page is past the end of the list."
                    : text.emptyAll}
              </p>
              {filtered && (
                <Link href={pathname} className="button button-secondary">
                  Clear filters
                </Link>
              )}
            </div>
          ) : (
            <div className={styles.grid}>
              {result.items.map((item) => (
                <ItemCard
                  key={item.id}
                  item={item}
                  extraAction={
                    kind === "LOST" ? (
                      <Link href={`/report/found?for=${item.id}`} className="link-button">
                        I found this
                      </Link>
                    ) : null
                  }
                />
              ))}
            </div>
          )}

          <Pagination
            page={result.page}
            totalPages={result.total_pages}
            hrefFor={(p) => hrefWith({ page: String(p) })}
          />
        </>
      )}
    </>
  );
}
