# SafeChat Database Schema

Firestore is a document database, not a relational one. This document describes the collection structure, document shapes, indexes, and security rules.

---

## 1. Schema Overview

```
firestore/
├── users/{uid}                        — User profiles
│   └── notifications/{notifId}        — Subcollection: per-user notifications
│
├── usernames/{username}               — Username uniqueness index
│
├── follows/{followId}                 — Follower relationships
│
├── blocks/{blockId}                   — Blocked user pairs
│
├── posts/{postId}                     — All posts
│   └── comments/{commentId}           — Subcollection: comments on post
│   └── likes/{uid}                    — Subcollection: who liked
│
├── stories/{storyId}                  — Stories (24hr expiry)
│   └── views/{uid}                    — Subcollection: who viewed
│
├── chats/{chatId}                     — Chat metadata
│   └── messages/{messageId}           — Subcollection: messages in chat
│
├── reports/{reportId}                 — User reports
│
├── moderation_keywords/{keywordId}    — Dynamic keyword filter entries
├── moderation_logs/{logId}            — Moderation decision logs
├── encryption_mode_audit_logs/{logId} — SafeChat Mode transition log
│
└── fcm_tokens/{tokenId}               — Push notification tokens
```

**Note on collection naming.** Firestore paths must alternate
collection / document segments, so `moderation/keywords/{id}` is not a valid
document path (it would be 3 segments: collection → doc → collection). The
moderation data therefore lives in two flat top-level collections:
`moderation_keywords` and `moderation_logs`.

---

## 2. Document Shapes

### `users/{uid}`

The `uid` matches the Firebase Auth UID.

```typescript
{
  uid: string,                  // matches doc ID
  email: string,                // from Firebase Auth
  username: string,             // unique, lowercase, 3-30 chars
  display_name: string,         // 1-50 chars
  bio: string,                  // 0-200 chars
  photo_url: string | null,     // Firebase Storage URL — pre-cropped on upload
  background_url: string | null, // pre-cropped (16:9) on upload
  public_key: string | null,    // base64 X25519 public key, for SafeChat Mode E2E encryption
  
  follower_count: number,       // denormalized counter
  following_count: number,
  post_count: number,
  
  is_verified: boolean,         // future: blue check
  is_suspended: boolean,        // admin-set
  
  created_at: Timestamp,
  updated_at: Timestamp,
  last_active_at: Timestamp,
  
  // Privacy settings
  private_account: boolean,
  allow_messages_from: "everyone" | "followers" | "none",
  
  // Internal
  schema_version: 1,
}
```

**Why denormalize counters?**
Firestore charges per document read. Computing follower count by querying the follows collection on every profile view would be expensive. Counters are updated via Cloud Functions or transaction in the backend on follow/unfollow.

### `usernames/{username}`

Username uniqueness via separate collection. Document ID is the lowercase username.

```typescript
{
  username: string,
  uid: string,                  // owning user's UID
  reserved_at: Timestamp,
}
```

**Why a separate collection?**
Firestore cannot enforce uniqueness on a field within a collection. Using the username as document ID makes uniqueness automatic — a create with an existing ID fails.

### `follows/{followId}`

Document ID format: `{follower_uid}_{followee_uid}`

```typescript
{
  follower_uid: string,
  followee_uid: string,
  created_at: Timestamp,
}
```

**Why composite ID?**
Eliminates duplicate follows and makes lookup O(1) without querying.

### `blocks/{blockId}`

Document ID format: `{blocker_uid}_{blocked_uid}`

```typescript
{
  blocker_uid: string,
  blocked_uid: string,
  created_at: Timestamp,
}
```

### `posts/{postId}`

```typescript
{
  id: string,
  author_uid: string,
  author_username: string,      // denormalized
  author_display_name: string,  // denormalized
  author_photo_url: string,     // denormalized
  
  caption: string,              // 0-2000 chars
  media_urls: string[],         // 1-10 URLs
  media_type: "image" | "video",
  
  status: "approved" | "pending_review" | "blocked" | "deleted",
  moderation: {
    layer: "keyword" | "openai" | "gemini" | "vision" | null,
    category: string | null,
    confidence: number | null,
  },
  
  like_count: number,
  comment_count: number,
  view_count: number,
  
  created_at: Timestamp,
  updated_at: Timestamp,
  
  schema_version: 1,
}
```

**Why denormalize author info?**
Feed display requires author name and photo. Without denormalization, every post in a feed requires a separate user document read. With 20 posts per page and 50 reads/day free tier, denormalization is essential.

**Tradeoff:** When a user updates their photo, all their old post documents have stale data. Acceptable — we update on next post edit or via background job.

### `posts/{postId}/comments/{commentId}`

```typescript
{
  id: string,
  post_id: string,
  author_uid: string,
  author_username: string,
  author_photo_url: string,
  
  text: string,                 // 1-500 chars
  parent_comment_id: string | null,  // for nested replies
  
  like_count: number,
  reply_count: number,
  
  status: "approved" | "blocked" | "deleted",
  
  created_at: Timestamp,
}
```

### `posts/{postId}/comments/{commentId}/likes/{uid}`

Document ID = liker's UID.

```typescript
{
  uid: string,
  created_at: Timestamp,
}
```

### `posts/{postId}/likes/{uid}`

Document ID = liker's UID.

```typescript
{
  uid: string,
  created_at: Timestamp,
}
```

### `stories/{storyId}`

```typescript
{
  id: string,
  author_uid: string,
  author_username: string,
  author_photo_url: string,
  
  media_url: string,
  media_type: "image" | "video",
  caption: string,              // 0-200 chars
  
  status: "approved" | "pending_review" | "blocked",
  
  view_count: number,
  
  created_at: Timestamp,
  expires_at: Timestamp,        // created_at + 24 hours
}
```

**Auto-expiry:**
Firestore doesn't have native TTL. Implemented via:
- Scheduled Cloud Function runs hourly
- Queries stories where `expires_at < now` and `status = approved`
- Updates status to `expired`

### `chats/{chatId}`

Chat ID format: `{sorted_uid1}_{sorted_uid2}` — alphabetically sorted UIDs joined by underscore. This ensures consistent chat IDs regardless of who initiates.

```typescript
{
  id: string,
  participants: string[],       // [uid1, uid2]
  
  last_message: {
    text: string,               // truncated to 100 chars
    sender_uid: string,
    created_at: Timestamp,
  } | null,
  
  unread_counts: {
    [uid: string]: number,
  },
  
  // SafeChat Mode. "pending" = ON (unencrypted, full moderation pipeline).
  // "trusted" = OFF (client-side E2E encrypted, moderation skipped).
  // Only togglable between mutual followers — see section 10.
  encryption_mode: "pending" | "trusted",  // default: "pending"
  
  created_at: Timestamp,
  updated_at: Timestamp,
}
```

**Why denormalize last message?**
Chat list UI shows preview of latest message per chat. Querying messages for each chat in the list would be expensive.

### `chats/{chatId}/messages/{messageId}`

```typescript
{
  id: string,
  chat_id: string,
  sender_uid: string,
  recipient_uid: string,
  
  text: string | null,          // 0-2000 chars (null if media-only)
  media_url: string | null,
  media_type: "image" | "video" | null,
  
  read_at: Timestamp | null,
  
  // See section 9 for the status/moderation fields ("approved" |
  // "pending_review" | "rejected") that apply when the parent chat's
  // encryption_mode is "pending".
  
  // True when `text` is client-side E2E ciphertext, i.e. this message was
  // sent while the chat's encryption_mode was "trusted". Recorded per-message
  // (not derived from the chat's current mode) so old messages stay readable
  // as history after the chat toggles modes again — see section 10.
  encrypted: boolean,            // default: false
  
  created_at: Timestamp,
}
```

### `reports/{reportId}`

```typescript
{
  id: string,
  reporter_uid: string,
  
  target_type: "message" | "post" | "comment" | "story" | "user",
  target_id: string,
  reported_uid: string,         // owner of target content
  
  reason: "harassment" | "spam" | "hate" | "threat" | "impersonation" | "other",
  description: string,          // 0-500 chars
  
  status: "pending" | "resolved" | "dismissed",
  resolution: {
    action: "block_content" | "warn_user" | "suspend_user" | "dismiss" | null,
    admin_uid: string | null,
    notes: string,
    resolved_at: Timestamp | null,
  },
  
  created_at: Timestamp,
}
```

### `moderation_keywords/{keywordId}`

```typescript
{
  id: string,
  category: "english_slurs" | "hindi_slurs" | "hinglish_slurs" | "threats" | "bypass_patterns",
  value: string,                // the word or regex pattern
  is_regex: boolean,
  severity: "high" | "medium" | "low",
  notes: string,                // admin notes
  
  added_by: string,             // admin uid
  created_at: Timestamp,
  updated_at: Timestamp,
}
```

### `moderation_logs/{logId}`

Privacy-preserving log of moderation decisions. Raw content is never stored.

```typescript
{
  id: string,
  
  content_hash: string,         // SHA-256 of content
  content_type: "message" | "post" | "comment" | "story" | "profile" | "test",
  content_id: string | null,    // null for admin-test calls
  author_uid: string,
  
  verdict: "approved" | "blocked",
  layer_triggered: "keyword" | "openai" | "gemini" | "vision" | null,
  category: string | null,
  confidence: number | null,
  
  api_latencies: {
    keyword_ms: number | null,   // in-process layer, tracked for parity
    openai_ms: number | null,
    gemini_ms: number | null,
    vision_ms: number | null,
  },
  total_latency_ms: number,
  
  created_at: Timestamp,
}
```

### `fcm_tokens/{tokenId}`

Document ID = the FCM token itself (truncated/hashed for safety).

```typescript
{
  uid: string,
  device_type: "android" | "web" | "ios",
  token: string,                // full FCM token
  user_agent: string,
  created_at: Timestamp,
  last_used_at: Timestamp,
}
```

### `users/{uid}/notifications/{notifId}`

Subcollection — notifications are user-scoped.

```typescript
{
  id: string,
  type: "like" | "comment" | "follow" | "mention" | "message",
  actor_uid: string,
  actor_username: string,
  actor_photo_url: string,
  
  target_type: "post" | "comment" | "story" | "profile",
  target_id: string,
  target_preview: string,       // 100 chars of context
  
  read: boolean,
  
  created_at: Timestamp,
}
```

---

## 3. Composite Indexes

Firestore requires explicit composite indexes for queries that combine multiple fields.

### Required indexes

```yaml
- collection: posts
  fields:
    - author_uid: ASCENDING
    - created_at: DESCENDING
  purpose: "User's own posts in profile view"

- collection: posts
  fields:
    - status: ASCENDING
    - created_at: DESCENDING
  purpose: "Admin: pending posts queue"

- collection: stories
  fields:
    - author_uid: ASCENDING
    - expires_at: ASCENDING
  purpose: "Get unexpired stories of a user"

- collection group: messages
  fields:
    - chat_id: ASCENDING
    - created_at: DESCENDING
  purpose: "Chat message history"

- collection: reports
  fields:
    - status: ASCENDING
    - created_at: DESCENDING
  purpose: "Admin: pending reports queue"

- collection: follows
  fields:
    - follower_uid: ASCENDING
    - created_at: DESCENDING
  purpose: "Users a user follows, ordered by recency"

- collection: follows
  fields:
    - followee_uid: ASCENDING
    - created_at: DESCENDING
  purpose: "User's followers, ordered by recency"
```

These are defined in `firestore.indexes.json` and deployed via Firebase CLI.

---

## 4. Security Rules

Firestore Security Rules are the database-level access control. They run on every read/write.

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
  
    // Helper functions
    function isAuthenticated() {
      return request.auth != null;
    }
    
    function isOwner(uid) {
      return isAuthenticated() && request.auth.uid == uid;
    }
    
    function isAdmin() {
      return isAuthenticated() && request.auth.token.admin == true;
    }
    
    function isNotBlocked(targetUid) {
      // Check that target hasn't blocked current user
      return !exists(/databases/$(database)/documents/blocks/$(targetUid + '_' + request.auth.uid));
    }
    
    
    // USERS — public read, owner-only write
    match /users/{uid} {
      allow read: if isAuthenticated();
      allow create: if isOwner(uid);
      allow update: if isOwner(uid)
                    && request.resource.data.uid == resource.data.uid
                    && request.resource.data.email == resource.data.email;
      allow delete: if false;  // Soft delete only via backend
      
      // Notifications subcollection — owner only
      match /notifications/{notifId} {
        allow read, update: if isOwner(uid);
        allow create: if false;  // Only backend creates these
        allow delete: if isOwner(uid);
      }
    }
    
    
    // USERNAMES — public read, no client writes
    match /usernames/{username} {
      allow read: if isAuthenticated();
      allow write: if false;  // Backend only
    }
    
    
    // POSTS — public read of approved, owner/admin write
    match /posts/{postId} {
      allow read: if isAuthenticated()
                  && (resource.data.status == 'approved' 
                      || isOwner(resource.data.author_uid)
                      || isAdmin());
      allow create: if false;  // Backend only (moderation enforced there)
      allow update: if isAdmin();  // Admins can change status
      allow delete: if isOwner(resource.data.author_uid) || isAdmin();
      
      // Comments subcollection
      match /comments/{commentId} {
        allow read: if isAuthenticated();
        allow create: if false;  // Backend only
        allow delete: if isOwner(resource.data.author_uid) || isAdmin();
      }
      
      // Likes subcollection
      match /likes/{likerUid} {
        allow read: if isAuthenticated();
        allow create, delete: if isOwner(likerUid);
      }
    }
    
    
    // STORIES — similar to posts
    match /stories/{storyId} {
      allow read: if isAuthenticated()
                  && resource.data.status == 'approved'
                  && resource.data.expires_at > request.time;
      allow create: if false;
      allow update: if isAdmin();
      allow delete: if isOwner(resource.data.author_uid) || isAdmin();
    }
    
    
    // CHATS — participants only
    match /chats/{chatId} {
      allow read: if isAuthenticated() 
                  && request.auth.uid in resource.data.participants;
      allow create, update: if false;  // Backend only
      
      // Messages — participants only, no direct writes
      match /messages/{messageId} {
        allow read: if isAuthenticated()
                    && request.auth.uid in get(/databases/$(database)/documents/chats/$(chatId)).data.participants;
        allow create, update, delete: if false;  // Backend only
      }
    }
    
    
    // FOLLOWS — public read, owner-only write
    match /follows/{followId} {
      allow read: if isAuthenticated();
      allow create: if false;  // Backend (need to update counters)
      allow delete: if false;  // Backend
    }
    
    
    // BLOCKS — owner only
    match /blocks/{blockId} {
      allow read: if isAuthenticated()
                  && resource.data.blocker_uid == request.auth.uid;
      allow create, delete: if false;  // Backend only
    }
    
    
    // REPORTS — reporter can read own, admins read all
    match /reports/{reportId} {
      allow read: if isAuthenticated()
                  && (resource.data.reporter_uid == request.auth.uid || isAdmin());
      allow create: if false;  // Backend only
      allow update: if isAdmin();
    }
    
    
    // MODERATION — admin only
    match /moderation_keywords/{keywordId} {
      allow read: if isAdmin();
      allow write: if false;  // Backend with admin verification
    }
    
    match /moderation_logs/{logId} {
      allow read: if isAdmin();
      allow write: if false;  // Backend only
    }
    
    
    // FCM TOKENS — owner only
    match /fcm_tokens/{tokenId} {
      allow read, write: if false;  // Backend only
    }
    
  }
}
```

### Storage Rules

```javascript
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
  
    // Profile pictures — public read, owner write
    match /profiles/{uid}/{fileName} {
      allow read: if request.auth != null;
      allow write: if request.auth != null 
                   && request.auth.uid == uid
                   && request.resource.size < 5 * 1024 * 1024  // 5MB max
                   && request.resource.contentType.matches('image/.*');
    }
    
    // Post media — public read, signed-URL write only
    match /posts/{postId}/{fileName} {
      allow read: if request.auth != null;
      allow write: if false;  // Only via backend signed URLs
    }
    
    // Story media — same as posts
    match /stories/{storyId}/{fileName} {
      allow read: if request.auth != null;
      allow write: if false;
    }
    
    // Message media — participants only
    match /messages/{chatId}/{fileName} {
      allow read: if request.auth != null
                  && request.auth.uid in firestore.get(/databases/(default)/documents/chats/$(chatId)).data.participants;
      allow write: if false;
    }
    
  }
}
```

---

## 5. Data Lifecycle

### Soft deletion

Users and posts are soft-deleted, not hard-deleted:
- `status` becomes `deleted`
- Content is hidden from queries
- Hard deletion happens via scheduled job after 30 days

This allows recovery from accidental deletion and meets compliance requirements.

### Hard deletion (GDPR/DPDP)

When a user requests account deletion:
1. Mark user as `deleted` immediately (hidden from app)
2. Scheduled function within 7 days:
   - Delete all posts, comments, stories, messages by user
   - Delete user document
   - Delete username reservation
   - Delete media from Storage
   - Anonymize moderation logs (replace `author_uid` with `deleted_user`)
3. FCM tokens removed immediately

### Backup

Firestore automatic backups: daily, 7-day retention. For longer retention, periodic export to Cloud Storage.

---

## 6. Migration Strategy

### Adding fields

New optional fields can be added without migration. Code handles missing fields with defaults.

### Removing or renaming fields

1. Add new field, write to both old and new
2. Backfill old documents via migration script
3. Update reads to use new field
4. Stop writing to old field
5. After deprecation period, remove old field

### Schema versioning

Every document has `schema_version`. Backend can migrate documents lazily on read if version mismatches.

---

## 7. Cost Optimization Patterns

| Pattern | Saves |
|---|---|
| Denormalized counters on user doc | 100s of reads per profile view |
| Denormalized author info on posts | 20+ reads per feed page |
| Last message preview on chat doc | N reads per chat list |
| Username collection for uniqueness | Cheaper than collection-group query |
| Pagination with cursors | Avoids full-collection reads |
| Subcollections for high-cardinality data | Comments don't bloat post doc |

---

## 8. What we deliberately did NOT do

| Pattern | Why not |
|---|---|
| Single-collection messages | Subcollection per chat enables better security rules |
| Composite document IDs everywhere | Hurts readability outside specific use cases |
| Denormalizing follower list onto user doc | Unbounded array; could hit 1MB doc limit |
| Storing media in Firestore | Storage exists for this; Firestore is for structured data |
| Using arrays for likes | Hits 1MB limit at ~50k likes per post; subcollection scales infinitely |

---

## 9. Moderation: content status + review queue

### Status fields on content
`posts`, `comments` (`posts/{id}/comments`), and `messages`
(`chats/{id}/messages`) each carry:

- `status`: `approved` | `pending_review` | `rejected` (default `approved`)
- `moderation_layer`, `moderation_reason` — which cascade layer flagged it + why
- `flagged_terms` — matched lexicon terms
- `rejection_reason` — reason shown to the author (rejected only)

Only `approved` content is public; `pending_review`/`rejected` are hidden from
feeds/threads and surfaced to the author in Profile ▸ Appeals. Counters
(`post_count`, `comment_count`) reflect approved content only.

### `moderation_queue/{queueId}`
One record per piece of content submitted for human verification. Written by the
backend in the same batch as the content document; never written by clients.

| Field | Type | Notes |
|---|---|---|
| id | string | == queueId |
| content_type | string | `post` \| `comment` \| `message` |
| content_id | string | id of the post/comment/message |
| post_id / chat_id | string? | parent post (comments) / chat (messages) |
| author_uid / author_username | string | content author |
| text | string | the flagged text |
| matches | array | `{term, category, weight, start, end}` spans |
| flagged_terms / categories | array<string> | de-duplicated terms / categories |
| lexicon_score / tfidf_score | number? | per-layer scores |
| layer | string? | triggering layer |
| status | string | `pending_review` \| `approved` \| `rejected` |
| reason / note | string? | rejection reason / optional author note |
| created_at / resolved_at | timestamp | |
| resolved_by | string? | admin uid |

**Indexes** (`firestore.indexes.json`): `status ASC, created_at ASC` (admin
queue, FIFO) and `author_uid ASC, created_at DESC` (author's appeals).
**Rules:** readable only by admins or the `author_uid`; backend-only writes.

---

## 10. SafeChat Mode (conditional E2E encryption for chats)

Per-chat toggle between two states, stored as `chats/{chatId}.encryption_mode`:

| Mode | Meaning | Moderation | Storage |
|---|---|---|---|
| `pending` (default) | SafeChat Mode ON | Full lexicon → TF-IDF → OpenAI → Vision pipeline runs | Plaintext |
| `trusted` | SafeChat Mode OFF | Skipped entirely — backend never sees plaintext | Client-side E2E ciphertext |

**Mutual-follow gate.** The mode can only be changed via
`PATCH /chats/{chat_id}/encryption-mode` when the two participants mutually
follow each other (`services/follows.py::is_mutual_follow`). Non-mutual chats
are always forced to `pending` — no way to disable moderation until the other
user follows back.

**Unfollow reverts trust.** If either participant unfollows the other, any
`trusted` chat between them is immediately forced back to `pending`
(`services/messages.py::revert_chat_to_pending_if_trusted`), and the
transition is recorded in `encryption_mode_audit_logs/{logId}`:

| Field | Type | Notes |
|---|---|---|
| chat_id | string | |
| previous_mode / new_mode | string | `pending` \| `trusted` |
| reason | string | `toggle` (user action) \| `unfollow` (system-forced) |
| actor_uid | string? | null for system-forced transitions |
| created_at | timestamp | |

**Rules:** backend-only, not client-readable (mirrors `moderation_logs`).

**No retroactive encryption.** Toggling a chat to `trusted` does not encrypt
its existing plaintext history — old messages remain exactly as sent. Each
message records its own `encrypted` flag at send time (section 2), so display
logic never depends on the chat's *current* mode.

**Client-side crypto (Flutter).** Each user generates an X25519 keypair on
first login (`cryptography` package); the private key lives only in
`flutter_secure_storage` and the public key is published to
`users/{uid}.public_key`. When sending in a `trusted` chat, the client derives
a shared secret via static-static X25519 ECDH with the recipient's public key,
then encrypts with a symmetric AEAD cipher before the ciphertext ever reaches
the backend. This is intentionally simple asymmetric encryption, **not**
Signal Protocol / Double Ratchet.

**Known limitation — no forward secrecy.** Because both sides use long-lived
static keypairs, a compromised private key exposes every past message
encrypted to it. Upgrading to a proper double-ratchet (Signal Protocol) for
forward secrecy is documented future work, not in scope for this phase.

---

*Last updated: July 2026 (SafeChat Mode conditional E2E encryption)*
