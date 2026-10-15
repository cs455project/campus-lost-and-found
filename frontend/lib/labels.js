/** Human-readable words for values that come from the backend. */

export const STATUS_LABELS = {
  AWAITING_DROPOFF: "Waiting to be handed in",
  IN_CUSTODY: "At a security office",
  CLAIM_PENDING: "Claim in progress",
  RETURNED: "Returned to owner",
  DISPOSED: "Disposed of",
  DONATED: "Donated",
  OPEN: "Still missing",
  RECOVERED: "Owner got it back",
  CLOSED: "Report closed",
};

/** Is this status a "finished" one (shown greyed in My reports)? */
export const FINISHED_STATUSES = ["RETURNED", "DISPOSED", "DONATED", "RECOVERED", "CLOSED"];

/** "2026-10-12" -> "12 Oct 2026". Dates from the backend have no time part,
 * so we build them in local time to avoid shifting a day. */
export function formatDate(isoDate) {
  const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Today's date as "YYYY-MM-DD", for the max value of date inputs. */
export function todayIso() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

/** Custody history: what happened, in words. */
export const EVENT_LABELS = {
  RECEIVED: "Received from the finder",
  REGISTERED: "Registered at the desk (walk-in)",
  TRANSFERRED: "Moved to another office",
  RETURNED: "Returned to its owner",
  DISPOSED: "Disposed of",
  DONATED: "Donated",
};

/** "2026-10-13T06:15:56Z" -> "13 Oct 2026, 11:45 am" in the viewer's time zone. */
export function formatDateTime(isoDateTime) {
  return new Date(isoDateTime).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Whole days between a moment and now, e.g. how long an item has been held. */
export function daysSince(isoDateTime) {
  return Math.floor((Date.now() - new Date(isoDateTime).getTime()) / 86400000);
}

/** Claim statuses, as the claimant reads them. */
export const CLAIM_STATUS_LABELS = {
  SUBMITTED: "Waiting for review",
  UNDER_REVIEW: "Being reviewed",
  NEEDS_MORE_INFO: "Visit the office",
  APPROVED: "Approved: ready to collect",
  COLLECTED: "Collected",
  REJECTED: "Not approved",
  WITHDRAWN: "Withdrawn",
  EXPIRED: "Expired (not collected in time)",
  CLOSED: "Closed (item returned to someone else)",
};

/** Claims that are still going (the rest are finished). */
export const OPEN_CLAIM_STATUSES = ["SUBMITTED", "UNDER_REVIEW", "NEEDS_MORE_INFO", "APPROVED"];

/** A date (no time) as "today", "yesterday", "5 days ago", or "12 Oct 2026" when older than a month. */
export function daysAgo(isoDate) {
  const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
  const then = new Date(year, month - 1, day);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((today - then) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 31) return `${days} days ago`;
  return `on ${formatDate(isoDate)}`;
}
