# Plugging in AI matching ("Similar items found")

The app already has the **Similar items found** section, the API endpoint,
and the safety rules. What's missing is the part that decides which items
are similar. That part is yours, and it lives in a class you write.

Right now the app uses `PlaceholderMatcher`, which suggests nothing. The
item page then says "Automatic matching isn't switched on yet".

## What already exists (don't rebuild these)

| Piece | Where |
|---|---|
| The contract (`Match`, `Matcher`) and the placeholder | `app/services/matching.py` |
| Endpoint `GET /items/{id}/similar` | `app/routers/items.py` |
| Item page section | `frontend/app/items/[id]/page.js` (`SimilarItems`) |
| Tests that any matcher must pass | `tests/test_similar.py` |

## What you write

A class with these three methods and one attribute:

```python
class MyMatcher:
    enabled = True

    def find_similar(self, db, item, limit) -> list[Match]:
        """Up to `limit` items of the OPPOSITE kind, best first."""

    def item_saved(self, db, item) -> None:
        """Called after an item is created, received at an office, or gets a
        new photo. Compute and store what you need (e.g. an embedding)."""

    def item_removed(self, item_id) -> None:
        """Called before an item is deleted for good. Delete what you stored."""
```

`Match(item_id, score, reason=None)`:
- `score` is from 0.0 (unrelated) to 1.0 (certainly the same item).
- `reason` is optional short text shown on the page, e.g. "Same category, similar colour".

Then switch it on:

1. Register it in `MATCHERS` in `app/services/matching.py`:
   ```python
   MATCHERS = {
       "placeholder": PlaceholderMatcher,
       "pgvector": PgVectorMatcher,
   }
   ```
2. In `backend/.env`, set `MATCHING_BACKEND=pgvector`.
3. Restart the API.

## Rules your matcher must follow

1. **Don't change the app's tables** in `find_similar`. Store your own data
   (embeddings) in your own table, with its own Alembic migration.
2. **Be quick.** The item page waits for `find_similar`. Aim for under a second.
3. **Expect failure to be handled.** If your matcher raises an exception,
   the app logs it and shows no suggestions. Reporting an item still works.

## What the app does for you (you don't need to)

`similar_items()` in `matching.py` filters whatever you return. It:
- keeps only items of the opposite kind (lost ↔ found)
- keeps only items on the public pages (a found item at an office, or an
  open lost report). Items waiting for drop-off, returned or closed are dropped.
- removes the item itself, unknown ids and duplicates
- clamps scores to 0..1, sorts best first, and cuts to the limit (6)

So a bug in your ranking can show a poor suggestion, but never a private one.

## Who sees suggestions

The person who reported the item, and security staff, while the item is
still open: a lost report that's still missing, or a found item not yet
returned. Other students don't see them.

## Fields you can use

`title`, `description`, `category` (`category.slug`), `colour`, `brand`,
`location_text`, `happened_on`, `kind`, and `photos` (each has a
`stored_name`; read the file with `app.services.storage.path_for(name)`).

## Testing your matcher

`tests/test_similar.py` uses a fake matcher to check the safety rules. Add
your own tests for your ranking, and run everything with `pytest`.
