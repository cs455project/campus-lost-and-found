"use client";

/**
 * The report form, shared by three pages:
 *   kind="LOST"     Report a lost item
 *   kind="FOUND"    Hand in a found item (finder chooses an office)
 *   kind="WALK_IN"  Staff register an item handed in at the desk
 *
 * Submitting happens in two steps:
 *   1. POST /items/lost, /items/found or /staff/items/walk-in (JSON)
 *   2. POST /items/{id}/photos once per photo (file upload)
 * Then we open the new item's page.
 *
 * "I found this" on a lost report opens kind="FOUND" with ?for=<lost id>.
 * Then the item is already described: we show the lost report in one
 * summary card instead of "What is it?" and Photos, copy its name,
 * category, colour and brand, and ask only for an optional note plus
 * where/when it was found and which office it goes to.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { apiFetch, apiUpload, photoUrl } from "@/lib/api";
import { formatDate, todayIso } from "@/lib/labels";
import styles from "./ReportForm.module.css";

const MAX_PHOTOS = 3;
const MAX_PHOTO_MB = 5;
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

const EMPTY = {
  title: "",
  category_id: "",
  description: "",
  colour: "",
  brand: "",
  location_text: "",
  happened_on: "",
  dropoff_office_id: "",
  handed_in_by: "",
  note: "", // only when answering a lost report: the finder's optional extra details
};

export default function ReportForm({ kind }) {
  const router = useRouter();
  const params = useSearchParams();
  const { user } = useAuth();
  const isWalkIn = kind === "WALK_IN";
  const isFound = kind !== "LOST"; // found and walk-in both describe a found item
  // "I found this" on the Lost page links here with ?for=<lost item id>.
  const forLostId = kind === "FOUND" ? params.get("for") : null;

  // Walk-ins start at the guard's usual office, if they have one.
  const [form, setForm] = useState(() => ({
    ...EMPTY,
    dropoff_office_id: isWalkIn && user?.office ? String(user.office.id) : "",
  }));
  const [photos, setPhotos] = useState([]); // [{ file, preview }]
  const [categories, setCategories] = useState([]);
  const [offices, setOffices] = useState([]);
  // undefined = still loading, null = none (or it couldn't be loaded), else the lost report
  const [lostItem, setLostItem] = useState(forLostId ? undefined : null);
  const matchMode = Boolean(lostItem); // answering a lost report
  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiFetch("/categories").then(setCategories).catch(() => setError("Couldn't load the category list."));
    if (isFound) apiFetch("/offices").then(setOffices).catch(() => setError("Couldn't load the office list."));
  }, [isFound]);

  // Prefill from the lost report this finder is responding to.
  useEffect(() => {
    if (!forLostId) return;
    apiFetch(`/items/${forLostId}`)
      .then((item) => {
        setLostItem(item);
        setForm((current) => ({
          ...current,
          title: item.title,
          category_id: String(item.category.id),
          colour: item.colour || "",
          brand: item.brand || "",
        }));
      })
      .catch(() => {
        setLostItem(null);
        setError("Couldn't load that lost report (it may be closed). Please describe the item yourself.");
      });
  }, [forLostId]);

  // Previews are temporary in-memory addresses (blob: URLs). Free them when
  // the page closes; removePhoto frees each one as it's removed.
  const photosRef = useRef(photos);
  photosRef.current = photos;
  useEffect(() => {
    return () => photosRef.current.forEach((p) => URL.revokeObjectURL(p.preview));
  }, []);

  function update(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
    setFieldErrors((current) => ({ ...current, [name]: undefined }));
  }

  function addPhotos(event) {
    const chosen = [...event.target.files];
    event.target.value = ""; // allow choosing the same file again later
    const problems = [];
    const accepted = [];
    for (const file of chosen) {
      if (!PHOTO_TYPES.includes(file.type)) problems.push(`${file.name}: use a JPEG, PNG or WebP photo.`);
      else if (file.size > MAX_PHOTO_MB * 1024 * 1024) problems.push(`${file.name}: larger than ${MAX_PHOTO_MB} MB.`);
      else accepted.push({ file, preview: URL.createObjectURL(file) });
    }
    const room = MAX_PHOTOS - photos.length;
    if (accepted.length > room) problems.push(`You can add up to ${MAX_PHOTOS} photos.`);
    setPhotos((current) => [...current, ...accepted.slice(0, room)]);
    setFieldErrors((current) => ({ ...current, photos: problems.join(" ") || undefined }));
  }

  function removePhoto(index) {
    URL.revokeObjectURL(photos[index].preview);
    setPhotos((current) => current.filter((_, i) => i !== index));
  }

  /** Quick checks before sending, so common mistakes get instant messages.
   * The backend checks everything again: it never trusts the browser. */
  function checkForm() {
    const problems = {};
    // When answering a lost report, the name, category and description come from it.
    if (!matchMode) {
      if (form.title.trim().length < 1) problems.title = "Give it a short name.";
      if (!form.category_id) problems.category_id = "Choose a category.";
      if (form.description.trim().length < 1) problems.description = "Please describe it.";
    }
    if (form.location_text.trim().length < 1) problems.location_text = "Say where.";
    if (!form.happened_on) problems.happened_on = "Choose the date.";
    if (isFound && !form.dropoff_office_id) {
      problems.dropoff_office_id = isWalkIn ? "Choose the office where it is." : "Choose where you'll hand it in.";
    }
    return problems;
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    const problems = checkForm();
    setFieldErrors(problems);
    if (Object.keys(problems).length) {
      setError("Please check the highlighted fields.");
      return;
    }

    setBusy(true);
    const body = {
      title: form.title,
      category_id: Number(form.category_id),
      description: matchMode ? describeFromLost(lostItem, form.note) : form.description,
      colour: form.colour,
      brand: form.brand,
      location_text: form.location_text,
      happened_on: form.happened_on,
    };
    // Answering a lost report: the backend links the two and copies the
    // item's details from the lost report.
    if (matchMode) body.for_lost_id = lostItem.id;
    if (isWalkIn) {
      body.office_id = Number(form.dropoff_office_id);
      body.handed_in_by = form.handed_in_by;
    } else if (isFound) {
      body.dropoff_office_id = Number(form.dropoff_office_id);
    }
    const endpoint = isWalkIn ? "/staff/items/walk-in" : isFound ? "/items/found" : "/items/lost";

    let item;
    try {
      item = await apiFetch(endpoint, { method: "POST", body });
    } catch (err) {
      // The walk-in API calls the office "office_id"; the form calls it dropoff_office_id.
      const fields = { ...(err.fieldErrors || {}) };
      if (fields.office_id) fields.dropoff_office_id = fields.office_id;
      setFieldErrors(fields);
      setError(err.message);
      setBusy(false);
      return;
    }

    // The report is saved; now the photos. A failed photo doesn't undo the report.
    // (There are none when answering a lost report: that section is hidden.)
    let failed = 0;
    for (const photo of matchMode ? [] : photos) {
      try {
        await apiUpload(`/items/${item.id}/photos`, photo.file);
      } catch {
        failed += 1;
      }
    }
    router.push(`/items/${item.id}?new=1${failed ? `&photos_failed=${failed}` : ""}`);
  }

  function errorProps(name) {
    return fieldErrors[name]
      ? { "aria-invalid": "true", "aria-describedby": `${name}-error` }
      : {};
  }

  function errorText(name) {
    return fieldErrors[name] ? (
      <span className="field-error" id={`${name}-error`}>
        {fieldErrors[name]}
      </span>
    ) : null;
  }

  const chosenOffice = offices.find((office) => String(office.id) === form.dropoff_office_id);

  if (lostItem === undefined) return <p className="loading">Loading the lost report…</p>;

  return (
    <form onSubmit={submit} noValidate className={styles.form}>
      <p className="req-note">
        Fields marked <span className="req">*</span> are required.
      </p>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      {matchMode && <LostSummary item={lostItem} />}

      {!matchMode && (
        <fieldset className={styles.group}>
          <legend>What is it?</legend>
          <div className="field">
            <label htmlFor="title">Item name <span className="req">*</span></label>
            <span className="hint">A few words, e.g. "Blue steel water bottle".</span>
            <input id="title" name="title" value={form.title} onChange={update} maxLength={120} {...errorProps("title")} />
            {errorText("title")}
          </div>

          <div className="field">
            <label htmlFor="category_id">Category <span className="req">*</span></label>
            <select id="category_id" name="category_id" value={form.category_id} onChange={update} {...errorProps("category_id")}>
              <option value="">Choose a category</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            {errorText("category_id")}
          </div>

          <div className="form-row">
            <div className="field">
              <label htmlFor="colour">Colour</label>
              <input id="colour" name="colour" value={form.colour} onChange={update} maxLength={40} />
            </div>
            <div className="field">
              <label htmlFor="brand">Brand</label>
              <input id="brand" name="brand" value={form.brand} onChange={update} maxLength={60} />
            </div>
          </div>

          <div className="field">
            <label htmlFor="description">Description <span className="req">*</span></label>
            <span className="hint">
              {isFound
                ? "What it looks like: colour, size, brand, stickers, what's inside."
                : "Anything that helps someone recognise it: size, stickers, case, what was inside."}
            </span>
            <textarea id="description" name="description" value={form.description} onChange={update} maxLength={1000} {...errorProps("description")} />
            {errorText("description")}
          </div>
        </fieldset>
      )}

      <fieldset className={styles.group}>
        <legend>
          {isWalkIn ? "Where and when was it found?" : isFound ? "Where and when did you find it?" : "Where and when did you lose it?"}
        </legend>
        <div className="field">
          <label htmlFor="location_text">Place <span className="req">*</span></label>
          <span className="hint">As exact as you can, e.g. "Library, 2nd floor reading room".</span>
          <input id="location_text" name="location_text" value={form.location_text} onChange={update} maxLength={200} {...errorProps("location_text")} />
          {errorText("location_text")}
        </div>
        <div className="field">
          <label htmlFor="happened_on">Date <span className="req">*</span></label>
          <input id="happened_on" name="happened_on" type="date" max={todayIso()} value={form.happened_on} onChange={update} {...errorProps("happened_on")} />
          {errorText("happened_on")}
        </div>
        {isWalkIn && (
          <div className="field">
            <label htmlFor="handed_in_by">Handed in by</label>
            <span className="hint">Who brought it to the desk, e.g. &quot;Canteen staff&quot; or a visitor&apos;s name.</span>
            <input id="handed_in_by" name="handed_in_by" value={form.handed_in_by} onChange={update} maxLength={120} />
          </div>
        )}
      </fieldset>

      {isFound && (
        <fieldset className={styles.group} aria-describedby="office-hint">
          <legend>{isWalkIn ? "Which office is it at?" : "Where will you hand it in?"}</legend>
          <p className="hint" id="office-hint">
            {isWalkIn
              ? "The office holding the item now. It appears on the Found page as soon as you register it."
              : "Take the item to this office. It appears on the Found page once a guard receives it."}
          </p>
          <div className="field">
            <label htmlFor="dropoff_office_id">Office <span className="req">*</span></label>
            <select
              id="dropoff_office_id"
              name="dropoff_office_id"
              value={form.dropoff_office_id}
              onChange={update}
              {...errorProps("dropoff_office_id")}
            >
              <option value="">Choose an office</option>
              {offices.map((office) => (
                <option key={office.id} value={office.id}>
                  {office.name}
                </option>
              ))}
            </select>
          </div>
          {/* Where the chosen office is and when it's open. */}
          {chosenOffice && (
            <p className={styles.officeChosen}>
              <span>{chosenOffice.location_description}</span>
              <span>Open: {chosenOffice.opening_hours}</span>
            </p>
          )}
          {errorText("dropoff_office_id")}
        </fieldset>
      )}

      {!matchMode && (
        <fieldset className={styles.group}>
          <legend>Photos (up to {MAX_PHOTOS})</legend>
          <p className="hint">
            Photos help the AI describe and match the item. Don&apos;t include faces or documents: photos may be
            analysed by an AI service (photos of ID cards and documents never are).
          </p>
          {photos.length > 0 && (
            <ul className={styles.previews}>
              {photos.map((photo, index) => (
                <li key={photo.preview}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.preview} alt={`Photo ${index + 1}`} />
                  <button type="button" className="link-button" onClick={() => removePhoto(index)}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          {photos.length < MAX_PHOTOS && (
            <div className="field">
              <label htmlFor="photos" className={styles.fileLabel}>
                Add photos
              </label>
              <span className="hint">JPEG, PNG or WebP, up to {MAX_PHOTO_MB} MB each.</span>
              <input id="photos" type="file" accept={PHOTO_TYPES.join(",")} multiple onChange={addPhotos} />
            </div>
          )}
          {fieldErrors.photos && <span className="field-error">{fieldErrors.photos}</span>}
        </fieldset>
      )}

      {/* Answering a lost report: the finder's optional note comes last. */}
      {matchMode && (
        <fieldset className={styles.group}>
          <legend>Anything to add?</legend>
          <div className="field">
            <label htmlFor="note">Your description</label>
            <span className="hint">
              Anything different from the report above, or its condition, e.g. &quot;Front light missing&quot;.
            </span>
            <textarea id="note" name="note" value={form.note} onChange={update} maxLength={500} />
          </div>
        </fieldset>
      )}

      <button type="submit" className="button" disabled={busy}>
        {busy ? "Submitting…" : isWalkIn ? "Register item" : isFound ? "Submit found item" : "Submit lost item"}
      </button>
    </form>
  );
}

/** The found item's description, built from the lost report plus the
 * finder's optional note (the API needs at least 1 character, and the
 * lost report's description always has that). */
function describeFromLost(lostItem, note) {
  const extra = note.trim();
  const text = extra ? `${lostItem.description}\n\nFinder's note: ${extra}` : lostItem.description;
  return text.slice(0, 1000);
}

/** One card with everything the owner reported about the lost item. */
function LostSummary({ item }) {
  return (
    <section className={styles.lostSummary} aria-labelledby="lost-summary-heading">
      <div className={styles.lostPhoto}>
        {item.cover_photo_id ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photoUrl(item.id, item.cover_photo_id)} alt={`Photo of ${item.title}`} />
        ) : (
          <span>{item.category.name}</span>
        )}
      </div>
      <div className={styles.lostBody}>
        <span className={styles.lostRef}>
          {item.ref} <span className={styles.lostStamp}>Lost</span>
        </span>
        <h2 id="lost-summary-heading" className={styles.lostTitle}>
          You found: {item.title}
        </h2>
        <dl className={styles.lostFacts}>
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
          <dt>Lost at</dt>
          <dd>{item.location_text}</dd>
          <dt>Lost on</dt>
          <dd>{formatDate(item.happened_on)}</dd>
          <dt>Description</dt>
          <dd className={styles.lostDescription}>{item.description}</dd>
        </dl>
      </div>
    </section>
  );
}
