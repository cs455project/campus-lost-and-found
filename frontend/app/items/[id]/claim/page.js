"use client";

/**
 * "This is mine": the claim form for a found item.
 *
 * The claimant describes how they can show it's theirs (required),
 * and may add up to 3 proof images. The claim is created first; the images
 * are then uploaded one by one to that claim. If an image fails, the claim
 * still exists and the claimant can add it again from the claim page.
 */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import { apiFetch, apiUpload, photoUrl } from "@/lib/api";
import styles from "../item.module.css";
import claimStyles from "./claim.module.css";

const MAX_PROOFS = 3;
const MIN_LENGTH = 1;

export default function ClaimFormPage() {
  return (
    <AppShell>
      <ClaimForm />
    </AppShell>
  );
}

function ClaimForm() {
  const { id } = useParams();
  const router = useRouter();
  const [item, setItem] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState([]); // [{ file, preview }]
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [busy, setBusy] = useState(false);
  const previews = useRef([]);

  useEffect(() => {
    apiFetch(`/items/${id}`)
      .then(setItem)
      .catch((err) => setLoadError(err.status === 404 ? "This item doesn't exist, or you can't see it." : err.message));
  }, [id]);

  // Free the preview images' memory when leaving the page.
  useEffect(() => () => previews.current.forEach(URL.revokeObjectURL), []);

  function addFiles(event) {
    const chosen = [...event.target.files].slice(0, MAX_PROOFS - files.length);
    event.target.value = "";
    const added = chosen.map((file) => {
      const preview = URL.createObjectURL(file);
      previews.current.push(preview);
      return { file, preview };
    });
    setFiles((current) => [...current, ...added]);
  }

  function removeFile(index) {
    setFiles((current) => current.filter((_, i) => i !== index));
  }

  async function submit(event) {
    event.preventDefault();
    if (description.trim().length < MIN_LENGTH) {
      setFieldError("Please write something, so the guard can check it's yours.");
      return;
    }
    setBusy(true);
    setError("");
    setFieldError("");
    try {
      const claim = await apiFetch(`/items/${id}/claims`, { method: "POST", body: { description } });
      let failed = 0;
      for (const { file } of files) {
        try {
          await apiUpload(`/claims/${claim.id}/proofs`, file);
        } catch {
          failed += 1;
        }
      }
      router.push(`/claims/${claim.id}?new=1${failed ? `&proofs_failed=${failed}` : ""}`);
    } catch (err) {
      setError(err.message);
      setFieldError(err.fieldErrors?.description || "");
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <>
        <div className="alert" role="alert">
          {loadError}
        </div>
        <Link href="/found">Back to found items</Link>
      </>
    );
  }
  if (!item) return <p className="loading">Loading…</p>;

  if (!item.can_claim) {
    return (
      <>
        <h1>You can't claim this item</h1>
        <p className="lede">
          {item.my_claim
            ? "You already have a claim on this item."
            : item.is_mine
              ? "You reported this item, so you can't claim it."
              : "Only found items held at a security office can be claimed."}
        </p>
        {item.my_claim ? (
          <Link href={`/claims/${item.my_claim.id}`}>See your claim {item.my_claim.ref}</Link>
        ) : (
          <Link href={`/items/${item.id}`}>Back to the item</Link>
        )}
      </>
    );
  }

  return (
    <>
      <p className={styles.back}>
        <Link href={`/items/${item.id}`}>{item.title}</Link>
      </p>
      <h1>This is mine</h1>
      <p className="lede">
        Tell us something about <strong>{item.title}</strong> ({item.ref}) that only the owner would know. A
        security guard checks it against the item they&apos;re holding.
      </p>

      <div className={claimStyles.itemBrief}>
        {item.cover_photo_id && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photoUrl(item.id, item.cover_photo_id)} alt="" />
        )}
        <div>
          <strong>{item.title}</strong>
          <br />
          {item.category.name}, held at {item.current_office_full?.name}
        </div>
      </div>

      <form onSubmit={submit} noValidate>
        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}

        <div className="field">
          <label htmlFor="description">How can you show it's yours? <span className="req">*</span></label>
          <span className="hint" id="description-hint">
            For example: what's inside, a scratch or sticker, the lock-screen picture, a name written on it. Don't
            just repeat the public description.
          </span>
          <textarea
            id="description"
            rows={6}
            maxLength={2000}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            aria-describedby="description-hint"
            aria-invalid={fieldError ? "true" : undefined}
          />
          {fieldError && <span className="field-error">{fieldError}</span>}
        </div>

        <div className="field">
          <label htmlFor="proofs">Proof images</label>
          <span className="hint">
            Up to {MAX_PROOFS}: an old photo of you with it, the bill, the box. Only security staff and you can see
            them, and they're deleted 2 days after the claim closes.
          </span>
          {files.length < MAX_PROOFS && (
            <input
              id="proofs"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              onChange={addFiles}
              disabled={busy}
            />
          )}
          {files.length > 0 && (
            <ul className={claimStyles.previews}>
              {files.map(({ file, preview }, index) => (
                <li key={preview}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={preview} alt={`Proof ${index + 1}: ${file.name}`} />
                  <button type="button" className="link-button" onClick={() => removeFile(index)}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <section className={claimStyles.whatNext} aria-labelledby="what-next">
          <h2 id="what-next">What happens next</h2>
          <ol>
            <li>A security guard reviews your claim. You'll get an email when they decide.</li>
            <li>If they can't be sure from this, they'll ask you to visit the office in person.</li>
            <li>
              If it's approved, collect it yourself within 7 days with your student ID card. Nobody else can collect
              it for you.
            </li>
          </ol>
        </section>

        <div className="button-row">
          <button type="submit" className="button" disabled={busy}>
            {busy ? "Sending claim…" : "Send claim"}
          </button>
          <Link href={`/items/${item.id}`}>Cancel</Link>
        </div>
      </form>
    </>
  );
}
