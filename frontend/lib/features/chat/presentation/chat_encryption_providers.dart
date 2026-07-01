import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
