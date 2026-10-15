"use client";

/**
 * Sign-in page, in two steps on the same page:
 *   step "email": enter your email -> POST /auth/request-code
 *     (students: @iitk.ac.in; guards and the admin may also use @gmail.com,
 *      once the admin has added them; the backend enforces this)
 *   step "code":  enter the 6-digit code         -> POST /auth/verify-code
 * After a correct code, the backend sets the session cookie, and we send
 * the user to onboarding (first time) or the home page.
 *
 * "Student" / "Admin / Guard" at the top only changes the wording and adds
 * one check: someone who chose Admin / Guard but whose account is a student
 * account is signed straight out again. The ROLE ITSELF NEVER COMES FROM
 * THIS CHOICE: it's stored in the database, and only the admin can make
 * someone a guard. Staff who choose "Student" are let in (they can do
 * everything a student can).
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import AuthLayout from "@/components/AuthLayout";
import { useAuth } from "@/components/AuthProvider";
import { apiFetch } from "@/lib/api";
import styles from "./login.module.css";

const DOMAIN = "iitk.ac.in";

const MODES = {
  student: {
    label: "Student",
    intro: "We'll email a 6-digit code to your IITK address. No password needed.",
    placeholder: `yourid@${DOMAIN}`,
  },
  staff: {
    label: "Admin / Guard",
    intro: "For campus security staff. Use the @gmail.com or @iitk.ac.in email the admin added as a guard.",
    placeholder: "yourname@gmail.com",
  },
};

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, setUser } = useAuth();

  const [mode, setMode] = useState("student"); // "student" | "staff"
  const [step, setStep] = useState("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [expiresIn, setExpiresIn] = useState(10);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Already signed in? Skip this page.
  useEffect(() => {
    if (!loading && user) router.replace(user.is_onboarded ? "/" : "/onboarding");
  }, [user, loading, router]);

  async function requestCode(event) {
    event?.preventDefault(); // stop the browser reloading the page on submit
    setError("");
    setBusy(true);
    try {
      const result = await apiFetch("/auth/request-code", {
        method: "POST",
        body: { email: email.trim() },
      });
      setExpiresIn(result.expires_in_minutes);
      setCode("");
      setStep("code");
    } catch (err) {
      setError(err.fieldErrors?.email ? "Enter a valid email address." : err.message);
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const signedIn = await apiFetch("/auth/verify-code", {
        method: "POST",
        body: { email: email.trim(), code },
      });
      if (mode === "staff" && signedIn.role === "STUDENT") {
        // Chose Admin / Guard, but this is a student account: sign out again.
        await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
        setStep("email");
        setCode("");
        setError(
          "This email isn't a security staff account. Students choose \u201cStudent\u201d; guards need the admin to add their email first.",
        );
        setBusy(false);
        return;
      }
      setUser(signedIn); // the redirect effect above takes it from here
    } catch (err) {
      setError(err.fieldErrors?.code ? "Enter the 6 digits from the email." : err.message);
      setBusy(false);
    }
  }

  function chooseMode(next) {
    setMode(next);
    setError("");
  }

  function changeEmail() {
    setStep("email");
    setError("");
  }

  return (
    <AuthLayout>
      <div className={styles.card}>
        {step === "email" ? (
          <form onSubmit={requestCode} noValidate>
            <span className="eyebrow">Welcome</span>
            <h1 className={styles.heading}>Sign in</h1>

            {/* Two options, shown as a switch. Radio buttons underneath,
                so keyboards and screen readers handle them correctly. */}
            <fieldset className={styles.modes}>
              <legend className="visually-hidden">Sign in as</legend>
              {Object.entries(MODES).map(([key, option]) => (
                <label key={key} className={styles.mode}>
                  <input
                    type="radio"
                    name="mode"
                    value={key}
                    checked={mode === key}
                    onChange={() => chooseMode(key)}
                  />
                  {option.label}
                </label>
              ))}
            </fieldset>

            <p className={styles.lede}>{MODES[mode].intro}</p>

            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}

            <div className="field">
              <label htmlFor="email">Email address <span className="req">*</span></label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                placeholder={MODES[mode].placeholder}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
            </div>
            <button type="submit" className="button button-full" disabled={busy || !email.trim()}>
              {busy ? "Sending code…" : "Send me a code"}
            </button>
          </form>
        ) : (
          <form onSubmit={verifyCode} noValidate>
            <span className="eyebrow">Check your email · {MODES[mode].label}</span>
            <h1 className={styles.heading}>Enter your code</h1>
            <p className={styles.lede}>
              We sent a 6-digit code to <strong>{email.trim().toLowerCase()}</strong>. It works once
              and expires in {expiresIn} minutes.
            </p>

            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}

            <div className="field">
              <label htmlFor="code">6-digit code <span className="req">*</span></label>
              <input
                id="code"
                className={styles.code}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                // Keep only digits, so pasting "482 913" still works.
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                autoFocus
              />
            </div>
            <button type="submit" className="button button-full" disabled={busy || code.length !== 6}>
              {busy ? "Checking…" : "Sign in"}
            </button>

            <div className={styles.actions}>
              <button type="button" className="link-button" onClick={changeEmail}>
                Use a different email
              </button>
              <button type="button" className="link-button" onClick={() => requestCode()} disabled={busy}>
                Send a new code
              </button>
            </div>

            {process.env.NODE_ENV === "development" && (
              <p className={styles.devHint}>
                Development mode: the code is printed in the terminal running the backend.
              </p>
            )}
          </form>
        )}
      </div>
    </AuthLayout>
  );
}
