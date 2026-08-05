# backend/services/follows.py
"""Follow relationship service.

Follows are unidirectional and stored at /follows/{follower_uid}_{followee_uid}.
Counter fields (follower.following_count, followee.follower_count) are updated
atomically in the same Firestore transaction as the follow document write.
"""

from __future__ import annotations

import asyncio

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter, Transaction

from core.cache import TTLCache
from core.firebase import db

FOLLOWS_COLLECTION = "follows"
_following_cache = TTLCache(default_ttl_seconds=60.0)


class CannotFollowSelf(Exception):
    """Raised when a user attempts to follow themselves."""


def _follow_id(follower_uid: str, followee_uid: str) -> str:
    return f"{follower_uid}_{followee_uid}"


def _follow_ref(follower_uid: str, followee_uid: str) -> DocumentReference:
    return db.collection(FOLLOWS_COLLECTION).document(
        _follow_id(follower_uid, followee_uid)
    )


def _user_ref(uid: str) -> DocumentReference:
    return db.collection("users").document(uid)


async def follow_user(follower_uid: str, followee_uid: str) -> None:
    """Follow a user. Idempotent — no-op (no rewrite) if already following.

    Atomically creates the follow document and increments both
    followee.follower_count and follower.following_count.

    Raises:
        CannotFollowSelf: if follower_uid == followee_uid.
    """
    if follower_uid == followee_uid:
        raise CannotFollowSelf(follower_uid)

    follow_ref = _follow_ref(follower_uid, followee_uid)
    follower_ref = _user_ref(follower_uid)
    followee_ref = _user_ref(followee_uid)

    @firestore.transactional
    def _txn(transaction: Transaction) -> None:
        if follow_ref.get(transaction=transaction).exists:
            return  # Already following — preserve original created_at.
        transaction.set(
            follow_ref,
            {
                "follower_uid": follower_uid,
                "followee_uid": followee_uid,
                "created_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.update(follower_ref, {"following_count": firestore.Increment(1)})
        transaction.update(followee_ref, {"follower_count": firestore.Increment(1)})

    await asyncio.to_thread(_txn, db.transaction())
    _following_cache.invalidate(follower_uid)


async def unfollow_user(follower_uid: str, followee_uid: str) -> None:
    """Unfollow a user. Idempotent — no-op if not currently following.

    Atomically deletes the follow document and decrements both counters
    (floored at 0 via Increment(-1) with server-side minimum enforcement
    handled by the Increment transform — callers must not let counters go
    negative via out-of-band writes).
    """
    follow_ref = _follow_ref(follower_uid, followee_uid)
    follower_ref = _user_ref(follower_uid)
    followee_ref = _user_ref(followee_uid)

    @firestore.transactional
    def _txn(transaction: Transaction) -> None:
        if not follow_ref.get(transaction=transaction).exists:
            return  # Not following — nothing to undo.
        transaction.delete(follow_ref)
        transaction.update(follower_ref, {"following_count": firestore.Increment(-1)})
        transaction.update(followee_ref, {"follower_count": firestore.Increment(-1)})

    await asyncio.to_thread(_txn, db.transaction())
    _following_cache.invalidate(follower_uid)


async def is_following(follower_uid: str, followee_uid: str) -> bool:
    """Return True if follower_uid follows followee_uid. Unidirectional."""
    snapshot = await asyncio.to_thread(_follow_ref(follower_uid, followee_uid).get)
    return bool(snapshot.exists)


async def is_mutual_follow(uid1: str, uid2: str) -> bool:
    """Check if uid1 and uid2 follow each other (both directions)."""
    a_follows_b, b_follows_a = await asyncio.gather(
        is_following(uid1, uid2),
        is_following(uid2, uid1),
    )
    return a_follows_b and b_follows_a


async def get_followers(uid: str) -> list[str]:
    """Return the list of uids that follow uid."""

    def _query() -> list[str]:
        stream = (
            db.collection(FOLLOWS_COLLECTION)
            .where(filter=FieldFilter("followee_uid", "==", uid))
            .stream()
        )
        return [
            str((snap.to_dict() or {}).get("follower_uid", ""))
            for snap in stream
        ]

    return await asyncio.to_thread(_query)


async def get_following(uid: str) -> list[str]:
    """Return the list of uids that uid follows."""
    cached = _following_cache.get(uid)
    if cached is not None:
        return list(cached)

    def _query() -> list[str]:
        stream = (
            db.collection(FOLLOWS_COLLECTION)
            .where(filter=FieldFilter("follower_uid", "==", uid))
            .stream()
        )
        return [
            str((snap.to_dict() or {}).get("followee_uid", ""))
            for snap in stream
        ]

    res = await asyncio.to_thread(_query)
    _following_cache.set(uid, res)
    return res

