/**
 * One place for every call to the backend.
 *
 * apiFetch("/auth/me") calls /api/auth/me, which Next.js forwards to
 * FastAPI (see next.config.mjs). If the backend answers with an error,
 * apiFetch throws an ApiError carrying a readable message, so pages can
 * simply do:
 *
 *   try { await apiFetch(...) } catch (error) { show(error.message) }
 */

export class ApiError extends Error {
  constructor(status, message, fieldErrors = {}) {
    super(message);
    this.status = status;
    // For 422 validation errors: { phone: "Enter a valid ..." }
    this.fieldErrors = fieldErrors;
  }
}

/**
 * FastAPI sends errors in two shapes:
 *  - our own rules:   {"detail": "This code has expired..."}
 *  - validation (422): {"detail": [{"loc": ["body", "phone"], "msg": "..."}]}
 */
function readError(status, body) {
  const detail = body?.detail;
  if (typeof detail === "string") {
    return new ApiError(status, detail);
  }
  if (Array.isArray(detail)) {
    const fieldErrors = {};
    for (const problem of detail) {
      const field = problem.loc?.[problem.loc.length - 1];
      // Pydantic prefixes custom messages with "Value error, ".
      const message = String(problem.msg || "").replace(/^Value error, /, "");
      if (field && !fieldErrors[field]) fieldErrors[field] = message;
    }
    return new ApiError(status, "Please check the highlighted fields.", fieldErrors);
  }
  return new ApiError(status, "Something went wrong. Please try again.");
}

const OFFLINE_MESSAGE = "Can't reach the server. Check that the backend is running.";

async function handle(request) {
  let response;
  try {
    response = await request;
  } catch {
    throw new ApiError(0, OFFLINE_MESSAGE);
  }

  if (response.status === 204) return null; // success with no content (e.g. sign out)

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    // The proxy answers 500 with no JSON when FastAPI isn't running.
    if (data === null && response.status >= 500) throw new ApiError(response.status, OFFLINE_MESSAGE);
    throw readError(response.status, data);
  }
  return data;
}

/** JSON requests. `query` becomes ?key=value, skipping empty values. */
export function apiFetch(path, { method = "GET", body, query } = {}) {
  let url = `/api${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") params.set(key, value);
    }
    const text = params.toString();
    if (text) url += `?${text}`;
  }
  return handle(
    fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
}

/**
 * File uploads use "multipart/form-data" instead of JSON. We don't set the
 * Content-Type header ourselves: the browser adds it, including the
 * boundary marker that separates the parts.
 */
export function apiUpload(path, file, fieldName = "file") {
  const form = new FormData();
  form.append(fieldName, file);
  return handle(fetch(`/api${path}`, { method: "POST", body: form }));
}

/** Address of an item photo (served by the backend with permission checks). */
export function photoUrl(itemId, photoId) {
  return `/api/items/${itemId}/photos/${photoId}`;
}

/** Address of a claim's proof image (only the claimant and staff can open it). */
export function proofUrl(claimId, proofId) {
  return `/api/claims/${claimId}/proofs/${proofId}`;
}
