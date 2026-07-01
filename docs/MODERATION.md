# SafeChat Moderation Engine (V1)

This document describes the moderation pipeline that screens user-generated text
(posts, comments, DMs) **before** it reaches other users, and the
human-verification + admin-review flow that surrounds it.

**V1 status:** the active layers are the **weighted keyword lexicon** (Layer 1)
and a **trained TF-IDF classifier** (Layer 2). The OpenAI and Vision layers are
scaffolded but only run when their API keys / flags are configured.

All moderation runs **server-side** (FastAPI). Nothing — no model, no keyword
list — ships to the client (`AGENT.md` rule #1: all writes go through the
backend so moderation always runs).

**Exception — SafeChat Mode (`chats/{chatId}.encryption_mode == "trusted"`).**
When two mutual followers turn SafeChat Mode OFF for their chat, messages are
encrypted client-side before they ever reach the backend, and
`services/messages.py::send_message` skips the cascade entirely for that
chat — the server only ever sees ciphertext. This is deliberate: it's the
only surface in the app where AI moderation is intentionally bypassed.
Non-mutual-follow chats can never enter this mode. See
`docs/DATABASE_SCHEMA.md` section 10 for the full model, the mutual-follow
gate, and the unfollow-reverts-trust rule.

The client-side encryption itself is simple static-key X25519 (via the
`cryptography` Dart package), **not** Signal Protocol / Double Ratchet — it
has **no forward secrecy**: a compromised private key exposes all past
messages encrypted to it. Upgrading to a proper ratchet is future work.

---

## The cascade (`moderation/engine.py`)

Every user string flows through `moderate_text(text) -> ModerationResult`:

1. **Empty/whitespace short-circuit** — no work, no API calls.
2. **Layer 1 — Weighted lexicon (`moderation/lexicon.py`)**
   - A curated lexicon of slurs/threats; each term has a severity weight.
   - Matching runs against the **original** text with an obfuscation-tolerant
     regex per term, tolerating case, leet-speak (`1d10t`, `b@d`), repeated
     characters (`idioooot`), and separators (`i.d.i.o.t`).
   - Returns exact character **spans** of every hit (`matches[]`) so the client
     can highlight the offending words. Reported as `layer="keyword"`.
3. **Layer 2 — TF-IDF classifier (`moderation/tfidf_model.py`)**
   - A scikit-learn `TfidfVectorizer -> LogisticRegression` pipeline trained by
     `moderation/train_model.py` on `moderation/data/seed_corpus.csv`, loaded
     once into RAM from `moderation/data/model.pkl`.
   - Flags text whose toxicity probability ≥ `TFIDF_FLAG_THRESHOLD` even when no
     keyword fired (novel/contextual phrasing). Reported as `layer="tfidf"`.
   - **Fail-open:** if the model artifact or scikit-learn is absent, it returns
     "no opinion" and the cascade falls through.
4. **Layer 3 — OpenAI Moderation API (upcoming)** — runs only if a key is set.

First block wins; downstream layers are skipped. Per-layer latency is recorded.

### Retraining Layer 2
```
cd backend
python -m moderation.train_model   # rebuilds moderation/data/model.pkl
```
Edit `seed_corpus.csv` (columns `text,label`; label 1 = toxic, 0 = clean) and
re-run. Commit the regenerated `model.pkl`.

### Editing the lexicon
Edit `_LEXICON` in `moderation/lexicon.py` and redeploy. No I/O — the patterns
compile once at import.

---

## Content lifecycle: flag → human verification → admin review

Outcomes are uniform across **posts, comments, and DMs**:

```
compose ──▶ POST (moderate server-side)
  ├─ clean ─────────────▶ status=approved ──▶ visible to its audience
  └─ flagged ─▶ 422 MODERATION_FLAGGED { matches:[{term,start,end,category}] }
                 │
          POPUP: "This can't be uploaded" + highlighted words
                 ├─ [Edit]                     → back to the composer
                 └─ [Submit for human review]  → re-POST submit_for_review=true
                        ▼
                 status=pending_review  +  moderation_queue record
                   (author tracks it in Profile ▸ Appeals)
                        ▼
                 ADMIN PORTAL: Approve / Reject(reason)
                   ├─ approve → status=approved → published + author notified 🎉
                   └─ reject  → status=rejected + reason → hidden + author notified
```

- **Statuses:** `approved | pending_review | rejected`.
- **Audience on approve:** post → global feed; comment → onto the post; DM →
  delivered to the recipient (+ FCM).
- **Counters** (`post_count`, `comment_count`) reflect **approved** content only
  — incremented on approve, never for pending/rejected.
- **The review queue** (`moderation_queue`) is the single source of truth for
  content under/after human review. Admins read all of it; an author reads only
  their own items. See `docs/DATABASE_SCHEMA.md`.
- **Notifications:** the author gets an in-app `appeal_update` notification on
  every decision (rejections include the reason).

### Admin access
Admins carry the Firebase `admin` custom claim (enforced by
`middleware.auth.require_admin`). Grant it with:
```
cd backend
python -m scripts.set_admin <uid>        # or --email user@example.com
python -m scripts.set_admin <uid> --revoke
```
The Flutter app shows the **Moderation Queue** entry (Profile ▸ Settings) only
to admins, backed by `/admin/moderation/queue`.

---

## Security rules (`firestore.rules`)
Reads are deny-by-default with explicit per-collection grants. `moderation_queue`
is readable only by admins or the content's author; `moderation_logs` is never
client-readable. All writes go through the backend.

> **Future hardening:** chat reads are currently scoped to any authenticated
> user (matching the app's existing behavior); tightening message reads to chat
> participants is a follow-up.

---

## Developer guidelines
**Golden rule: never bypass moderation.** When adding a backend feature that
accepts user text:
1. Route the text through `engine.moderate_text(text)`.
2. On `result.blocked`, either raise the content's `*Blocked` exception (the
   route returns `422 MODERATION_FLAGGED` with `matches`) or, when
   `submit_for_review` is set, persist as `pending_review` and enqueue via
   `services/moderation_queue.build_item(...)`.
3. **Never** write user text to Firestore directly from the Flutter client.
