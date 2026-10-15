/**
 * Page links: « Previous  1 … 4 5 6 … 12  Next »
 *
 * `hrefFor(page)` builds the link for a page, so the current filters stay
 * in the address. Using real links (not buttons) means the browser's Back
 * button, bookmarks and "open in new tab" all work.
 */

import Link from "next/link";

import styles from "./Pagination.module.css";

/** Which page numbers to show: first, last, and two either side of the current one. */
function visiblePages(current, total) {
  const pages = new Set([1, total]);
  for (let p = current - 2; p <= current + 2; p++) {
    if (p >= 1 && p <= total) pages.add(p);
  }
  const sorted = [...pages].sort((a, b) => a - b);
  // Insert "…" wherever numbers are skipped.
  const result = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push("gap-" + page);
    result.push(page);
  });
  return result;
}

export default function Pagination({ page, totalPages, hrefFor }) {
  if (totalPages <= 1) return null;

  return (
    <nav className={styles.pagination} aria-label="Pages">
      {page > 1 ? (
        <Link href={hrefFor(page - 1)} className={styles.step}>
          Previous
        </Link>
      ) : (
        <span className={`${styles.step} ${styles.disabled}`}>Previous</span>
      )}

      <ol className={styles.numbers}>
        {visiblePages(page, totalPages).map((entry) =>
          typeof entry === "string" ? (
            <li key={entry} className={styles.gap} aria-hidden="true">
              …
            </li>
          ) : (
            <li key={entry}>
              <Link
                href={hrefFor(entry)}
                className={styles.number}
                aria-current={entry === page ? "page" : undefined}
                aria-label={`Page ${entry}`}
              >
                {entry}
              </Link>
            </li>
          ),
        )}
      </ol>

      {page < totalPages ? (
        <Link href={hrefFor(page + 1)} className={styles.step}>
          Next
        </Link>
      ) : (
        <span className={`${styles.step} ${styles.disabled}`}>Next</span>
      )}
    </nav>
  );
}
