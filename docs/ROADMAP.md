# SafeChat Roadmap

Phased build plan from current state to production launch and beyond.

---

## Current State

**Phases 1–7 substantially implemented.** The FastAPI backend (auth, posts,
comments, likes, follows, blocks, DMs, stories, notifications, reports),
the four-layer moderation cascade with human-verification queue and admin
review, the Flutter client (auth, feed, chat, profile, moderation UI), and
SafeChat Mode (conditional E2E encryption) are all built and deployed via
GitHub Actions to Cloud Run + Firebase. Remaining gaps: environment
separation (single Firebase project), error monitoring/alerting, and the
Phase 8+ items below. The phase plan is retained for historical context.

---

## Phase 0 — Foundation (current)

**Goal:** Establish the infrastructure and documentation needed to begin building.

### Deliverables

- [x] GCP project `safechat-prod-66143` created
- [x] Firebase Authentication enabled (Google + Email/Password)
- [x] Cloud Firestore database provisioned (nam5)
- [x] Firebase Storage configured
- [x] Firebase Admin SDK key downloaded
- [x] Project documentation set complete
- [ ] GitHub repository created and initialized
- [ ] Project folder structure committed
- [ ] OpenAI API key obtained (Moderation API)
- [ ] Gemini API key obtained
- [ ] Cloud Vision API enabled
- [ ] Local development environment verified

**Exit criteria:** Repository exists, all credentials secured, environment ready to start backend work.

---

## Phase 1 — Backend Foundation

**Estimated time:** 1 week

**Goal:** Working FastAPI backend with authentication and Firestore integration. No moderation yet.

### Deliverables

- FastAPI project structure
- Firebase Admin SDK integration
- Auth middleware (token verification)
- User onboarding endpoint
- Profile read/update endpoints
- Basic error handling and logging
- Health check endpoint
- Unit tests for core utilities
- Dockerfile for Cloud Run
- `.env.example` documented

**Exit criteria:** Can sign up via API, create profile in Firestore, fetch own profile.

---

## Phase 2 — Moderation Engine

**Estimated time:** 1.5 weeks

**Goal:** All five moderation layers implemented and tested independently.

### Deliverables

- Text normalization module
- Dynamic keyword filter with Firestore caching
- OpenAI Moderation API integration
- Gemini API integration with prompt template
- Cloud Vision Safe Search integration
- Cloud Vision OCR + text cascade
- Unified moderation engine orchestrator
- Comprehensive test suite (clean, toxic, bypass attempts, multilingual)
- Admin endpoint to test moderation interactively
- Moderation logging to Firestore

**Exit criteria:** Cascade processes English, Hindi, and Hinglish samples with >90% accuracy on a curated test set. Latency p95 under 1.5 seconds.

---

## Phase 3 — Core Social Features (Backend)

**Estimated time:** 2 weeks

**Goal:** All v1 social endpoints functional and moderated.

### Deliverables

- Post creation with image upload pipeline
- Feed endpoint (followed users)
- Comment creation and listing
- Like/unlike posts
- Follow/unfollow users
- User search
- Block user
- Reporting endpoint
- All endpoints integrate with moderation engine

**Exit criteria:** Full social feature set accessible via API. Postman/Bruno collection covers all endpoints. Integration tests pass.

---

## Phase 4 — Direct Messaging

**Estimated time:** 1 week

**Goal:** Real-time DMs with moderation.

### Deliverables

- Chat creation logic (composite chat IDs)
- Message send endpoint with moderation
- Chat list endpoint
- Message history endpoint with pagination
- Read receipts
- Firestore Security Rules for chat access
- FCM dispatch on new message

**Exit criteria:** Two test accounts can exchange messages in real-time. Toxic messages are blocked silently. Notifications fire on receipt.

---

## Phase 5 — Frontend Foundation (Flutter)

**Estimated time:** 2 weeks

**Goal:** Flutter app running on both Android and web with auth.

### Deliverables

- Flutter project initialized with Riverpod and GoRouter
- Navigation structure (React Navigation)
- Firebase Auth integration (Google + Email)
- API client with auth token injection
- Sign up and login screens
- Onboarding flow (username selection)
- Basic profile screen
- Theme and design system foundation
- Loading and error states
- Build configuration for both web and Android

**Exit criteria:** User can sign up and log in on both web and Android, see their profile.

---

## Phase 6 — Frontend Core Features

**Estimated time:** 3 weeks

**Goal:** Functional Instagram-like UI for all v1 features.

### Deliverables

- Feed screen with infinite scroll
- Post creation with image picker and upload
- Post detail screen with comments
- Like button with optimistic updates
- Comment composer with moderation feedback
- User profile screen with follow button
- User search screen
- DM list screen
- DM conversation screen with realtime updates
- Stories upload and viewer
- Settings screen
- Block/report flows from UI

**Exit criteria:** App is fully usable end-to-end on both platforms. Moderation feedback is shown clearly when content is blocked.

---

## Phase 7 — Admin Panel

**Estimated time:** 1.5 weeks

**Goal:** Operational tools for moderating live content.

### Deliverables

- Separate admin React app (Firebase Hosting)
- Admin authentication (custom claim verification)
- Pending posts review queue
- Pending stories review queue
- Open reports queue with action buttons
- Keyword filter management UI
- Moderation cascade tester
- Suspended users management
- Basic analytics dashboard (posts/day, reports/day, block rate)

**Exit criteria:** A moderator can fully manage reports, content, and keywords without touching code or the database directly.

---

## Phase 8 — Production Readiness

**Estimated time:** 1 week

**Goal:** Prepare for real users.

### Deliverables

- Rate limiting middleware
- Error monitoring (Sentry)
- Cloud Logging configuration
- Cloud Monitoring dashboards and alerts
- Billing alerts
- CI/CD via GitHub Actions
- Staging environment (separate Firebase project)
- Load testing of moderation endpoint
- Security audit of Firestore rules
- Privacy Policy, Terms of Service, Community Guidelines in app
- Email verification flow
- Password reset flow

**Exit criteria:** App can withstand realistic load. Operational visibility is complete. Legal requirements met.

---

## Phase 9 — Launch (v1.0)

**Estimated time:** 1 week

**Goal:** Public release on Web + Play Store.

### Deliverables

- Play Store listing assets (screenshots, descriptions, icon)
- Android signed APK build
- Play Store developer account setup
- Initial submission to Play Store
- Web deployment to custom domain
- Marketing landing page
- Analytics implementation
- Support email setup
- First 50 invited beta users
- Bug fix sprint based on beta feedback

**Exit criteria:** App publicly available on Play Store and web. Active users using the platform.

---

## Phase 10 — Voice and Video Calls

**Estimated time:** 2 weeks

**Goal:** 1-on-1 voice and video calling.

### Deliverables

- Agora SDK integration in Flutter
- Call signaling backend (FCM-based)
- Call screen UI
- Mute, camera flip, end call controls
- Call quality indicators
- Missed call notifications
- Call history view
- Per-user call privacy settings

**Exit criteria:** Two users can complete voice and video calls reliably across web and Android.

---

## Phase 11 — Growth and Iteration

Ongoing. Features prioritized based on user feedback and metrics.

### Likely candidates

- iOS support (Flutter build for iOS, App Store submission)
- Group chats with moderation
- Verified accounts
- Algorithmic feed (engagement-based ranking)
- Hashtags and discovery
- Live streaming with real-time moderation
- Multi-language UI (i18n)
- Phone number auth
- Two-factor authentication
- Encrypted DMs (E2E)
- Sticker packs and reactions
- Custom moderation rules per community

---

## Cost Projections

These are estimates based on architecture decisions. Real costs depend on usage patterns.

| Users (DAU) | Monthly cost | GenAI credit duration |
|---|---|---|
| 0 — 100 | ₹0 (free tier) | Indefinite |
| 100 — 1,000 | ₹200 — ₹500 | ~15 years |
| 1,000 — 10,000 | ₹1,500 — ₹3,500 | ~2.5 years |
| 10,000 — 100,000 | ₹15,000 — ₹35,000 | ~4 months |
| 100,000+ | ₹50,000+ | Requires monetization |

**Major cost drivers ranked:**
1. Firestore reads (mitigated via aggressive caching)
2. Cloud Vision API calls (mitigated via image hash deduplication)
3. Cloud Storage egress (mitigated via CDN caching)
4. Gemini API (free under credit until exhausted)
5. Cloud Run compute (covered by free tier well into growth)

---

## Critical Path

The dependency chain that drives the timeline:

```
Phase 0  →  Phase 1  →  Phase 2  →  Phase 3  →  Phase 5
                                                    │
                          Phase 4 ─────────────────┤
                                                    ▼
                                                Phase 6  →  Phase 7  →  Phase 8  →  Phase 9
```

Parallel work possible:
- Phase 7 (Admin panel) can start during Phase 6
- Frontend (5,6) and DMs backend (4) can progress in parallel after Phase 3
- Documentation and legal pages updated continuously

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GenAI credit doesn't cover Gemini in production | Medium | High | Fallback to OpenAI-only at higher cost |
| Moderation accuracy below user expectations | Medium | High | Aggressive iteration via Phase 11 reviews |
| Cloud Vision costs exceed budget at scale | Medium | Medium | Image hashing dedup + sampling strategies |
| Play Store rejects app | Low | High | Pre-launch policy review, content rating |
| Solo developer bandwidth | High | High | Contributor onboarding, ship narrow scope |
| Firebase free tier exhaustion before launch | Low | Medium | Aggressive caching from day one |
| Sustained bot/spam attack post-launch | Medium | High | Rate limiting + Cloud Armor + manual review |

---

## Definitions of Done

A phase is complete when:

- All deliverables shipped
- Tests passing in CI
- Documentation updated to reflect changes
- No critical bugs in tracker
- Demo-able end-to-end
- Contributor (or another developer) can run the relevant components locally

---

## What We Are Not Building (Explicitly)

To preserve focus, these are explicitly out of scope:

- Marketplace, e-commerce, or in-app purchases (v1)
- Crypto, wallet, NFT features
- AR filters or face beautification
- Reels-style video editor
- Public APIs for third-party developers
- White-label or B2B offering

---

*Last updated: November 2026*
