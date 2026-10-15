"use client";

/**
 * One claim. The claimant and security staff see different things:
 *
 *  Claimant: what to do next (wait, visit the office, collect), their own
 *            description and proof, and the history. Can withdraw.
 *  Staff:    also the claimant's contact details, other claims on the
 *            same item, and the review buttons. Staff check the claimant's
 *            description against the item itself, which they're holding.
 *
 * The backend decides who sees what and sends can_* flags; this page only
 * shows the buttons it's told are allowed.
 */

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { apiFetch, apiUpload, photoUrl, proofUrl } from "@/lib/api";
import { CLAIM_STATUS_LABELS, OPEN_CLAIM_STATUSES as OPEN_STATUSES, formatDateTime } from "@/lib/labels";
import styles from "./claimPage.module.css";

export default function ClaimPage() {
  return (
    <AppShell>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ClaimDetail />
      </Suspense>
    </AppShell>
  );
}

function ClaimDetail() {
  const { id } = useParams();
  const params = useSearchParams();
  const [claim, setClaim] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    apiFetch(`/claims/${id}`)
      .then(setClaim)
      .catch((err) => setError(err.status === 404 ? "This claim doesn't exist, or it isn't yours." : err.message));
  }, [id]);

  useEffect(load, [load]);

  if (error) {
    return (
      <>
        <div className="alert" role="alert">
          {error}
        </div>
        <Link href="/my-claims">My claims</Link>
      </>
    );
  }
  if (!claim) return <p className="loading">Loading…</p>;

  const isStaffView = claim.claimant !== null; // only staff receive the claimant's details
  const proofsFailed = Number(params.get("proofs_failed")) || 0;

  return (
    <article>
      {params.get("new") && (
        <div className="success" role="status">
          Your claim is sent. A security guard will review it, and you'll get an email when they decide.
          {proofsFailed > 0 && ` ${proofsFailed} image(s) couldn't be uploaded; you can add them below.`}
        </div>
      )}

      <p className={styles.back}>
        {isStaffView ? <Link href="/staff/claims">Claims desk</Link> : <Link href="/my-claims">My claims</Link>}
      </p>

      <header>
        <h1>Claim {claim.ref}</h1>
        <p className={styles.status}>
          <span className={styles.badge} data-status={claim.status}>
            {CLAIM_STATUS_LABELS[claim.status]}
          </span>
          <span className={styles.muted}>Sent {formatDateTime(claim.created_at)}</span>
        </p>
      </header>

      <Tracker claim={claim} />

      <ItemBrief item={claim.item} />

      {claim.is_mine && <NextStep claim={claim} />}

      {isStaffView && <StaffReview claim={claim} onChanged={setClaim} />}

      <section className={styles.panel} aria-labelledby="desc-heading">
        <h2 id="desc-heading">{claim.is_mine ? "What you told us" : "What the claimant says"}</h2>
        <p className={styles.description}>{claim.description}</p>
        <Proofs claim={claim} onChanged={setClaim} />
      </section>

      <History events={claim.events} />

      {claim.can_withdraw && <Withdraw claim={claim} onChanged={setClaim} />}
    </article>
  );
}

// The happy path, left to right. A claim that ends early (rejected,
// withdrawn, expired, closed) shows red from the step where it stopped.
const TRACK = [
  { status: "SUBMITTED", label: "Sent" },
  { status: "UNDER_REVIEW", label: "Review" },
  { status: "APPROVED", label: "Approved" },
  { status: "COLLECTED", label: "Collected" },
];
const STEP_OF = { SUBMITTED: 0, UNDER_REVIEW: 1, NEEDS_MORE_INFO: 1, APPROVED: 2, COLLECTED: 3 };

function Tracker({ claim }) {
  // Furthest step the claim reached, from its history.
  const reached = Math.max(0, ...claim.events.map((e) => STEP_OF[e.to_status] ?? 0));
  const deadEnd = !(claim.status in STEP_OF);
  return (
    <ol className={styles.tracker} aria-label="Claim progress">
      {TRACK.map((step, index) => {
        let state = "todo";
        if (index < reached) state = "done";
        else if (index === reached) state = deadEnd ? "dead" : "current";
        const label =
          index === 1 && claim.status === "NEEDS_MORE_INFO" ? "Office visit" : state === "dead" ? `${step.label}: stopped` : step.label;
        return (
          <li key={step.status} className={styles.trackStep} data-state={state}>
            <span className={styles.trackNum}>{index + 1}</span>
            {label}
            {state === "current" && <span className="visually-hidden"> (current step)</span>}
          </li>
        );
      })}
    </ol>
  );
}

function ItemBrief({ item }) {
  return (
    <div className={styles.itemBrief}>
      {item.cover_photo_id ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={photoUrl(item.id, item.cover_photo_id)} alt="" />
      ) : (
        <span className={styles.noPhoto}>{item.category.name}</span>
      )}
      <div>
        <Link href={`/items/${item.id}`}>{item.title}</Link>
        <span className={styles.muted}> {item.ref}</span>
        <br />
        <span className={styles.muted}>
          {item.current_office ? `Held at ${item.current_office.name}` : item.category.name}
        </span>
      </div>
    </div>
  );
}

/** The single most important thing for the claimant: what to do now. */
function NextStep({ claim }) {
  const office = claim.item.current_office;
  const officeText = office && (
    <>
      <strong>{office.name}</strong>, {office.location_description}. Open {office.opening_hours}.
    </>
  );

  let content = null;
  if (claim.on_hold) {
    content = (
      <>
        <h2>Waiting</h2>
        <p>
          Another claim on this item has been approved. If that person doesn't collect it, or it turns out not to be
          theirs, your claim will be reviewed. You'll get an email if anything changes.
        </p>
      </>
    );
  } else if (claim.status === "SUBMITTED" || claim.status === "UNDER_REVIEW") {
    content = (
      <>
        <h2>Next: a guard reviews your claim</h2>
        <p>You'll get an email when they decide. There's nothing you need to do now.</p>
      </>
    );
  } else if (claim.status === "NEEDS_MORE_INFO") {
    content = (
      <>
        <h2>Next: visit the security office</h2>
        <p>The guard needs to talk to you in person before deciding. Bring your student ID card.</p>
        {claim.staff_message && <p className={styles.message}>“{claim.staff_message}”</p>}
        {office && <p>{officeText}</p>}
      </>
    );
  } else if (claim.status === "APPROVED") {
    content = (
      <>
        <h2>Next: collect your item by {formatDateTime(claim.collect_by)}</h2>
        {office && <p>{officeText}</p>}
        <p>
          Bring your <strong>student ID card</strong>. The guard checks your name and roll number before handing it
          over. Only you can collect it.
        </p>
        {claim.staff_message && <p className={styles.message}>Note from the guard: “{claim.staff_message}”</p>}
      </>
    );
  } else if (claim.status === "COLLECTED") {
    content = (
      <>
        <h2>Collected</h2>
        <p>You collected this item on {formatDateTime(claim.collected_at)}. Glad it's back with you.</p>
      </>
    );
  } else if (claim.status === "REJECTED") {
    content = (
      <>
        <h2>Your claim was not approved</h2>
        {claim.staff_message && <p className={styles.message}>Reason: “{claim.staff_message}”</p>}
        <p>If you have new evidence, you can claim the item again from its page while it's still held.</p>
      </>
    );
  } else if (claim.status === "EXPIRED") {
    content = (
      <>
        <h2>Expired</h2>
        <p>The item wasn't collected within 7 days. If it's still yours, contact the security office.</p>
      </>
    );
  } else if (claim.status === "CLOSED") {
    content = (
      <>
        <h2>Closed</h2>
        <p>This item was returned to someone else after an ID check at the office.</p>
      </>
    );
  } else if (claim.status === "WITHDRAWN") {
    content = (
      <>
        <h2>Withdrawn</h2>
        <p>You withdrew this claim.</p>
      </>
    );
  }
  return (
    <section className={styles.nextStep} data-status={claim.on_hold ? "ON_HOLD" : claim.status} aria-live="polite">
      {content}
    </section>
  );
}

function Proofs({ claim, onChanged }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function upload(event) {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      onChanged(await apiUpload(`/claims/${claim.id}/proofs`, file));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {claim.proof_ids.length > 0 && (
        <>
          <h3 className={styles.subheading}>Proof images</h3>
          <ul className={styles.proofs}>
            {claim.proof_ids.map((proofId, index) => (
              <li key={proofId}>
                <a href={proofUrl(claim.id, proofId)} target="_blank" rel="noreferrer">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={proofUrl(claim.id, proofId)} alt={`Proof image ${index + 1} (opens full size)`} />
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
      {claim.proof_ids.length === 0 && !claim.can_add_proof && (
        <p className={styles.muted}>
          {OPEN_STATUSES.includes(claim.status)
            ? "No proof images."
            : "No proof images. (Proof images are deleted 2 days after a claim closes.)"}
        </p>
      )}
      {claim.can_add_proof && (
        <div className="field">
          <label htmlFor="add-proof">Add a proof image</label>
          <span className="hint">
            Up to 3, until a guard starts reviewing. Only security staff and you can see them.
          </span>
          <input
            id="add-proof"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={upload}
            disabled={busy}
          />
          {busy && <span className="hint">Uploading…</span>}
          {error && <span className="field-error">{error}</span>}
        </div>
      )}
    </>
  );
}

const EVENT_WORDS = {
  SUBMITTED: "Claim sent",
  UNDER_REVIEW: "Review started",
  NEEDS_MORE_INFO: "Asked to visit the office",
  APPROVED: "Approved",
  COLLECTED: "Handed over",
  REJECTED: "Not approved",
  WITHDRAWN: "Withdrawn",
  EXPIRED: "Expired",
  CLOSED: "Closed",
};

function History({ events }) {
  return (
    <section className={styles.panel} aria-labelledby="history-heading">
      <h2 id="history-heading">History</h2>
      <ol className={styles.history}>
        {events.map((event) => (
          <li key={event.id}>
            <strong>
              {event.from_status === "NEEDS_MORE_INFO" && event.to_status === "UNDER_REVIEW"
                ? "Office visit done, review continued"
                : EVENT_WORDS[event.to_status]}
            </strong>
            <span className={styles.muted}>
              {" "}
              {formatDateTime(event.created_at)}
              {event.actor_name ? `, by ${event.actor_name}` : event.from_status ? ", automatically" : ""}
            </span>
            {event.note && <p className={styles.note}>{event.note}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Withdraw({ claim, onChanged }) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      onChanged(await apiFetch(`/claims/${claim.id}/withdraw`, { method: "POST" }));
      setAsking(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.panel}>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {asking ? (
        <>
          <p>Withdraw this claim? You can claim the item again later while it's still held.</p>
          <div className="button-row">
            <button type="button" className="button" onClick={confirm} disabled={busy}>
              {busy ? "Withdrawing…" : "Withdraw claim"}
            </button>
            <button type="button" className="link-button" onClick={() => setAsking(false)}>
              Keep my claim
            </button>
          </div>
        </>
      ) : (
        <button type="button" className="link-button" onClick={() => setAsking(true)}>
          It's not mine after all: withdraw this claim
        </button>
      )}
    </section>
  );
}

// ---------------- Staff ----------------

function StaffReview({ claim, onChanged }) {
  const person = claim.claimant;
  return (
    <section className={styles.staff} aria-labelledby="review-heading">
      <h2 id="review-heading">Review (security staff only)</h2>

      {claim.on_hold && (
        <p className={styles.warning}>
          Another claim on this item is approved and waiting for collection. Approve this one only after that claim
          is rejected or expires.
        </p>
      )}

      <p className={styles.muted}>
        Check the claimant&apos;s description below against the item itself:{" "}
        <Link href={`/items/${claim.item.id}`}>{claim.item.ref}</Link>. Ask for an office visit if you can&apos;t be sure.
      </p>

      <div className={styles.compare}>
        <div>
          <h3 className={styles.subheading}>Claimant</h3>
          <p>
            <strong>{person.full_name}</strong>
            {person.roll_number && <> (roll no. {person.roll_number})</>}
            <br />
            {person.email}
            {person.phone && (
              <>
                <br />
                {person.phone}
              </>
            )}
          </p>
        </div>
      </div>

      {claim.other_claims.length > 0 && (
        <>
          <h3 className={styles.subheading}>Other claims on this item</h3>
          <ul className={styles.others}>
            {claim.other_claims.map((other) => (
              <li key={other.id}>
                <Link href={`/claims/${other.id}`}>{other.ref}</Link> by {other.claimant_name},{" "}
                {CLAIM_STATUS_LABELS[other.status].toLowerCase()}
              </li>
            ))}
          </ul>
        </>
      )}

      <StaffActions claim={claim} onChanged={onChanged} />
    </section>
  );
}

// Same minimum as the backend (RequiredNoteIn in schemas/claim.py).
const MIN_NOTE = 1;

/** Each action that needs typing opens its own small form; only one at a time. */
function StaffActions({ claim, onChanged }) {
  const [open, setOpen] = useState(null); // "visit" | "resume" | "approve" | "reject" | "handover" | null
  const [note, setNote] = useState("");
  const [idChecked, setIdChecked] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function show(name) {
    setOpen(name);
    setNote("");
    setIdChecked(false);
    setError("");
  }

  async function send(path, body) {
    setBusy(true);
    setError("");
    try {
      onChanged(await apiFetch(`/staff/claims/${claim.id}/${path}`, { method: "POST", body }));
      setOpen(null);
    } catch (err) {
      setError(err.fieldErrors?.note || err.fieldErrors?.id_checked || err.message);
    } finally {
      setBusy(false);
    }
  }

  const any =
    claim.can_start_review ||
    claim.can_request_info ||
    claim.can_resume_review ||
    claim.can_approve ||
    claim.can_reject ||
    claim.can_hand_over;
  if (!any) return null;

  const FORMS = {
    visit: {
      label: "What should they bring or explain at the office?",
      required: true,
      button: "Ask them to visit",
      submit: () => send("request-visit", { note }),
    },
    resume: {
      label: "What did the claimant show or explain at the office?",
      required: true,
      button: "Save and continue review",
      submit: () => send("resume", { note }),
    },
    approve: {
      label: "Note for the claimant",
      required: false,
      button: "Approve claim",
      submit: () => send("approve", { note: note.trim() || null }),
    },
    reject: {
      label: "Reason (the claimant is told this)",
      required: true,
      button: "Reject claim",
      submit: () => send("reject", { note }),
    },
    handover: {
      label: "Note",
      required: false,
      button: "Hand over item",
      submit: () => send("hand-over", { id_checked: idChecked, note: note.trim() || null }),
    },
  };
  const form = open && FORMS[open];

  return (
    <div className={styles.actions}>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      {!form && (
        <div className="button-row">
          {claim.can_start_review && (
            <button type="button" className="button" disabled={busy} onClick={() => send("start-review")}>
              {busy ? "Starting…" : "Start review"}
            </button>
          )}
          {claim.can_hand_over && (
            <button type="button" className="button" onClick={() => show("handover")}>
              Hand over item
            </button>
          )}
          {claim.can_approve && (
            <button type="button" className="button" onClick={() => show("approve")}>
              Approve
            </button>
          )}
          {claim.can_resume_review && (
            <button type="button" className="button" onClick={() => show("resume")}>
              Claimant visited: continue review
            </button>
          )}
          {claim.can_request_info && (
            <button type="button" className="button button-secondary" onClick={() => show("visit")}>
              Ask for an office visit
            </button>
          )}
          {claim.can_reject && (
            <button type="button" className="button button-secondary" onClick={() => show("reject")}>
              Reject
            </button>
          )}
        </div>
      )}

      {form && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            form.submit();
          }}
        >
          {open === "handover" && (
            <div className={styles.idCheck}>
              <p>
                Check the student ID card. It must show:
                <br />
                <strong>{claim.claimant.full_name}</strong>, roll no.{" "}
                <strong>{claim.claimant.roll_number || "(staff member: check staff ID)"}</strong>
              </p>
              <label>
                <input type="checkbox" checked={idChecked} onChange={(e) => setIdChecked(e.target.checked)} /> I
                checked the ID card, and the name and roll number match
              </label>
              <p className={styles.muted}>The return time is recorded automatically when you press the button.</p>
            </div>
          )}
          {open === "approve" && (
            <p className={styles.muted}>
              The claimant is emailed the office details and has 7 days to collect the item in person.
            </p>
          )}
          <div className="field">
            <label htmlFor="staff-note">
              {form.label}
              {form.required && <span className="req">*</span>}
            </label>
            {/* Say WHY the button is disabled, instead of leaving it silently grey. */}
            {form.required && (
              <span className="hint" id="staff-note-hint" aria-live="polite">
                {note.trim().length < MIN_NOTE
                  ? "Required: write a short note."
                  : "The claimant can see this note."}
              </span>
            )}
            <textarea
              id="staff-note"
              rows={3}
              maxLength={500}
              value={note}
              required={form.required}
              aria-describedby={form.required ? "staff-note-hint" : undefined}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <div className="button-row">
            <button
              type="submit"
              className="button"
              disabled={busy || (open === "handover" && !idChecked) || (form.required && note.trim().length < MIN_NOTE)}
            >
              {busy ? "Saving…" : form.button}
            </button>
            <button type="button" className="link-button" onClick={() => setOpen(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
