import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/encryption_service.dart';

/// Live SafeChat Mode for a chat: "pending" (ON, moderated) or "trusted"
/// (OFF, E2E encrypted). Direct Firestore read — same pattern as the rest of
/// the real-time chat feature (see AGENT.md rule #2).
final chatEncryptionModeProvider = StreamProvider.family<String, String>((
  ref,
  chatId,
) {
  return FirebaseFirestore.instance
      .collection('chats')
      .doc(chatId)
      .snapshots()
      .map((doc) => (doc.data()?['encryption_mode'] as String?) ?? 'pending');
});

/// A user's published E2E public key (base64), or null if they haven't
/// generated one yet (e.g. haven't logged in since this feature shipped).
final userPublicKeyProvider = StreamProvider.family<String?, String>((
  ref,
  uid,
) {
  return FirebaseFirestore.instance
      .collection('users')
      .doc(uid)
      .snapshots()
      .map((doc) => doc.data()?['public_key'] as String?);
});

/// Derives the 1:1 shared encryption key for a chat, reactively (SM-02).
///
/// Watches the peer's published public key, so if they generate or rotate a
/// keypair mid-session the shared key is re-derived automatically — the old
/// one-shot fetch in initState left SafeChat Mode permanently unavailable
/// until the screen was reopened. Returns null while the peer has no key.
final chatSharedKeyProvider =
    FutureProvider.family<SecretKey?, ({String myUid, String otherUid})>((
      ref,
      args,
    ) async {
      final otherPublicKeyB64 = ref
          .watch(userPublicKeyProvider(args.otherUid))
          .value;
      if (otherPublicKeyB64 == null || otherPublicKeyB64.isEmpty) return null;
      return ref
          .read(encryptionServiceProvider)
          .deriveSharedKey(
            myUid: args.myUid,
            otherPublicKeyB64: otherPublicKeyB64,
          );
    });
