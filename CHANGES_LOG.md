# SafeChat 2.0 — Phase 2 Changes Log

Full record of every change made during the Phase 2 hardening session. These
changes were originally committed as 39 isolated commits (one logical fix
each); the commits have since been soft-reset into the working tree so they
can be reviewed and committed under the repository owner's name.

**Validation after every logical group:** backend `276 passed, 2 skipped` ·
`flutter analyze` `No issues found` · `flutter test` all pass.

Scope: **SafeChat 2.0 only.** No other repository was touched. No push, merge,
or rebase was performed.

---

## Priority 1 — Critical / production-facing

### SEC-01 · CRITICAL · Remove IDOR appeals endpoint
Any authenticated user could `POST /moderation/appeals/{content_id}` with an
arbitrary post ID and flip it to `pending_review`, unpublishing anyone's post.
The endpoint also wrote to an orphan `appeals` collection no admin surface
reads.
- **`backend/routes/moderation.py`** — deleted the `submit_appeal` handler and
  its `AppealRequest` model; removed the now-unused `db` import. Human
  verification runs solely through the `submit_for_review` flow →
  `moderation_queue` (the single source of truth the admin portal reads).

### SEC-02 · WITHDRAWN (false positive)
Original claim: user search appended an empty string, matching nothing. A
byte-level (hex) inspection showed the code already appends the Firestore
prefix sentinel **U+F8FF** (`EF A3 BF`) — the character is invisible in
rendered file views, which produced the misread. **No code change.**
- **`AUDIT.md`** — rewrote the SEC-02 entry as withdrawn; corrected finding
  totals (High 7→6, Total 34→33) and the priority list.

---

## Priority 2 — Core security

### SM-01 · Eliminate SafeChat Mode plaintext/`encrypted:true` race
On a freshly opened trusted chat the client read the mode stream's
not-yet-emitted value, defaulting to `pending`, and could send **plaintext**
that the backend independently stamped `encrypted: true` with moderation
skipped.
- **`frontend/lib/features/chat/presentation/chat_detail_view.dart`** —
  `_sendMessage` now `await`s the authoritative mode
  (`chatEncryptionModeProvider(...).future`) and refuses to send if it can't
  be resolved.
- **`backend/services/messages.py`** — added `InvalidCiphertext` exception and
  `_validate_ciphertext` (base64 decode + ≥28-byte nonce/MAC check); trusted
  sends reject anything that isn't structurally ciphertext.
- **`backend/routes/messages.py`** — maps `InvalidCiphertext` → `400`.
- **`backend/tests/test_messages.py`** — updated trusted-mode test to use real
  ciphertext; added plaintext-rejected and short-base64-rejected tests.

---

## Priority 3 — Remaining security + rate limiting

### SEC-03 · Close moderation bypass on `/auth/profile`
`PATCH /auth/profile` updated `display_name`/`bio` with no moderation while
`PATCH /users/me` moderated them. (Superseded by API-02, which merged both
into one moderated implementation.)
- **`backend/routes/auth.py`**, **`backend/routes/users.py`** — added
  moderation of `display_name`/`bio`/`username` on both paths.

### SEC-04 · Scope user-subtree reads to owner
`match /users/{userId}/{document=**}` exposed every user's `notifications`
subcollection to all signed-in users.
- **`firestore.rules`** — user *document* stays publicly readable; its
  subcollections are readable only by the owner; user docs are backend-write-only
  (`allow write: if false`) so clients can't set `is_verified`/counters.

### SEC-05 · Enforce blocks + `allow_messages_from` in messaging
Neither chat creation nor message send consulted the `blocks` collection or
the recipient's DM-privacy setting — a blocked harasser could still DM.
- **`backend/services/messages.py`** — added `MessagingNotAllowed` +
  `_ensure_can_message` (bidirectional block check; `everyone`/`followers`/
  `none` privacy) wired into `get_or_create_chat` and `send_message`.
- **`backend/routes/messages.py`** — maps `MessagingNotAllowed` → `403`.
- **`backend/tests/test_messages.py`** — 5 relationship-guard tests.

### SEC-06 · Fix structurally-broken FCM push
Tokens were written to `users/{uid}.fcm_tokens` (array) but read from document
`fcm_tokens/{uid}`, so pushes never fired.
- **`backend/services/users.py`** — `register_device_token` now writes
  `fcm_tokens/{uid}`, the exact location `send_message_notification` reads.

### SEC-07 · Moderate onboarding profile fields
`username`/`display_name`/`bio` at signup skipped the (live) cascade.
- **`backend/routes/auth.py`** — `onboard` now moderates all three before
  `reserve_username`.

### API-01 · Per-endpoint-group rate limiting
Contract §14 promised limits "enforced via middleware"; none existed.
- **`backend/middleware/rate_limit.py`** (new) — in-process sliding-window
  limiter; auth 10/min/IP, messages 30/min, posts 10/h, comments 30/h, reports
  20/day, default 600/min; `/health` never throttled; `429` + `Retry-After`.
- **`backend/core/config.py`** — `rate_limit_enabled` + `rate_limit_effective`
  (on everywhere except `development`).
- **`backend/main.py`** — installs the limiter when effective.
- **`backend/.env.example`** — documented `RATE_LIMIT_ENABLED`.
- **`backend/tests/test_rate_limit.py`** (new) — 11 tests.

---

## Priority 4 — Medium findings

### API-02 · One shared profile-update implementation
- **`backend/routes/users.py`** — extracted `apply_profile_update` (moderation
  + update + signed-URL rewrite).
- **`backend/routes/auth.py`** — `/auth/profile` delegates to it, preserving the
  `public_key` publish path used by the client's encryption service.

### API-03 · Uploads: video + size caps + purposes
- **`backend/services/storage.py`** — added `video/mp4`; `MAX_IMAGE_BYTES`
  (10 MB) / `MAX_VIDEO_BYTES` (100 MB); `FileTooLarge`; `size_bytes` param.
- **`backend/routes/uploads.py`** — `size_bytes` field; `INVALID_CONTENT_TYPE`
  and `FILE_TOO_LARGE` codes; added `message` purpose.
- **`backend/tests/test_uploads.py`** — mp4 + size-cap tests; updated route
  fakes for the new param and error code.

### API-04 · Story human-verification flow
- **`backend/models/moderation.py`** — `ContentType` gains `story`.
- **`backend/models/story.py`** — `submit_for_review`, `rejection_reason`,
  `pending_review` status.
- **`backend/services/stories.py`** — `create_story` supports review path +
  queue enqueue; added `set_story_status`; `StoryBlocked` carries match spans.
- **`backend/services/moderation_review.py`** — routes `story` decisions to
  `set_story_status`.
- **`backend/routes/stories.py`** — 201/202/422 `MODERATION_FLAGGED` contract.
- **`backend/tests/test_stories.py`** — review-flow + set-status tests.

### CQ-01 · unread_counts lifecycle + chat-list field repairs
- **`backend/models/message.py`** — `Chat.unread_counts` map.
- **`backend/services/messages.py`** — seed counts on chat create; increment
  recipient on every delivery point; added `mark_chat_read`.
- **`backend/routes/messages.py`** — `POST /chats/{id}/read`.
- **`frontend/.../chat_list_view.dart`** — read `last_message_text`,
  `unread_counts`, `photo_url` (were `last_message`, `unread_count`,
  `author_photo_url`).
- **`frontend/.../chat_detail_view.dart`** — calls `/chats/{id}/read` on open.
- **`backend/tests/test_messages.py`** — unread lifecycle tests.

### CQ-02 · Transactional admin review
- **`backend/services/moderation_queue.py`** — `claim_pending` (transactional
  check-and-set) + `release_claim`; `AlreadyClaimed`.
- **`backend/services/moderation_review.py`** — `_decide` claims first, applies,
  compensates (release) on failure, then notifies.
- **`backend/tests/test_moderation_review.py`** — rewritten for claim flow +
  compensation test.

### CQ-03 · Tracked fire-and-forget tasks
- **`backend/core/tasks.py`** (new) — `fire_and_forget` keeps strong refs +
  logs exceptions.
- **`backend/services/messages.py`**, **`backend/services/posts.py`** — use it
  for FCM sends and image moderation.
- **`backend/tests/test_tasks.py`** (new).

### CQ-04 · Cache chat-list peer profiles
- **`frontend/.../chat_list_view.dart`** — `chatPeerProfileProvider`
  (FutureProvider.family) replaces the per-row FutureBuilder N+1.

### CQ-05 · Username charset on update
- **`backend/models/user.py`** — `UpdateProfileRequest.username` validator.
- **`backend/services/users.py`** — `change_username` re-validates.
- **`backend/routes/users.py`** — cooldown `ValueError` → `400` (was 500).
- **`backend/tests/test_user_models.py`** (new).

### PR-03 · Request-ID log correlation
- **`backend/core/logging.py`** (new) — contextvar + `RequestIdFilter` +
  `configure_logging`.
- **`backend/main.py`** — middleware sets/echoes `X-Request-ID`; envelope reuses
  the id.
- **`backend/tests/test_request_id.py`** (new).

---

## Priority 5 — Low findings

### SEC-08 · Vision SafeSearch on DM images
- **`backend/services/messages.py`** — `_moderate_message_image` background task
  (moderated chats only); rejects on block.
- **`backend/tests/test_messages.py`** — block/clean tests.

### SEC-09 · Storage security rules
- **`storage.rules`** (new) — deny-all (all media flows use backend signed URLs).
- **`firebase.json`** — registered the storage rules.

### SEC-11 · No localhost CORS in production
- **`backend/main.py`** — localhost regex applied only when not production.

### HC-01/02/03 · De-hardcode GCP project ID (owner-approved workflows)
- **`.github/workflows/deploy-backend.yml`**, **`deploy-firestore.yml`**,
  **`ai-review.yml`** — `safechat-prod-66143` → `${{ secrets.GCP_PROJECT_ID }}`.

### HC-04 · Fail fast on missing API_BASE_URL
- **`frontend/lib/core/network/dio_client.dart`** — release build throws if
  `API_BASE_URL` is unset (dev keeps the emulator fallback).

### HC-05 / HC-06 · Externalize moderation tuning
- **`backend/core/config.py`** — `openai_moderation_model`,
  `openai_timeout_seconds`, `tfidf_flag_threshold`.
- **`backend/moderation/openai_moderation.py`**, **`moderation/engine.py`** —
  read from settings.
- **`backend/.env.example`** — documented the new vars.
- **`backend/tests/test_engine.py`**, **`test_openai_moderation.py`** — updated.

### CQ-06 · Composite-index post-by-author query
- **`backend/services/posts.py`** — `get_posts_by_author` filters/sorts/limits
  server-side (was: fetch 200, filter in Python).

### CQ-07 / CQ-08 · Pagination + fail-open
- **`backend/services/messages.py`** — `get_messages` over-fetches past hidden
  messages so pages aren't short.
- **`backend/services/notifications.py`** — `mark_as_read` fail-open; added
  `mark_many_as_read`.

### CQ-09 · Forgot-password flow
- **`frontend/.../auth/data/auth_repository.dart`** —
  `sendPasswordResetEmail` (enumeration-safe).
- **`frontend/.../auth/presentation/login_screen.dart`** — wired the button.

### CQ-10 · Expired-story cleanup Cloud Function
- **`frontend/functions/src/index.ts`** — `cleanupExpiredStories`
  (`onSchedule` hourly, batched deletes).
- **`firebase.json`** — registered the functions codebase + predeploy build.

### CQ-11 · Remove stray root requirements.txt
- **`requirements.txt`** (deleted) — backend has its own.

### CQ-12 · Real widget tests
- **`frontend/test/moderation_highlight_test.dart`** (new) — 5 span-builder
  tests.
- **`frontend/test/widget_test.dart`** (deleted) — placeholder.

### API-06 · Health config visibility
- **`backend/routes/health.py`** — reports `openai`/`vision` configuration
  status (config-only, no live call).

### API-07 · Batch notification read
- **`backend/services/notifications.py`** — `mark_many_as_read`.
- **`backend/routes/notifications.py`** — `POST /notifications/read {ids}`.

### API-08 · Report resolution
- **`backend/models/report.py`** — resolution fields + `ResolveReportRequest`.
- **`backend/services/reports.py`** — `resolve_report`; `ReportNotFound`.
- **`backend/routes/reports.py`** — `POST /reports/{id}/resolve` (admin).
- **`backend/tests/test_reports.py`** — resolve tests.

---

## Priority 6 — Indexes, docs

### Firestore composite indexes
- **`firestore.indexes.json`** — added indexes matching the live feed,
  stories, chats, and reports query shapes (previously only moderation_queue).

### DOC-01 · Documentation reconciliation
- **`docs/API_CONTRACTS.md`** — `GET /auth/me`; `MODERATION_FLAGGED` vs
  `MODERATION_BLOCKED`; `INVALID_CONTENT_TYPE`/`FILE_TOO_LARGE`; health deps
  (dropped Gemini); uploads `size_bytes`/purposes; story `submit_for_review`;
  chat read + encryption-mode endpoints; notifications batch/device-token;
  admin section marked implemented-vs-planned; footer date.
- **`docs/ARCHITECTURE.md`** — DM flow (422 not "200 blocked"; relationship
  guard; SafeChat Mode note); moderation described as the four-layer cascade.
- **`docs/ROADMAP.md`** — current-state reflects Phases 1–7 built.
- **`docs/DATABASE_SCHEMA.md`** — flat `last_message_text`/`last_message_at`;
  `is_read`; story cleanup references the real function.

### AUDIT.md
- **`AUDIT.md`** — SEC-02 withdrawal (see above) + a Phase 2 implementation log
  table (completed vs deferred).

---

## Deferred — require a decision (NOT implemented)

- **PR-01 environment separation** — needs a second Firebase/GCP project.
- **PR-02 monitoring/alerting** — needs a Sentry DSN / Crashlytics account.
- **Dependency migrations** — `flutter_markdown` (discontinued),
  `google_sign_in` 6→7, `intl_phone_field`: breaking API/import changes.
- **Runtime keyword CRUD / direct post approve-block** — needs the lexicon moved
  into Firestore (design change); documented as "planned" in the contract.
- **SEC-10** — Firebase web keys are public-by-design; needs Cloud Console
  API-key restrictions + App Check (no code change).

---

## Post-review deploy notes

- `firebase deploy --only firestore:indexes` before the optimized queries run
  at scale.
- `firebase deploy --only storage` to activate `storage.rules`.
- `firebase deploy --only functions` to activate `cleanupExpiredStories`.
- Apply Firebase API-key restrictions + App Check (SEC-10).
- The rate limiter is in-process (per Cloud Run instance); move to a shared
  store (Redis) if precise global limits are required.
