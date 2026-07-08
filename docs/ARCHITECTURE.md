# SafeChat Architecture (V1)

This document describes the system architecture, hosting decisions, and scaling considerations for SafeChat.

---

## 1. System Overview

SafeChat is built as a three-tier system:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  CLIENT LAYER (Flutter 3.x)                                 │
│  ├─ Android app (Play Store)                                │
│  ├─ Web app (browser)                                       │
│  └─ Direct Firebase SDK access for realtime features        │
│                                                             │
└──────────────────┬──────────────────────┬───────────────────┘
                   │                      │
                   │ REST API             │ Firestore Listeners
                   │ (HTTPS)              │ (WebSocket)
                   │                      │
┌──────────────────▼──────────┐  ┌────────▼─────────────────┐
│                             │  │                          │
│  APPLICATION LAYER          │  │  DATA + AUTH LAYER       │
│  (FastAPI on Cloud Run)     │  │  (Firebase / GCP)        │
│                             │  │                          │
│  ├─ Auth verification       │  │  ├─ Firebase Auth        │
│  ├─ Moderation engine       │  │  ├─ Cloud Firestore      │
│  ├─ Business logic          │  │  ├─ Firebase Storage     │
│  ├─ Push notification       │  │  ├─ Firebase Hosting     │
│  │   dispatch               │  │  └─ Cloud Messaging      │
│  └─ External API calls      │  │                          │
│                             │  └──────────────────────────┘
└─────────────┬───────────────┘
              │
              │ HTTPS
              │
┌─────────────▼─────────────────────────────────────────────┐
│                                                           │
│  EXTERNAL SERVICES (Upcoming Features)                    │
│  ├─ OpenAI Moderation API (text classification)           │
│  ├─ Google Gemini API (nuanced moderation, Hindi/lang)    │
│  ├─ Google Cloud Vision (image safety + OCR)              │
│  └─ Agora SDK (voice/video calls)                         │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

---

## 2. Component Responsibilities

### Client Layer (Flutter App)

**Responsibilities:**
- User interface for both Android and Web from a single codebase using Riverpod and GoRouter.
- Direct read access to Firestore via Firebase SDK (real-time listeners for chat, feed, notifications).
- All write operations routed through backend API for moderation.
- Firebase Auth token management.
- Local state management.
- Push notification reception (FCM).

**Why direct Firestore reads:**
Real-time chat performance requires WebSocket-based listeners. Routing reads through the backend would add latency and require maintaining our own WebSocket infrastructure. Firestore's security rules enforce that users can only read what they're authorized to see.

**Why writes go through backend:**
Every write (message, post, comment, story, profile update) must pass through the moderation cascade. Allowing direct Firestore writes would bypass moderation.

### Application Layer (FastAPI on Cloud Run)

**Responsibilities:**
- Verify Firebase Auth tokens on every request.
- Run all content through the moderation cascade (weighted lexicon → TF-IDF
  classifier → OpenAI Moderation → Vision SafeSearch on images) before
  persisting. Layers fail open; OpenAI/Vision run when configured.
- Write moderated content to Firestore.
- Dispatch push notifications via FCM.
- Handle media upload pipeline (signed URLs to Firebase Storage).

**Why Cloud Run:**
- Autoscales from zero to thousands of containers.
- Native HTTPS, integrates with Firebase Auth tokens.
- Container-based — same Docker setup that works locally.

### Data + Auth Layer (Firebase / GCP)

**Cloud Firestore (database):**
- Document-based, real-time, globally distributed.
- Security rules enforce row-level access control.

**Firebase Auth:**
- Handles session management, OAuth flows.
- ID tokens verified by backend on every request.

**Firebase Storage:**
- Stores all user media (profile pictures, posts).
- Access controlled via signed URLs from backend.

### External Services (Upcoming)

Currently, the Moderation Engine relies strictly on **TF-IDF Keyword Detection** to establish the foundation. Advanced moderation pipelines are documented here as upcoming phases:

**OpenAI Moderation API:**
- Text moderation across categories: hate, harassment, violence, self-harm, sexual content.

**Google Gemini API:**
- Nuanced moderation for borderline cases and Hinglish context.

**Google Cloud Vision:**
- Safe Search for adult/violence/racy content in images.
- OCR to extract text from memes.

---

## 3. Hosting & Infrastructure

- **Cloud Run:** Hosts FastAPI Backend.
- **Firestore:** Database backend with security rules.
- **Firebase Storage:** Media storage.

---

## 4. Data Flow Examples

### Sending a Direct Message

```
1. User types message in Flutter app
2. App calls POST /api/v1/messages
   - Includes Firebase Auth ID token
3. Backend verifies token via Firebase Admin SDK
4. Backend enforces the relationship guard (blocks + `allow_messages_from`)
5. Backend runs message through the moderation cascade (lexicon → TF-IDF →
   OpenAI → Vision-on-image):
   a. Clean → status `approved`
   b. Flagged, not submitted → `422 MODERATION_FLAGGED` (with match spans)
   c. Flagged + `submit_for_review` → status `pending_review`, queued
6. If approved: write to /chats/{chatId}/messages/{messageId} in Firestore
7. Recipient's Firestore listener triggers — message appears instantly
8. Backend dispatches FCM push notification to recipient (token at
   `fcm_tokens/{uid}`)

> **SafeChat Mode exception:** in a `trusted` chat the client encrypts the
> message before it reaches the backend, which stores ciphertext and skips
> the cascade entirely. See docs/MODERATION.md and DATABASE_SCHEMA.md §10.
```

### Uploading a Post with Image

```
1. User selects image in app
2. App requests signed upload URL: POST /api/v1/uploads/sign
3. Backend returns signed Firebase Storage URL
4. App uploads image directly to Firebase Storage (bypasses backend)
5. App calls POST /api/v1/posts with caption + image path
6. Backend runs moderation on caption via TF-IDF filter.
   (Note: Vision API moderation for images is an upcoming feature)
7. If clean: write post to /posts/{postId} (status: approved)
8. App displays appropriate UI to user
```

### Reading the Feed

```
1. User opens app
2. App subscribes to Firestore query:
   /posts where author in [followedUsers] orderBy createdAt desc limit 20
3. Security Rules verify user can read these posts
4. Firestore streams matching documents directly to client
5. New posts appear in real-time via listener
6. No backend involvement — pure Firestore real-time
```

---

## 5. Authentication Flow

```
1. User opens app, taps "Sign in with Google" or Email/Password
2. Firebase Auth SDK executes auth flow
3. Firebase Auth returns ID token (JWT) to app
4. App stores token securely, attaches to every API request via Dio interceptor
5. Backend verifies token on each request via Firebase Admin SDK
6. Token contains user UID — used to authorize Firestore operations
```
