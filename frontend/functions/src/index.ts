/**
 * SafeChat Cloud Functions.
 *
 * Currently: scheduled cleanup of expired stories (CQ-10).
 *
 * Stories carry an `expires_at` (created_at + 24h) and are already hidden
 * from every read path once expired (backend filters `expires_at > now`), so
 * this job is purely about bounding Firestore growth / storage cost — it is
 * NOT user-visible behavior. Documented in docs/DATABASE_SCHEMA.md
 * ("Scheduled Cloud Function runs hourly").
 */

import {setGlobalOptions} from "firebase-functions";
import {onSchedule} from "firebase-functions/v2/scheduler";
import * as logger from "firebase-functions/logger";
import {initializeApp} from "firebase-admin/app";
import {getFirestore, Timestamp} from "firebase-admin/firestore";

setGlobalOptions({maxInstances: 10});

initializeApp();
const db = getFirestore();

// Firestore caps writes at 500 per batch.
const BATCH_LIMIT = 500;

/**
 * Delete stories whose `expires_at` is in the past, in batches.
 * Runs hourly. Each invocation clears up to BATCH_LIMIT expired stories;
 * with hourly cadence and a 24h TTL this comfortably keeps pace with
 * realistic story volumes.
 */
export const cleanupExpiredStories = onSchedule(
  {schedule: "every 60 minutes", timeoutSeconds: 300},
  async () => {
    const now = Timestamp.now();
    const expired = await db
      .collection("stories")
      .where("expires_at", "<=", now)
      .limit(BATCH_LIMIT)
      .get();

    if (expired.empty) {
      logger.info("cleanupExpiredStories: nothing to delete");
      return;
    }

    const batch = db.batch();
    expired.docs.forEach((doc) => batch.delete(doc.ref));
    await batch.commit();

    logger.info(
      `cleanupExpiredStories: deleted ${expired.size} expired story doc(s)`
    );
  }
);
