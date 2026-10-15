"use client";

/**
 * One item's page. What's shown depends on who's looking; the backend
 * decides, and sends flags like can_close and can_claim.
 */

import { ImageOff, Lock, Pencil, Plus, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import SimilarItems from "@/components/SimilarItems";
import StaffPanel from "@/components/StaffPanel";
import { apiFetch, apiUpload, photoUrl } from "@/lib/api";
import {
  CLAIM_STATUS_LABELS,
  OPEN_CLAIM_STATUSES,
  STATUS_LABELS,
  formatDate,
  formatDateTime,
  todayIso,
} from "@/lib/labels";
import styles from "./item.module.css";

// Colour of the status pill under the title.
const STATUS_PILL = {
  AWAITING_DROPOFF: "pill-amber",
  IN_CUSTODY: "pill-found",
  CLAIM_PENDING: "pill-brand",
  RETURNED: "pill-found",
  OPEN: "pill-lost",
};

// Statuses shown on the public Found and Lost pages.
const PUBLIC = ["IN_CUSTODY", "CLAIM_PENDING", "OPEN"];

export default function ItemPage() {
  return (
    <AppShell wide>
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <ItemDetail />
      </Suspense>
    </AppShell>
  );
}

function ItemDetail() {
  const { id } = useParams(); // from the address: /items/<id>
  const params = useSearchParams();
  const { user } = useAuth();
  const [item, setItem] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    apiFetch(`/items/${id}`)
      .then((data) => {
        setItem(data);
        setError("");
      })
      .catch((err) => setError(err.status === 404 ? "This item doesn't exist, or you can't see it." : err.message));
  }, [id]);

  useEffect(load, [load]);

  if (error) {
    return (
      <>
        <div className="alert" role="alert">
          {error}
        </div>
        <Link href="/found">Back to found items</Link>
      </>
    );
  }
  if (!item) return <p className="loading">Loading…</p>;

  const isFound = item.kind === "FOUND";
  const isStaff = user.role !== "STUDENT";
  const photosFailed = Number(params.get("photos_failed")) || 0;

  return (
    <article>
      {params.get("new") && (
        <div className="success" role="status">
          {!isFound
            ? "Your lost item report is saved."
            : item.status === "IN_CUSTODY"
              ? "Item registered. It's on the Found page now."
              : "Thanks for reporting it."}
          {photosFailed > 0 && ` ${photosFailed} photo(s) couldn't be uploaded; you can add them below.`}
        </div>
      )}

      <p className={styles.back}>
        {/* Items not on the public pages (e.g. waiting to be handed in) link back to My reports. */}
        {item.status === "RETURNED" ? (
          <Link href="/returned">Returned items</Link>
        ) : item.is_mine && !PUBLIC.includes(item.status) ? (
          <Link href="/my-reports">My reports</Link>
        ) : (
          <Link href={isFound ? "/found" : "/lost"}>{isFound ? "Found items" : "Lost items"}</Link>
        )}
      </p>

      <header className={styles.header}>
        <span className={styles.ref}>
          {item.ref}
          <span className={styles.stamp} data-kind={item.status === "RETURNED" ? "RETURNED" : item.kind}>
            {item.status === "RETURNED" ? "Returned" : isFound ? "Found" : "Lost"}
          </span>
        </span>
        <h1>{item.title}</h1>
        <span className={`pill ${STATUS_PILL[item.status] || ""}`}>{STATUS_LABELS[item.status]}</span>
      </header>








      <div className={styles.layout}>
        <div className={styles.mainCol}>
          <Gallery item={item} onChanged={setItem} />
          <DetailsPanel item={item} onSaved={setItem} />
          {item.can_see_similar && (
            <SimilarItems
              item={item}
              // Changes after an edit, a photo change or a status change, so the
              // matches are fetched again (the backend re-runs matching then).
              version={[item.title, item.description, item.category.id, item.colour, item.brand,
                item.location_text, item.happened_on, item.status, item.photo_ids.join(",")].join("|")}
            />
          )}

          {isStaff && isFound && item.claims.length > 0 && <ItemClaims claims={item.claims} />}

          {isStaff && isFound && <StaffPanel item={item} onChanged={setItem} />}

        </div>

        <aside className={styles.sideCol}>
          {/* What happens next: only for the finder, before hand-in. */}
          {isFound && item.status === "AWAITING_DROPOFF" && item.is_mine && item.dropoff_office && (
            <HandInBox item={item} onChanged={setItem} />
          )}
          {isFound && item.current_office_full && (
            <section className={styles.office} aria-labelledby="where-held">
              <h2 id="where-held">Where it's held</h2>
              <p>
                <strong>{item.current_office_full.name}</strong>
                <br />
                {item.current_office_full.location_description}
                <br />
                Open {item.current_office_full.opening_hours}
              </p>
            </section>
          )}
          {item.returned_at && (
            <p className="success" role="status">
              Returned to its owner on {formatDateTime(item.returned_at)}.
            </p>
          )}
          {isFound && <ClaimBox item={item} />}
          {!isFound && !item.is_mine && item.status === "OPEN" && (
            <section className={styles.panel}>
              <h2>Found it?</h2>
              <p>Hand it in at a security office and tell us what you found.</p>
              <Link href={`/report/found?for=${item.id}`} className="button">
                I found this
              </Link>
            </section>
          )}
          {item.can_close && <CloseLost item={item} onClosed={setItem} />}
        </aside>
      </div>
    </article>
  );
}

/** For the finder before hand-in: where to take it, with "Change office"
 * (a dropdown, right here) and "Cancel this report". */
function HandInBox({ item, onChanged }) {
  const router = useRouter();
  const [mode, setMode] = useState(null); // null | "office" | "cancel"
  const [offices, setOffices] = useState([]);
  const [officeId, setOfficeId] = useState(String(item.dropoff_office.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function startChanging() {
    setMode("office");
    setError("");
    setOfficeId(String(item.dropoff_office.id));
    if (!offices.length) apiFetch("/offices").then(setOffices).catch(() => setError("Couldn't load the office list."));
  }

  async function saveOffice(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const updated = await apiFetch(`/items/${item.id}/dropoff-office`, {
        method: "PUT",
        body: { office_id: Number(officeId) },
      });
      onChanged(updated);
      setMode(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancelReport() {
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/items/${item.id}`, { method: "DELETE" });
      router.push("/my-reports?cancelled=1");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const office = item.dropoff_office;
  return (
    <section className={styles.nextStep} aria-labelledby="next-step">
      <h2 id="next-step">Next: hand it in</h2>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {mode === "office" ? (
        <form onSubmit={saveOffice} className={styles.handInForm}>
          <div className="field">
            <label htmlFor="change-office">Hand it in at <span className="req">*</span></label>
            <select id="change-office" value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
              {(offices.length ? offices : [office]).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
          <div className="button-row">
            <button type="submit" className="button" disabled={busy || officeId === String(office.id)}>
              {busy ? "Saving…" : "Save office"}
            </button>
            <button type="button" className="link-button" onClick={() => setMode(null)} disabled={busy}>
              Back
            </button>
          </div>
        </form>
      ) : (
        <p>
          Take the item to <strong>{office.name}</strong>. {office.location_description}. Open {office.opening_hours}.
        </p>
      )}

      {mode === "cancel" && (
        <div className={styles.cancelConfirm} role="alert">
          <p>Cancel this report? It will be deleted and can&apos;t be undone.</p>
          <div className="button-row">
            <button type="button" className="button button-danger" onClick={cancelReport} disabled={busy}>
              {busy ? "Cancelling…" : "Yes, cancel it"}
            </button>
            <button type="button" className="link-button" onClick={() => setMode(null)} disabled={busy}>
              Keep it
            </button>
          </div>
        </div>
      )}

      {mode === null && (item.can_change_dropoff || item.can_cancel) && (
        <div className={styles.handInActions}>
          {item.can_change_dropoff && (
            <button type="button" className="button button-secondary" onClick={startChanging}>
              Change office
            </button>
          )}
          {item.can_cancel && (
            <button type="button" className="button button-danger" onClick={() => setMode("cancel")}>
              Cancel this report
            </button>
          )}
        </div>
      )}

      <p className={styles.handInNote}>
        Only you can see this report until a guard receives the item. Then it appears on the Found page.
      </p>
    </section>
  );
}

/** "This is mine", or a link to my claim if I already made one. */
function ClaimBox({ item }) {
  const mine = item.my_claim;
  const mineIsOpen = mine && OPEN_CLAIM_STATUSES.includes(mine.status);

  if (mineIsOpen) {
    return (
      <section className={styles.nextStep} aria-labelledby="claim-heading">
        <h2 id="claim-heading">Your claim</h2>
        <p>
          <Link href={`/claims/${mine.id}`}>{mine.ref}</Link>: {CLAIM_STATUS_LABELS[mine.status].toLowerCase()}.
        </p>
      </section>
    );
  }
  if (!item.can_claim) return null;
  return (
    <section className={styles.panel} aria-labelledby="claim-heading">
      <h2 id="claim-heading">Is this yours?</h2>
      <p>
        Tell a security guard something only the owner would know. If they agree it's yours, you collect it from the
        office with your student ID card.
        {mine && (
          <>
            {" "}
            Your earlier claim <Link href={`/claims/${mine.id}`}>{mine.ref}</Link> was{" "}
            {CLAIM_STATUS_LABELS[mine.status].toLowerCase()}.
          </>
        )}
      </p>
      <p>
        <Link href={`/items/${item.id}/claim`} className="button">
          This is mine
        </Link>
      </p>
    </section>
  );
}

/** For staff: every claim on this item. */
function ItemClaims({ claims }) {
  return (
    <section className={styles.panel} aria-labelledby="claims-heading">
      <h2 id="claims-heading">Claims on this item</h2>
      <ul>
        {claims.map((claim) => (
          <li key={claim.id}>
            <Link href={`/claims/${claim.id}`}>{claim.ref}</Link> by {claim.claimant_name},{" "}
            {CLAIM_STATUS_LABELS[claim.status].toLowerCase()} (sent {formatDateTime(claim.created_at)})
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The Details box. The reporter sees "Edit" in its top-right corner;
 * clicking it turns the box into a form right here (no separate page).
 * Save sends PUT /items/{id}; the backend checks everything again. */
function DetailsPanel({ item, onSaved }) {
  const isFound = item.kind === "FOUND";
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(null);
  const [categories, setCategories] = useState([]);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [busy, setBusy] = useState(false);

  function startEditing() {
    // Start from what's saved now. Empty optional fields become "".
    setForm({
      title: item.title,
      category_id: String(item.category.id),
      colour: item.colour || "",
      brand: item.brand || "",
      description: item.description,
      location_text: item.location_text,
      happened_on: item.happened_on,
    });
    setError("");
    setFieldErrors({});
    setEditing(true);
    if (!categories.length) apiFetch("/categories").then(setCategories).catch(() => setCategories([]));
  }

  function update(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
    setFieldErrors((current) => ({ ...current, [name]: undefined }));
  }

  async function save(event) {
    event.preventDefault();
    const problems = {};
    if (form.title.trim().length < 1) problems.title = "Please fill this in.";
    if (form.description.trim().length < 1) problems.description = "Please fill this in.";
    if (form.location_text.trim().length < 1) problems.location_text = "Please fill this in.";
    if (!form.happened_on) problems.happened_on = "Choose the date.";
    setFieldErrors(problems);
    if (Object.keys(problems).length) return;

    setBusy(true);
    setError("");
    try {
      const updated = await apiFetch(`/items/${item.id}`, {
        method: "PUT",
        body: { ...form, category_id: Number(form.category_id) },
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setFieldErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function field(name, label, input) {
    return (
      <div className="field">
        <label htmlFor={`edit-${name}`}>{label}</label>
        {input}
        {fieldErrors[name] && <span className="field-error">{fieldErrors[name]}</span>}
      </div>
    );
  }
  const invalid = (name) => (fieldErrors[name] ? "true" : undefined);

  return (
    <section className={styles.detailsPanel} aria-labelledby="details-heading">
      <div className={styles.panelHead}>
        <h2 id="details-heading" className={styles.panelTitle}>
          {editing ? "Edit details" : "Details"}
        </h2>
        {item.can_edit && !editing && (
          <button type="button" className={styles.editLink} onClick={startEditing}>
            <Pencil size={14} aria-hidden="true" /> Edit
          </button>
        )}
      </div>

      {!editing ? (
        <>
          <dl className={styles.facts}>
            <dt>Category</dt>
            <dd>{item.category.name}</dd>
            {item.colour && (
              <>
                <dt>Colour</dt>
                <dd>{item.colour}</dd>
              </>
            )}
            {item.brand && (
              <>
                <dt>Brand</dt>
                <dd>{item.brand}</dd>
              </>
            )}
            <dt>{isFound ? "Found at" : "Lost at"}</dt>
            <dd>{item.location_text}</dd>
            <dt>{isFound ? "Found on" : "Lost on"}</dt>
            <dd>{formatDate(item.happened_on)}</dd>
            {item.answers_lost_ref && (
              <>
                <dt>Lost report</dt>
                <dd>
                  <Link href={`/items/${item.answers_lost_id}`}>{item.answers_lost_ref}</Link>
                </dd>
              </>
            )}
          </dl>
          {/* "I found this" reports: the details are the owner's, so no Edit. */}
          {item.answers_lost_ref && item.is_mine && item.status === "AWAITING_DROPOFF" && (
            <p className={styles.small}>
              These details come from the owner&apos;s lost report, so they can&apos;t be edited here.
            </p>
          )}
          <h3 className={styles.descTitle}>Description</h3>
          <p className={styles.description}>{item.description}</p>
          {/* Phase C: written by the AI from the photos. */}
          {item.ai_description && (
            <div className={styles.aiDescription}>
              <h3 className={styles.descTitle}>
                <Sparkles size={15} aria-hidden="true" /> AI description <span>from the photos</span>
              </h3>
              <p>{item.ai_description}</p>
              {item.ai_marks.length > 0 && (
                <ul className={styles.aiMarks} aria-label="Distinctive marks seen by the AI">
                  {item.ai_marks.map((mark) => (
                    <li key={mark}>{mark}</li>
                  ))}
                </ul>
              )}
              <p className={styles.aiNote}>Generated automatically; it may be inaccurate.</p>
            </div>
          )}
        </>
      ) : (
        <form onSubmit={save} noValidate className={styles.editForm}>
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {field(
            "title",
            <>Item name <span className="req">*</span></>,
            <input id="edit-title" name="title" value={form.title} onChange={update} maxLength={120} aria-invalid={invalid("title")} />,
          )}
          {field(
            "category_id",
            <>Category <span className="req">*</span></>,
            <select id="edit-category_id" name="category_id" value={form.category_id} onChange={update}>
              {/* Until the list loads, show the current category so the value is never blank. */}
              {(categories.length ? categories : [item.category]).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>,
          )}
          <div className="form-row">
            {field("colour", "Colour", <input id="edit-colour" name="colour" value={form.colour} onChange={update} maxLength={40} />)}
            {field("brand", "Brand", <input id="edit-brand" name="brand" value={form.brand} onChange={update} maxLength={60} />)}
          </div>
          <div className="form-row">
            {field(
              "location_text",
              <>{isFound ? "Found at" : "Lost at"} <span className="req">*</span></>,
              <input id="edit-location_text" name="location_text" value={form.location_text} onChange={update} maxLength={200} aria-invalid={invalid("location_text")} />,
            )}
            {field(
              "happened_on",
              <>{isFound ? "Found on" : "Lost on"} <span className="req">*</span></>,
              <input id="edit-happened_on" name="happened_on" type="date" max={todayIso()} value={form.happened_on} onChange={update} aria-invalid={invalid("happened_on")} />,
            )}
          </div>
          {field(
            "description",
            <>Description <span className="req">*</span></>,
            <textarea id="edit-description" name="description" value={form.description} onChange={update} maxLength={1000} aria-invalid={invalid("description")} />,
          )}
          <div className="button-row">
            <button type="submit" className="button" disabled={busy}>
              {busy ? "Saving…" : "Save changes"}
            </button>
            <button type="button" className="link-button" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

const MAX_PHOTOS = 3;

/** The photos: one big photo, and a strip of small ones underneath.
 * The reporter (while the report is editable) and staff (for found items
 * they hold) also get a round × on each small photo to remove it, and a
 * "+ Add" tile at the end of the strip (up to 3 photos in total).
 * To replace a photo: remove it, then add the new one. */
function Gallery({ item, onChanged }) {
  const [shown, setShown] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const photos = item.photo_ids;
  const current = Math.min(shown, Math.max(photos.length - 1, 0)); // stays valid after a removal
  const room = MAX_PHOTOS - photos.length;
  const canAdd = item.can_add_photos && room > 0;
  const showStrip = photos.length > 1 || item.can_remove_photos || canAdd;

  async function add(event) {
    const files = [...event.target.files].slice(0, room);
    event.target.value = ""; // so choosing the same file again still works
    if (!files.length) return;
    setBusy(true);
    setError("");
    let latest = null;
    const problems = [];
    // One request per photo; each answers with the updated item.
    for (const file of files) {
      try {
        latest = await apiUpload(`/items/${item.id}/photos`, file);
      } catch (err) {
        problems.push(`${file.name}: ${err.message}`);
      }
    }
    if (latest) {
      onChanged(latest);
      setShown(latest.photo_ids.length - 1); // show the newest photo
    }
    if (problems.length) setError(problems.join(" "));
    setBusy(false);
  }

  async function remove(photoId) {
    setBusy(true);
    setError("");
    try {
      onChanged(await apiFetch(`/items/${item.id}/photos/${photoId}`, { method: "DELETE" }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.gallery}>
      {photos.length > 0 ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.mainPhoto} src={photoUrl(item.id, photos[current])} alt={`Photo of ${item.title}`} />
      ) : item.photos_hidden ? (
        // ID cards and documents: the photo shows someone's personal details.
        <div className={styles.noPhoto}>
          <Lock size={26} strokeWidth={1.5} aria-hidden="true" />
          <span>Photo hidden to protect personal details</span>
        </div>
      ) : (
        <div className={styles.noPhoto}>
          <ImageOff size={28} strokeWidth={1.4} aria-hidden="true" />
          <span>No photo yet</span>
        </div>
      )}

      {showStrip && (
        <div className={styles.thumbs}>
          {photos.map((photoId, index) => (
            <div key={photoId} className={styles.thumbWrap}>
              <button
                type="button"
                className={styles.thumb}
                aria-label={`Show photo ${index + 1}`}
                aria-pressed={index === current}
                onClick={() => setShown(index)}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={photoUrl(item.id, photoId)} alt="" />
              </button>
              {item.can_remove_photos && (
                <button
                  type="button"
                  className={styles.removePhoto}
                  aria-label={`Remove photo ${index + 1}`}
                  title="Remove photo"
                  onClick={() => remove(photoId)}
                  disabled={busy}
                >
                  <X size={16} strokeWidth={2.4} aria-hidden="true" />
                </button>
              )}
            </div>
          ))}

          {canAdd && (
            // A label around a hidden file input: clicking the tile opens the file picker.
            <label className={styles.addTile} title={`Add photos (${room} more allowed)`} aria-disabled={busy}>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                onChange={add}
                disabled={busy}
                className="visually-hidden"
              />
              <Plus size={20} aria-hidden="true" />
              <span>{busy ? "Saving…" : "Add"}</span>
            </label>
          )}
        </div>
      )}

      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** Closing a lost report asks for confirmation first, because it removes the
 * report from the Lost page and can't be undone. */
function CloseLost({ item, onClosed }) {
  const [choice, setChoice] = useState(null); // "RECOVERED" | "CLOSED" | null
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      onClosed(await apiFetch(`/items/${item.id}/close`, { method: "POST", body: { outcome: choice } }));
      setChoice(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.panel} aria-labelledby="close-heading">
      <h2 id="close-heading">Close this report</h2>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {choice ? (
        <>
          <p>
            {choice === "RECOVERED" ? "Glad you got it back. " : ""}This removes the report from the Lost page, and it
            can't be reopened.
          </p>
          <div className="button-row">
            <button type="button" className="button" onClick={confirm} disabled={busy}>
              {busy ? "Closing…" : "Close report"}
            </button>
            <button type="button" className="link-button" onClick={() => setChoice(null)}>
              Keep it open
            </button>
          </div>
        </>
      ) : (
        <div className="button-row">
          <button type="button" className="button button-secondary" onClick={() => setChoice("RECOVERED")}>
            I got it back
          </button>
          <button type="button" className="button button-secondary" onClick={() => setChoice("CLOSED")}>
            I've stopped looking
          </button>
        </div>
      )}
    </section>
  );
}
