# SafeChat 2.0 — Full Repository Audit (Phase 1)

**Date:** 2026-07-08
**Scope:** `SafeChat-2.0` repository only (`github.com/ADNAN-ZEYA/SafeChat-2.0`). No other project was inspected or referenced.
**Mode:** Read-only audit. No code changes were made. Phase 2 (implementation) awaits explicit approval.
**Working tree at audit time:** clean (HEAD `4f8ee8c` — "Add governance automation system").
**Git-history secret scan:** no service-account keys, `.env` files, or `google-services.json` were ever committed on any branch.

---

## 1. Executive Summary

SafeChat 2.0 is an AI-moderated, teen-safety-focused social platform (Flutter + FastAPI on Cloud Run + Firebase) with a conditional-E2EE messaging feature ("SafeChat Mode"). The architecture is fundamentally sound: disciplined route/service layering, Firestore transactions where consistency matters, a fail-open four-layer moderation cascade, deny-by-default security rules, a private storage bucket with signed URLs, and an exemplary multi-stage non-root Dockerfile. **SafeChat Mode (E2EE) is fully implemented end-to-end — UI toggle, backend gate, key management, and real runtime behavior change** (§6).

However, the audit found **1 Critical and 7 High** findings that block safe production deployment. The most severe: any authenticated user can unpublish any other user's post via an unauthorized status write (IDOR); user search is 100% broken by a one-character bug; a duplicate profile-update route bypasses content moderation entirely; blocked users can still DM the people who blocked them; and push notifications are structurally non-functional because the FCM token is written to one Firestore location and read from another.

### Finding totals

| Severity | Count |
|---|---:|
| **Critical** | **1** |
| **High** | **7** |
| **Medium** | **13** |
| **Low** | **13** |
| **Total** | **34** |

### Highest-priority fixes (in order)

1. SEC-01 — Appeals IDOR (any user can hide any post)
2. SEC-02 — Broken user search (`+ ""` instead of `+ ""`)
3. SEC-03 — `PATCH /auth/profile` moderation bypass
4. SEC-05 — Blocks not enforced in messaging (harassment vector in an anti-bullying app)
5. SEC-06 — FCM push notifications non-functional (write/read path mismatch)
6. SEC-04 — Firestore rule exposes every user's notifications to all users

### Architectural recommendations

- Consolidate the duplicate profile-update routes (`/auth/profile` + `/users/me`) into one moderated handler.
- Route all appeal flows through `moderation_queue` (single source of truth) and delete the orphan `appeals` collection path.
- Introduce a `relationship guard` helper (blocks + `allow_messages_from`) consumed by chat/DM endpoints.
- Add environment separation (staging Firebase project) before public launch.

### Estimated implementation effort

| Bucket | Effort |
|---|---|
| Critical + High (8 findings) | ~2–3 focused days |
| Medium (13 findings) | ~4–6 days |
| Low (13 findings) | ~2–3 days |
| **Total to production-ready** | **~2 sprint weeks** |

---

## 2. Security Audit

### SEC-01 · CRITICAL · IDOR — any user can unpublish any post
- **File:** `backend/routes/moderation.py`, `submit_appeal`, lines 82–125
- **Issue:** `POST /moderation/appeals/{content_id}` takes an arbitrary `content_id` and executes `db.collection("posts").document(content_id).update({"status": "pending_review"})` (line 109) with **no ownership check**. Feeds only show `status == "approved"` (`services/posts.py:264,279`), so this instantly hides the post from everyone.
- **Impact:** Trivial denial-of-content attack against any user. Also writes to an `appeals` collection nothing reads (admin portal reads `moderation_queue`), so real appeals vanish. The comment/`pass` branches (lines 111–118) are dev remnants.
- **Fix:** Verify `claims["uid"] == post.author_uid` before any status change; route appeals through `moderation_queue`; remove dead branches.

### SEC-02 · HIGH · User search permanently returns zero results
- **File:** `backend/services/users.py`, `search_users`, line 264
- **Issue:** ``end = normalised + ""`` (empty string appended) — upper bound equals lower bound, so ``username >= q AND username < q`` matches nothing. The docstring (lines 255-257) specifies appending the Firestore prefix sentinel U+F8FF.
- **Impact:** Core discovery feature is dead in production. Tests pass because the test fake's `where()` is a no-op (see CQ-12).
- **Fix:** `end = normalised + "\uf8ff"` (the standard Firestore prefix-range upper bound, written as a Python escape).

### SEC-03 · HIGH · Moderation bypass via duplicate profile route
- **File:** `backend/routes/auth.py`, `update_profile`, lines 144–181
- **Issue:** `PATCH /auth/profile` updates `display_name`/`bio` with **no moderation**, while the sibling `PATCH /users/me` (`backend/routes/users.py:97–112`) moderates the same fields. Both routes are live.
- **Impact:** Violates AGENT.md rule #3 ("never bypass moderation"): slurs can be planted in profile fields, the first content other users see. Note: this route is also how the E2E `public_key` is published (`encryption_service.dart:55`) — a fix must preserve that path.
- **Fix:** Add the same moderation checks, or merge both routes into one moderated handler.

### SEC-04 · HIGH · Firestore rule exposes all users' notifications
- **File:** `firestore.rules`, lines 21–23
- **Issue:** `match /users/{userId}/{document=**} { allow read: if request.auth != null; }` grants any signed-in user read access to the entire user subtree — including `users/{uid}/notifications`, which contains moderation rejection reasons and private activity.
- **Fix:** Public read on the user doc only; `isOwner(userId)` for the notifications subcollection.

### SEC-05 · HIGH · Blocks are not enforced anywhere in messaging
- **Files:** `backend/services/messages.py` — `get_or_create_chat` (93–127), `send_message` (238–265); `backend/services/blocks.py` (pure CRUD, never consulted by messaging)
- **Issue:** Neither chat creation nor message send checks the `blocks` collection. The `allow_messages_from` privacy setting (written at onboarding, `users.py:126`) is never read by any code path.
- **Impact:** A user who blocks a harasser can still be messaged by them — a direct failure of the product's core anti-cyberbullying purpose.
- **Fix:** Consult `blocks_service.is_blocked` (both directions) and `allow_messages_from` in `get_or_create_chat` and `send_message`.

### SEC-06 · HIGH · Push notifications are structurally non-functional
- **Files:** `backend/services/users.py`, `register_device_token`, lines 287–299 (writes `users/{uid}.fcm_tokens` ArrayUnion) vs `backend/services/notifications.py`, `send_message_notification`, lines 54–69 (reads document `fcm_tokens/{recipient_uid}`)
- **Issue:** The token registration path and the token read path use entirely different Firestore locations. No code ever writes to the `fcm_tokens` collection.
- **Impact:** FCM pushes silently never fire ("No FCM token — skipping" logged for every user). All notification-send code (`messages.py:317,410,571`) is dead in effect.
- **Fix:** Standardize on one location (schema documents `fcm_tokens/{tokenId}`, `docs/DATABASE_SCHEMA.md:372–385`) and align both paths.

### SEC-07 · HIGH · Onboarding profile fields are unmoderated
- **File:** `backend/routes/auth.py`, `onboard`, lines 83–142 (docstring admits it: "moderation … is added in Phase 2", line 92)
- **Issue:** `username`, `display_name`, `bio` at signup never pass through `moderate_text`, though the cascade is fully live.
- **Fix:** Moderate all three fields before `reserve_username`.

### SEC-08 · MEDIUM · DM images are never Vision-moderated
- **File:** `backend/services/messages.py`, `send_message`, lines 238–414 (stores `image_url` at 281/355 with no `moderate_image` call; contrast `services/posts.py:187–189`)
- **Impact:** Posts get SafeSearch screening; DM images get none — abusive imagery flows unscreened in moderated ("pending") chats.
- **Fix:** Mirror the posts pattern: fire `_moderate_message_image`-style background check that rejects on Vision block.

### SEC-09 · MEDIUM · No Storage security rules
- **Files:** `firebase.json` (only `firestore` section); no `storage.rules` anywhere; documented in `docs/DATABASE_SCHEMA.md` §4 (lines 617–652)
- **Impact:** Bucket relies solely on being private + signed URLs (good baseline), but client-SDK access is ungoverned by the documented rules.
- **Fix:** Add `storage.rules`, register in `firebase.json`, deploy alongside Firestore rules.

### SEC-10 · LOW · Client Firebase config committed (by design, needs restrictions)
- **File:** `frontend/lib/firebase_options.dart`, lines 43–87 (web/Android/iOS API keys, project `safechat-prod-66143`)
- **Note:** Firebase client keys are public-by-design and ship in every APK/JS bundle regardless; they are **not** equivalent to the (correctly gitignored, never-committed) Admin SDK key at `backend/credentials/`. Risk is quota abuse without further controls.
- **Fix:** Apply per-platform API-key restrictions in Google Cloud Console and enable Firebase App Check. No code change required.

### SEC-11 · LOW · CORS allows any localhost origin in production
- **File:** `backend/main.py`, lines 153–161 (`allow_origin_regex=r"http://localhost(:\d+)?"` unconditional)
- **Impact:** Low (bearer-token auth, no cookies), but production has no reason to trust localhost pages.
- **Fix:** Gate the regex on `settings.environment != "production"`.

### Reverse-engineering / exposure review (no further findings)
- APK/asset exposure: `.env` bundled as a Flutter asset contains only `API_BASE_URL` (public by nature). No secrets in Dart sources.
- Logs: backend logs moderation metadata + content hashes, never raw text (`services/moderation_log.py` design per `docs/MODERATION.md`); no tokens logged.
- Git history: clean of key material (verified via `git log --all --diff-filter=A` over credential/env/google-services patterns).

---

## 3. Hardcoded Configuration Audit

| ID | Sev | File:Line | Value | Problem | Fix |
|---|---|---|---|---|---|
| HC-01 | MEDIUM | `.github/workflows/deploy-backend.yml:36,118` | `GCP_PROJECT_ID: safechat-prod-66143` | Project ID hardcoded in test env + Cloud Run `--set-env-vars`, while the same file uses `${{ secrets.GCP_PROJECT_ID }}` elsewhere — split-brain config | Use the secret in both places ⚠️ *owner-only path — see §9* |
| HC-02 | MEDIUM | `.github/workflows/deploy-firestore.yml:57,65` | `--project safechat-prod-66143` + hardcoded rules URL | Same project ID drift | Use `${{ secrets.GCP_PROJECT_ID }}` ⚠️ *owner-only path* |
| HC-03 | MEDIUM | `.github/workflows/ai-review.yml:155` | `GCP_PROJECT_ID: safechat-prod-66143` | Same drift in the new CI workflow | Use the secret ⚠️ *owner-only path* |
| HC-04 | LOW | `frontend/lib/core/network/dio_client.dart:9–10` | fallback `http://10.0.2.2:8000` | Android-emulator URL silently used when `.env` is missing — web/desktop builds silently point nowhere useful | Fail fast (throw) when `API_BASE_URL` is absent in release mode |
| HC-05 | LOW | `backend/moderation/openai_moderation.py:23–24` | `OPENAI_MODEL = "omni-moderation-latest"`, `_TIMEOUT_SECONDS = 3.0` | Model ID/timeout not configurable without redeploy | Move to `Settings` fields with current values as defaults |
| HC-06 | LOW | `backend/moderation/engine.py:37` | `TFIDF_FLAG_THRESHOLD = 0.55` | Tuning parameter fixed at import; comment (line 35) even says "0.5", contradicting the constant | `Settings` field; fix the comment |
| HC-07 | LOW | `backend/core/config.py:22–26` | Default CORS origins list | Acceptable as dev defaults; documented in `.env.example` | No action needed — recorded for completeness |
| HC-08 | INFO | `frontend/lib/firebase_options.dart:43–87` | Firebase client config | See SEC-10 — public-by-design | Key restrictions + App Check |

No hardcoded API keys, JWT secrets, access tokens, or database credentials were found in tracked source. Secrets correctly flow through GitHub Actions secrets (`GCP_SA_KEY`, `FIREBASE_ADMIN_SDK_KEY`, `OPENAI_API_KEY` via Secret Manager `--set-secrets`, `deploy-backend.yml:119`).

---

## 4. API & Backend Review

| ID | Sev | Finding | Evidence | Recommendation |
|---|---|---|---|---|
| API-01 | HIGH | **No rate limiting exists** though `docs/API_CONTRACTS.md` §14 (lines 616–627) documents per-endpoint limits "enforced via middleware" | `backend/main.py` — only CORS middleware registered (153–161); `backend/middleware/` contains only `auth.py` | Implement (e.g. slowapi/Redis or Cloud Armor) or amend the contract; unthrottled POSTs on a teen platform is an abuse vector |
| API-02 | MEDIUM | Duplicate profile-update endpoints with different behavior | `PATCH /auth/profile` (`routes/auth.py:144`) vs `PATCH /users/me` (`routes/users.py:84`) — see SEC-03 | Consolidate |
| API-03 | MEDIUM | Upload contract drift: no `video/mp4`, no `size_bytes` validation, `purpose` enum mismatch (`avatar`/`background` vs contract's `profile`/`message`) | `backend/services/storage.py:24–28`; `backend/routes/uploads.py:27–29`; contract §8 (lines 395–421) | Align allowlist + enforce size caps |
| API-04 | MEDIUM | Stories have no `submit_for_review`/appeal path — flagged text hard-fails with no human-verification option, unlike posts/comments/DMs | `backend/services/stories.py:74–77` (`raise StoryBlocked`, no queue write) | Add the same `submit_for_review` + `moderation_queue` flow |
| API-05 | LOW | `GET /auth/me` vs contract's `POST /auth/me` | `routes/auth.py:47`; contract §2 line 90 | Fix the contract (GET is correct REST) |
| API-06 | LOW | Health endpoint omits documented `openai`/`gemini`/`vision` dependency keys | `backend/routes/health.py:65–74`; contract §12 (lines 584–600) | Add probes or trim contract |
| API-07 | LOW | Notification routes drift from contract (`POST /notifications/read` with `ids[]` and `POST /notifications/fcm-token` don't exist; implementation has per-item + read-all + `/users/device-token`) | `backend/routes/notifications.py`; `routes/users.py:259–266`; contract §9 | Reconcile contract and routes |
| API-08 | LOW | Missing admin endpoints documented in contract §11: reports resolve, posts pending/approve/block, keywords CRUD | Only queue approve/reject + test exist (`backend/routes/admin.py`) | Implement or mark as roadmap |

Authentication is otherwise consistent (every non-health route depends on `get_current_user_claims`; admin routes on `require_admin` — `middleware/auth.py:31–75`). Error envelope handling in `main.py:94–141` is uniform and well designed (422 reserved for moderation, validation remapped to 400).

---

## 5. SafeChat Mode (E2EE) Verification — ✅ FULLY IMPLEMENTED

All six required components exist and are wired end-to-end. **The toggle genuinely changes runtime behavior; it is not cosmetic.**

| Component | Status | Evidence |
|---|---|---|
| UI toggle | ✅ | Lock icon in chat AppBar, mutual-follow-gated, confirmation dialog with accurate copy — `frontend/lib/features/chat/presentation/chat_detail_view.dart:155–213, 232–249` |
| Backend support | ✅ | `PATCH /chats/{id}/encryption-mode` (`routes/messages.py:102–133`) → `set_encryption_mode` with participant + mutual-follow gate (`services/messages.py:160–202`); unfollow force-reverts trust (`services/messages.py:205–235`, called from `routes/users.py:202`); audit trail in `encryption_mode_audit_logs` (`services/messages.py:130–157`) |
| Frontend state | ✅ | Live Firestore stream of `encryption_mode` (`chat_encryption_providers.dart:7–16`) |
| Encryption logic | ✅ | X25519 static-static ECDH → HKDF-SHA256 → AES-256-GCM; private key only in `flutter_secure_storage` (`encryption_service.dart:33–144`); keypair published on login (`auth_provider.dart:58`) |
| Trust boundary | ✅ | `send_message` skips the entire moderation cascade only when `encryption_mode == "trusted"` (`services/messages.py:267–322`); server stores ciphertext + `encrypted: true`; preview/notification use a 🔒 placeholder (line 38) |
| Runtime behavior change | ✅ | Client branches on mode at send time (`chat_detail_view.dart:78–108`): trusted → encrypt-then-send; pending → moderated send with 422-flagged-dialog flow |

### Implementation defects found (not gaps)

- **SM-01 · MEDIUM · Mode-read race can corrupt a trusted chat.** `_sendMessage` reads `ref.read(chatEncryptionModeProvider(...)).value ?? 'pending'` (`chat_detail_view.dart:78–79`). On a freshly opened trusted chat, before the stream's first emission, `.value` is null → defaults to `'pending'` → **plaintext** is sent; the backend independently sees `trusted` and stores it with `encrypted: true` (`services/messages.py:267–283`) — the recipient gets "🔒 Unable to decrypt" and moderation was skipped for plaintext. Fix: await the first stream value (or read the chat doc) before sending.
- **SM-02 · LOW · Stale peer key.** `_loadSharedKey` fetches the peer's `public_key` once in `initState` (`chat_detail_view.dart:49–70`) instead of watching the existing `userPublicKeyProvider` (`chat_encryption_providers.dart:20–29`, currently **unused** — dead code); a peer who generates keys mid-session never becomes decryptable without reopening the screen.
- Documented (not a defect): no forward secrecy — static keypairs, disclosed in `docs/MODERATION.md:25–28` and `DATABASE_SCHEMA.md` §10.

---

## 6. Code Quality Audit

| ID | Sev | File:Line | Issue | Recommendation |
|---|---|---|---|---|
| CQ-01 | MEDIUM | `frontend/lib/features/chat/presentation/chat_list_view.dart:130,131,147` | Three field-name bugs: reads `last_message` (backend writes `last_message_text` — `services/messages.py:298`), `unread_count` (**no unread field is ever written by any backend code**; schema says `unread_counts`), `author_photo_url` on a user doc (users store `photo_url` — `services/users.py:116`) | Fix names; implement `unread_counts` maintenance server-side or drop the badge |
| CQ-02 | MEDIUM | `backend/services/moderation_review.py:83–97` | Approve/reject is check-then-act across three separate writes — two admins can double-resolve (double `post_count` increment via `posts.py:378–383`) | Wrap decision in a Firestore transaction |
| CQ-03 | MEDIUM | `backend/services/posts.py:189`; `backend/services/messages.py:317,410,571` | `asyncio.create_task` fire-and-forget without holding a reference — tasks can be garbage-collected before running (CPython caveat); affected: post image moderation, FCM sends | Keep a module-level task set (or `TaskGroup`) with done-callback discard |
| CQ-04 | MEDIUM | `frontend/lib/features/chat/presentation/chat_list_view.dart:133–137` | N+1: one `FutureBuilder` Firestore `get()` per chat row, re-fired every stream tick; no cache | Cache user docs by uid / denormalize peer info onto the chat doc |
| CQ-05 | MEDIUM | `backend/models/user.py:36–47` + `services/users.py:197–249` | `UpdateProfileRequest.username` validated for length only — no `^[a-z0-9_]+$` charset check (onboarding enforces it, line 12/24–33); `change_username` doesn't re-validate | Apply `USERNAME_PATTERN` on update path |
| CQ-06 | LOW | `backend/services/posts.py:306–325` | `get_posts_by_author` fetches up to 200 docs then filters/sorts in Python — cost + latency | Composite index (`author_uid`, `created_at`) + status filter in query |
| CQ-07 | LOW | `backend/services/messages.py:449–469` | `get_messages` filters non-approved messages *after* `limit()` — a page densely packed with the peer's pending items returns short/empty pages, breaking pagination heuristics | Filter by status in the query for the non-sender case |
| CQ-08 | LOW | `backend/services/notifications.py:115,153,162` | Writes `is_read`; `docs/DATABASE_SCHEMA.md:403` documents `read`. `mark_as_read` (149–155) is not fail-open unlike everything else in the module | Align field name; guard missing doc |
| CQ-09 | LOW | `frontend/lib/features/auth/presentation/login_screen.dart:160` | `// TODO: Navigate to forgot password screen` — dead button | Implement or hide |
| CQ-10 | LOW | `frontend/functions/src/index.ts:1–33` | Cloud Functions scaffold entirely commented out — dead code; the story-expiry job documented in `DATABASE_SCHEMA.md:232–237` does not exist anywhere | Either implement the TTL job here or remove `frontend/functions/` |
| CQ-11 | LOW | `requirements.txt` (repo root) | Stray one-line file (`google-cloud-vision==3.14.0`) duplicating a `backend/requirements.txt` entry — confuses tooling and stack detection | Delete |
| CQ-12 | LOW | `backend/tests/test_messages.py:65–68` (pattern repeated across the suite) | In-memory Firestore fake's `where()` is a documented no-op — query-shape bugs (SEC-02) sail through green tests; frontend has a single placeholder test (`frontend/test/widget_test.dart`) | Implement basic filter support or add emulator-backed integration tests; add real widget tests |
| CQ-13 | LOW | `.github/modernize/java-upgrade/` | Leftover third-party tooling remnant (Java-upgrade hook scripts) unrelated to this Flutter/Python project. Per scope rules it was **not** followed or executed — flagged as an external-tooling reference | Delete the directory |

**Explicitly good (no findings):** moderation engine layering (`moderation/engine.py`), lexicon design with ReDoS-safe possessive quantifiers and span capture (`moderation/lexicon.py`), transaction discipline (`services/follows.py`, `services/users.py:76–131`, `services/posts.py:328–354`), pure `build_item` for batch-consistent queue writes (`services/moderation_queue.py:40–81`), the error-envelope design in `main.py`, and strict tooling (`backend/pyproject.toml`: mypy strict, ruff+bugbear, black).

---

## 7. Dependency Audit

### Flutter (`frontend/pubspec.yaml`)

| Package | Line | Concern |
|---|---|---|
| `flutter_markdown ^0.7.7+1` | 60 | **Officially discontinued** by the Flutter team — no future updates incl. security fixes. Migrate to a maintained fork (e.g. `flutter_markdown_plus`) or `markdown_widget` |
| `google_sign_in: 6.2.1` | 54 | Exact pin (no `^`) blocks even patch releases; 7.x is the current major with breaking-but-required changes for newer Android SDKs |
| `intl_phone_field ^3.2.0` | 55 | Appears unmaintained (no releases in years); used in onboarding — evaluate replacement before OS/SDK drift breaks it |
| `cryptography ^2.7.0` | 64 | Core of SafeChat Mode; maintenance cadence is slow — pin review + test coverage recommended, no known advisory identified |

No duplicate libraries detected. Riverpod 3.x / go_router 17.x / Firebase suite are current-generation.

### Python (`backend/requirements.txt`)

- `fastapi==0.115.12`, `firebase-admin==6.8.0`, `httpx==0.28.1`, `pydantic==2.11.4` — current, no known advisories identified.
- `numpy==1.26.4` — pre-2.0 pin, deliberate for `scikit-learn==1.5.2` model-pickle compatibility (`moderation/data/model.pkl`); an upgrade requires retraining/re-pickling (`moderation/train_model.py`). Acceptable; document it.
- Test deps (`pytest`) ship in the production image because there's a single requirements file — split `requirements-dev.txt` to slim the Cloud Run image (ties to Dockerfile `COPY . /app` which also copies `venv/` unless `.dockerignore` excludes it — it does; verified `backend/.dockerignore` exists).

### Node (`frontend/functions/package.json`)
Dead scaffold (CQ-10) — remove or use; auditing pins is moot until it has real code.

---

## 8. Production Readiness Assessment

| Area | State | Gap |
|---|---|---|
| CI/CD | ✅ Strong: tests + Trivy fs/image scans + SonarCloud + smoke-tested Cloud Run deploy + release APK artifact | Deploy uses hardcoded project ID (HC-01/02); no staging deploy target |
| Environment separation | ❌ | **PR-01 · MEDIUM:** single Firebase project `safechat-prod-66143` for everything; `.env.example:16` requires matching the compiled client config — no dev/staging isolation. Create a second Firebase project + per-env `firebase_options` |
| Secrets management | ✅ | GitHub secrets + Secret Manager (`deploy-backend.yml:119`); Admin key never committed |
| Monitoring / observability | ❌ | **PR-02 · MEDIUM:** No Sentry/Crashlytics/alerting anywhere (pubspec has no crashlytics; backend has no error-reporting integration). Roadmap Phase 8 promises these. Cloud Run logs exist but nothing pages a human |
| Logging | ⚠️ | **PR-03 · LOW:** `request_id` is generated per-response (`main.py:54–58` and each route's `_meta()`) but never attached to log lines — no correlation. Add middleware that injects a request ID into the logging context |
| Reliability | ⚠️ | Fail-open moderation is a documented, reasonable availability choice; fire-and-forget tasks (CQ-03) and non-functional FCM (SEC-06) are the real reliability gaps; Cloud Run `--min-instances 0` (deploy-backend.yml:113) means cold-start latency on the 3-Firestore-call onboarding transaction |
| Scalability | ⚠️ | `get_chats` unpaginated (`services/messages.py:474–489`); `get_followers/get_following` unbounded streams (`services/follows.py:111–142`); fine at beta scale, bounded pagination needed before growth |
| Config management | ✅ | `pydantic-settings` with fail-fast validation (`core/config.py`) is exemplary |
| Error recovery | ⚠️ | Story TTL cleanup absent (CQ-10) → unbounded collection growth; no documented backup/restore runbook (schema doc mentions backups aspirationally, §5) |

---

## 9. Remaining Findings

### Documentation accuracy (composite finding DOC-01 · LOW)

| Doc says | Code does |
|---|---|
| `ROADMAP.md:7–9` "Current State: Phase 0 — Foundation", repo-creation unchecked | Phases 1–7 substantially built and deployed |
| `ARCHITECTURE.md:101,135` moderation is "TF-IDF keyword only", toxic DMs → "200 with blocked status" | Four layers implemented; flagged DMs → **422 MODERATION_FLAGGED** (`routes/messages.py:55–72`) |
| `MODERATION.md` five-layer cascade incl. Gemini; `API_CONTRACTS.md:597` `gemini` health key | No Gemini layer exists (`moderation/engine.py`) |
| `DATABASE_SCHEMA.md:247–251` `chats.last_message{}` object | Flat `last_message_text`/`last_message_at` (`services/messages.py:115–116`) |
| `API_CONTRACTS.md:58` error code `MODERATION_BLOCKED` vs §14b `MODERATION_FLAGGED` | Routes emit `MODERATION_FLAGGED` for content, `MODERATION_BLOCKED` for profile fields (`routes/users.py:105`) — internally inconsistent both in docs and code |
| Doc footers dated "November 2026" | Future-dated relative to today (2026-07-08) |

### External references encountered (reported, not followed — per scope rules)
- `.github/modernize/java-upgrade/` — third-party upgrade-tooling remnant (CQ-13).
- `.agents/skills/` and `frontend/.agents/skills/` — vendored AI-skill documentation packs (GSAP, Material 3, Firebase). Benign, large, read-only reference material; consider pruning unused packs.
- `docs/v0/` — archived previous-generation docs; clearly namespaced, no action needed.
- No Git submodules, no cross-repository paths, no references to any other project were found in tracked code or CI. **Nothing in this audit derives from any repository other than SafeChat 2.0.**

### ⚠️ Governance-protected paths requiring separate approval
Per repository governance, these fixes touch OWNER-ONLY paths and will **not** be made without your explicit sign-off, even after general Phase 2 approval:
- `.github/workflows/deploy-backend.yml` — HC-01 (hardcoded project ID at lines 36, 118)
- `.github/workflows/deploy-firestore.yml` — HC-02 (lines 57, 65)
- `.github/workflows/ai-review.yml` — HC-03 (line 155)
- No changes are proposed to `.github/CODEOWNERS` or `GOVERNANCE.md`.

---

## Phase 2 Gate

- Working tree verified **clean** at audit time (HEAD `4f8ee8c`); the only file created by this audit is `AUDIT.md` itself.
- Awaiting explicit approval (full or per-finding) before any implementation begins.
