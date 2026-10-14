# AI in the Lost & Found portal

The AI works **in the background**: saving a report never waits for it.
Results appear on the item page a few seconds later.

## Phases

| Phase | What it does | Status |
|---|---|---|
| A | Local embeddings + top-K "Possible matches" on every report, both directions | **Done** |
| B | Agent (Groq → Gemini fallback): smart search filters, re-ranking with reasons, match emails | **Done** |
| C | Gemini photo descriptions ("AI description") | **Done** |
| D | Found ID cards: local OCR reads the roll number / email → owner emailed | **Done** |
| E | AI claim review for guards (text only) | Planned |
| F | Natural-language search | Planned |

## How Phase A works

```
report saved ──► SemanticMatcher.item_saved() ──► ai_jobs row (status "queued"), item.ai_status = "pending"
                                                         │
             background runner (inside the API, every 3 s; or python -m app.worker)
                                                         ▼
     semantic.analyse_item():  text ─► embedding (384 numbers) ─► compare with active items of the
     opposite kind (SQL pre-filter + numpy cosine) ─► best K above MATCH_MIN_SCORE ─► match_suggestions
                                                         ▼
     item page: GET /items/{id}/similar ─► {"status": "pending" | "ready" | "failed", "matches": [...]}
     The page shows "Looking for similar items…" and asks again every 3 s until ready.
```

Score = 70% meaning of the text (cosine similarity) + 20% same category + 10% dates close.
A found item dated more than 3 days *before* the loss is never suggested.

Every suggestion still passes the safety rules in `services/matching.py`:
only the opposite kind, only items on the public pages (a found item appears
only after a guard has received it), never the item itself.

## How Phase B works: the Recovery Agent

**When the agent runs** is set by `AGENT_MATCHING` in `.env` (the maths always runs first):

| Mode | LLM calls | Use |
|---|---|---|
| `off` | never | rule-based matching only |
| `ambiguous` (default) | only when the maths isn't sure: the best two scores are within 0.10, or the best score is below 0.65 | everyday use; most reports cost no LLM call |
| `always` | every report | demos |

Without the agent, a very clear match (rule score >= 0.8, found item at an office) still emails
the owner, once.


After the embedding step, `agent.match_with_agent()` runs the agent (if an LLM key is set):

```
LLM (Groq, or Gemini if Groq fails) gets: the report (no personal data) + category list + tools
  1. search_candidates({categories, colours, keywords, date_from, date_to})
        our code: safe base query (opposite kind, active, sane dates) → agent's filters →
        rank by embeddings + keyword/colour boost → up to 12 short summaries
     (the agent may search again with wider filters)
  2. save_matches([{id, score, reason}])   only ids it was shown; top K kept, method = "ai"
  3. notify_lost_owner(lost, found)        only score >= 0.7, item at an office, once per pair
  4. finish(summary)
```

- Guardrails are in our code, not the prompt: unknown ids rejected, scores clamped, at most 6 LLM
  calls per run, `AI_DAILY_CALL_LIMIT` per day, no personal data in any message, no tool that can
  change an item or a claim.
- Fallback: no key, rate limit, network error, daily limit, or the model not saving anything →
  the Phase A rule-based matching runs instead. The page always gets a list.
- Audit log: every run is a row in `agent_runs` (provider, model, LLM calls, each tool call with
  its arguments and result, outcome `ok` / `fallback`, error).
- Matches ranked by the agent show an **AI** badge on the item page.
- Dates: the app computes `days_found_after_lost` and writes the date part of every reason
  itself; any date wording from the model is removed (models get date arithmetic wrong).
- The report text is user-written: the prompt tells the model to treat it as data and ignore
  any instructions in it, and every tool is checked in code anyway.

Check your keys: `python -m app.cli ai-check` (one tiny test call per provider).

## How Phase C works: photo descriptions

Before embedding, `vision.describe_photos()` runs when the item's photos changed:

- Up to 3 photos, shrunk to 768 px JPEG, plus the report text (no personal data) go to Gemini.
- Gemini answers JSON: object, colour, brand, marks, description, contains_personal_info.
- The description is shown on the item page ("AI description, from the photos") and added to the
  text that is embedded, so matching sees what's in the photo (e.g. a visible brand).
- Never sent: photos in `AI_SKIP_PHOTO_CATEGORIES` (default `id-cards-documents`).
- Thrown away: answers where the model says a photo shows personal information.
- Once per photo set (`items.ai_photos_key`): text edits don't call the AI again.
- Logged in `agent_runs` (tool `describe_photos`), counted in the daily limit.
- Switch off with `AI_PHOTO_DESCRIPTIONS=false`.

## How Phase D works: ID card → owner email

`idcard.py`, run during the same background analysis:

1. **Which items**: only FOUND items in `ID_CARD_CATEGORIES` (default "ID Cards & Documents")
   that have photos. Lost reports and every other category are never read.
2. **Read once**: each set of photos is read once (`items.ocr_photos_key`). The image is read
   again only if a photo is added or removed.
3. **OCR on our own server**: RapidOCR (`rapidocr_onnxruntime`, bundled ONNX models, ~14 MB,
   no PyTorch, ~1-2 s per photo on a CPU). This is the ONLY thing that reads the card: these
   photos are never sent to Gemini (Phase C skips the category), and no LLM sees the roll
   number or email. The agent's job stays matching.
4. **Detection is plain code** (regular expressions):
   - a roll number only next to a "Roll" label (`Roll No: 220345`, `RollNo:22O345` with OCR's
     O→0, l→1 fixed; 6-9 digits). PIN codes, phone numbers and years are ignored.
   - email addresses only on `OWNER_EMAIL_DOMAINS` (default `iitk.ac.in`, subdomains allowed).
   - names are ignored (not unique).
5. **Who is emailed** (first rule that gives anyone wins, so one card = one email per person):
   1. a registered user with that roll number or email → their account email;
   2. an email printed on the card → that email;
   3. only a roll number → `{roll}@iitk.ac.in` (`ROLL_EMAIL_PATTERN`).
   Never the finder. Stored in `owner_alerts`.
6. **When**: only once a guard holds the item at an office (receiving re-runs the analysis).
   Once per person. The email masks the roll number ("roll no. ending 45").
7. If the owner has an **open lost report** for it, the two are linked with score 1.0
   (`method = "idcard"`, shown as an "ID match" badge), and they don't also get the general
   "a found item may be yours" email.
8. **Privacy**: photos in `ID_CARD_CATEGORIES` are shown only to the reporter and staff (hidden
   in lists, on the item page and at the photo URL). Guards see masked alerts in the Staff panel.

## Files

| File | Purpose |
|---|---|
| `app/services/ai/embeddings.py` | Local model (fastembed, `BAAI/bge-small-en-v1.5`, ~70 MB ONNX) with a tiny built-in fallback ("hashing") |
| `app/services/ai/queue.py` | The `ai_jobs` queue (FOR UPDATE SKIP LOCKED, safe with several runners) |
| `app/services/ai/semantic.py` | Matching and the `SemanticMatcher` plugged into `services/matching.py` |
| `app/services/ai/runner.py` | Background loop started by the API |
| `app/services/ai/llm.py` | LLM client: Groq → Gemini, daily call limit |
| `app/services/ai/agent.py` | The Recovery Agent: prompt, tools, loop, guardrails, audit log |
| `app/services/ai/vision.py` | Phase C: Gemini photo descriptions |
| `app/services/ai/idcard.py` | Phase D: OCR, roll number / email detection, owner alerts and emails |
| `app/models/ai.py` | Tables `ai_jobs`, `match_suggestions`, `agent_runs`, `match_notifications`, `owner_alerts` |
| `frontend/components/SimilarItems.js` | The "Possible matches" panel |
| `tests/test_ai_matching.py` | Phase A tests (hashing embedder: no download needed) |
| `tests/test_ai_agent.py` | Phase B tests with a fake LLM (no API calls, no quota used) |
| `tests/test_ai_vision.py` | Phase C tests with a fake vision model |
| `tests/test_ai_idcard.py` | Phase D tests with a fake OCR |

## Settings (`backend/.env`, all optional)

| Setting | Default | Meaning |
|---|---|---|
| `MATCHING_BACKEND` | `semantic` | `placeholder` switches matching off |
| `MATCH_TOP_K` | `5` | Suggestions per report |
| `MATCH_MIN_SCORE` | `0.30` | Lowest score shown |
| `EMBEDDING_BACKEND` | `local` | `hashing` = no model download (weaker matching) |
| `AI_WORKER_IN_API` | `true` | Run the queue inside the API process |
| `AI_POLL_SECONDS` | `3` | How often the queue is checked |
| `LLM_API_KEY` | — | Groq key (the agent's main model) |
| `LLM_MODEL` | `openai/gpt-oss-20b` | Groq model with tool calling |
| `GEMINI_API_KEY` | — | Gemini key (fallback model; photo descriptions in Phase C) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Use a free Flash model listed in AI Studio |
| `AI_DAILY_CALL_LIMIT` | `500` | Max LLM calls per day (stays inside free quotas) |
| `AI_ENABLED` | `true` | `false` = no AI API calls at all (rules only) |
| `AGENT_MATCHING` | `ambiguous` | `off` / `ambiguous` / `always`: when the agent ranks matches |
| `AI_PHOTO_DESCRIPTIONS` | `true` | Phase C on/off |
| `AI_SKIP_PHOTO_CATEGORIES` | `id-cards-documents` | Category slugs whose photos are never sent to an AI |
| `ID_CARD_OCR` | `true` | Phase D on/off |
| `ID_CARD_CATEGORIES` | `id-cards-documents` | Always read; photos visible only to reporter and staff |
| `OWNER_EMAIL_DOMAINS` | `iitk.ac.in` | Emails read on a card are used only on these domains |
| `ROLL_EMAIL_PATTERN` | `{roll}@iitk.ac.in` | Where an unregistered roll number is emailed (empty = nowhere) |

## Commands

```
python -m app.cli ai-reindex     queue every active item (after setup, or after changing the model)
python -m app.worker --once      run the queue once and stop
python -m app.cli ai-check       test the Groq / Gemini keys
python -m app.cli ai-run 42      run the agent on item 42 and print every step (testing)
python -m app.cli ocr-check x.jpg read an ID card photo offline and show who would be emailed
python -m app.cli item-check FND-0735   what the AI did with an item, and why an email was / wasn't sent
python -m app.cli email-log             the last 20 emails: when, sent / printed / failed, to whom, subject
python -m app.cli email-log --ref FND-0735   only emails about one item
```

The first time the API starts, the embedding model is downloaded into
`backend/.cache/models/` (needs internet once). If that fails, matching
still works with the built-in fallback and a warning is logged.
