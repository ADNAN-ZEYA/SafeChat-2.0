# backend/services/users.py
"""User service layer.

Operations against /users/{uid} and /usernames/{username}. Username reservation
and profile creation happen in a single Firestore transaction so the two
documents can never get out of sync.
"""

from __future__ import annotations

import asyncio
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter, Transaction

from core.firebase import db
from models.user import USERNAME_PATTERN, UserProfile, UserSearchResult


class UsernameTaken(Exception):
    """Raised when the requested username is already owned by a different uid."""


class PhoneNumberTaken(Exception):
    """Raised when the requested phone number is already registered."""


class AlreadyOnboarded(Exception):
    """Raised when /users/{uid} already exists."""


class ProfileNotFound(Exception):
    """Raised when an update targets a /users/{uid} doc that doesn't exist."""


def _username_ref(username: str) -> DocumentReference:
    return db.collection("usernames").document(username)


def _user_ref(uid: str) -> DocumentReference:
    return db.collection("users").document(uid)


def _phone_ref(phone_number: str) -> DocumentReference:
    return db.collection("phone_numbers").document(phone_number)


async def reserve_username(
    *,
    uid: str,
    email: str | None,
    phone_number: str | None,
    username: str,
    display_name: str,
    dob: str,
    bio: str,
    photo_url: str | None = None,
) -> UserProfile:
    """Atomically reserve a username and create the user profile.

    Both /usernames/{username} and /users/{uid} are written in a single
    transaction. A pre-existing username doc owned by the same uid is treated
    as a recoverable retry (e.g. previous onboarding attempt crashed after
    reservation but before user-doc write); a pre-existing user doc is not.

    Raises:
        UsernameTaken: /usernames/{username} exists and belongs to another uid.
        AlreadyOnboarded: /users/{uid} already exists.
    """
    username_ref = _username_ref(username)
    phone_ref = _phone_ref(phone_number) if phone_number else None
    user_ref = _user_ref(uid)

    @firestore.transactional
    def _txn(transaction: Transaction) -> None:
        # All reads must happen before any writes within a Firestore txn.
        username_snap = username_ref.get(transaction=transaction)
        phone_snap = phone_ref.get(transaction=transaction) if phone_ref else None
        user_snap = user_ref.get(transaction=transaction)

        if username_snap.exists:
            owner_uid = (username_snap.to_dict() or {}).get("uid")
            if owner_uid != uid:
                raise UsernameTaken(username)
                
        if phone_snap and phone_snap.exists:
            owner_uid = (phone_snap.to_dict() or {}).get("uid")
            if owner_uid != uid:
                raise PhoneNumberTaken(phone_number)

        if user_snap.exists:
            raise AlreadyOnboarded(uid)

        now = firestore.SERVER_TIMESTAMP
        transaction.set(
            username_ref,
            {"username": username, "uid": uid, "reserved_at": now},
        )
        if phone_ref:
            transaction.set(
                phone_ref,
                {"phone_number": phone_number, "uid": uid, "reserved_at": now},
            )
        transaction.set(
            user_ref,
            {
                "uid": uid,
                "email": email or "",
                "phone_number": phone_number,
                "username": username,
                "display_name": display_name,
                "dob": dob,
                "bio": bio,
                "photo_url": photo_url,
                "follower_count": 0,
                "following_count": 0,
                "post_count": 0,
                "is_verified": False,
                "is_suspended": False,
                "created_at": now,
                "updated_at": now,
                "last_active_at": now,
                "private_account": False,
                "allow_messages_from": "everyone",
                "safechat_protection": True,
                "schema_version": 1,
            },
        )

    await asyncio.to_thread(_txn, db.transaction())

    # Refetch so the returned model carries resolved server timestamps
    # rather than the SERVER_TIMESTAMP sentinel.
    snapshot = await asyncio.to_thread(user_ref.get)
    return UserProfile.model_validate(snapshot.to_dict())


async def get_user_profile(uid: str) -> UserProfile | None:
    """Fetch /users/{uid}. Returns None if the user hasn't onboarded."""
    snapshot = await asyncio.to_thread(_user_ref(uid).get)
    if not snapshot.exists:
        return None
    return UserProfile.model_validate(snapshot.to_dict())


async def get_profile_by_username(username: str) -> UserProfile | None:
    """Resolve username -> uid via /usernames/{username}, then fetch the profile.

    Returns None if the username is unreserved or the user doc is missing.
    """
    normalised = username.strip().lower()
    if not normalised:
        return None

    username_snap = await asyncio.to_thread(_username_ref(normalised).get)
    if not username_snap.exists:
        return None

    uid = (username_snap.to_dict() or {}).get("uid")
    if not uid:
        return None

    return await get_user_profile(str(uid))


async def update_profile(uid: str, fields: dict[str, Any]) -> UserProfile:
    """Apply a partial update to /users/{uid} and return the refreshed profile.

    `updated_at` is always bumped to the server timestamp. Raises
    ProfileNotFound if the user has no profile document.
    """
    user_ref = _user_ref(uid)

    new_username = fields.pop("username", None)
    if new_username:
        await change_username(uid, new_username)

    # Drop None values — clients may send explicit nulls for unchanged fields,
    # which must not overwrite existing Firestore data.
    update_fields = {k: v for k, v in fields.items() if v is not None}

    if not update_fields:
        snapshot = await asyncio.to_thread(user_ref.get)
        return UserProfile.model_validate(snapshot.to_dict())

    payload: dict[str, Any] = {**update_fields, "updated_at": firestore.SERVER_TIMESTAMP}

    try:
        await asyncio.to_thread(lambda: user_ref.update(payload))
    except NotFound as exc:
        raise ProfileNotFound(uid) from exc

    snapshot = await asyncio.to_thread(user_ref.get)
    return UserProfile.model_validate(snapshot.to_dict())

async def change_username(uid: str, new_username: str) -> None:
    """Safely change a user's username using a transaction."""
    # Defense-in-depth (CQ-05): enforce the charset rule even if a caller
    # bypasses the request-model validation.
    new_username = new_username.strip().lower()
    if not USERNAME_PATTERN.match(new_username):
        raise ValueError(
            "Username must be 3-30 characters: lowercase letters, "
            "digits, and underscores only."
        )

    user_ref = _user_ref(uid)
    new_username_ref = _username_ref(new_username)
    
    @firestore.transactional
    def _txn(transaction: Transaction) -> None:
        user_snap = user_ref.get(transaction=transaction)
        if not user_snap.exists:
            raise ProfileNotFound(uid)
            
        user_data = user_snap.to_dict() or {}
        old_username = user_data.get("username")
        
        if old_username == new_username:
            return
            
        # Enforce 30 day limit if username_changed_at exists
        changed_at = user_data.get("username_changed_at")
        if changed_at:
            # Check if 30 days have passed
            from datetime import datetime, timezone, timedelta
            if isinstance(changed_at, datetime):
                # Using UTC
                if datetime.now(timezone.utc) - changed_at < timedelta(days=30):
                    raise ValueError("Username can only be changed once every 30 days.")
            else:
                # Firestore Timestamp
                if datetime.now(timezone.utc) - changed_at.replace(tzinfo=timezone.utc) < timedelta(days=30):
                    raise ValueError("Username can only be changed once every 30 days.")
                    
        new_username_snap = new_username_ref.get(transaction=transaction)
        if new_username_snap.exists:
            owner_uid = (new_username_snap.to_dict() or {}).get("uid")
            if owner_uid != uid:
                raise UsernameTaken(new_username)
                
        # Perform the swap
        now = firestore.SERVER_TIMESTAMP
        transaction.set(new_username_ref, {"username": new_username, "uid": uid, "reserved_at": now})
        if old_username:
            transaction.delete(_username_ref(old_username))
            
        # Update user profile
        change_count = user_data.get("username_change_count", 0) + 1
        transaction.update(user_ref, {
            "username": new_username,
            "username_changed_at": now,
            "username_change_count": change_count,
            "updated_at": now
        })
        
    await asyncio.to_thread(_txn, db.transaction())


async def search_users(query: str, limit: int) -> list[dict[str, Any]]:
    """Prefix-search users by username (case-insensitive).

    Usernames are stored lowercase, so the standard Firestore range trick
    (`>= q` and `< q + '\\uf8ff'`) gives a case-insensitive prefix match.
    An empty query returns an empty list without touching Firestore.
    """
    normalised = query.strip().lower()
    if not normalised:
        return []

    def _run_search() -> list[dict[str, Any]]:
        end = normalised + ""
        firestore_query = (
            db.collection("users")
            .where(filter=FieldFilter("username", ">=", normalised))
            .where(filter=FieldFilter("username", "<", end))
            .limit(limit)
        )
        results: list[dict[str, Any]] = []
        for snapshot in firestore_query.stream():
            data = snapshot.to_dict() or {}
            results.append(
                UserSearchResult(
                    uid=str(data.get("uid", "")),
                    username=str(data.get("username", "")),
                    display_name=str(data.get("display_name", "")),
                    photo_url=data.get("photo_url"),
                ).model_dump()
            )
        return results

    return await asyncio.to_thread(_run_search)


async def register_device_token(uid: str, token: str) -> None:
    """Store the user's FCM device token at /fcm_tokens/{uid}.

    SEC-06: this MUST be the same location the push sender reads
    (services/notifications.py::send_message_notification reads
    fcm_tokens/{recipient_uid}). The previous implementation appended to a
    `users/{uid}.fcm_tokens` array that nothing ever read, so push
    notifications silently never fired.

    Single token per user, last write wins — the most recently registered
    device receives pushes.
    """
    payload = {
        "uid": uid,
        "token": token,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    def _write() -> None:
        db.collection("fcm_tokens").document(uid).set(payload, merge=True)

    await asyncio.to_thread(_write)


async def get_blocked_users_profiles(uid: str) -> list[dict[str, Any]]:
    """Return UserSearchResult dicts for blocked users."""
    from services import blocks as blocks_service
    blocked_uids = await blocks_service.get_blocked_users(uid)
    if not blocked_uids:
        return []
        
    def _query() -> list[dict[str, Any]]:
        results = []
        for b_uid in blocked_uids:
            snap = _user_ref(b_uid).get()
            if snap.exists:
                data = snap.to_dict() or {}
                results.append(
                    UserSearchResult(
                        uid=str(data.get("uid", "")),
                        username=str(data.get("username", "")),
                        display_name=str(data.get("display_name", "")),
                        photo_url=data.get("photo_url"),
                    ).model_dump()
                )
        return results
    return await asyncio.to_thread(_query)


async def get_suggested_users(uid: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return suggested UserProfile dicts.
    
    Ranking ideally incorporates Trust Level, Reputation Score, and Activity.
    For Phase B Beta, we pull recent active users not followed or blocked.
    """
    from services import blocks as blocks_service
    from services import follows as follows_service
    
    blocked_uids = await blocks_service.get_blocked_users(uid)
    following_uids = await follows_service.get_following(uid)
    exclude_uids = set(blocked_uids) | set(following_uids) | {uid}
    
    def _query() -> list[dict[str, Any]]:
        q = (
            db.collection("users")
            .order_by("last_active_at", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        results = []
        for snap in q.stream():
            data = snap.to_dict() or {}
            c_uid = str(data.get("uid", ""))
            if c_uid not in exclude_uids:
                results.append(UserProfile.model_validate(data).model_dump(mode="json"))
            if len(results) >= limit:
                break
        return results
        
    return await asyncio.to_thread(_query)
