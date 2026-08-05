// frontend/lib/features/chat/presentation/presence_provider.dart
import 'dart:async';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../auth/presentation/auth_provider.dart';

class PresenceState {
  final bool isTyping;
  final bool isViewing;
  final DateTime? lastActive;

  const PresenceState({
    this.isTyping = false,
    this.isViewing = false,
    this.lastActive,
  });
}

final chatPresenceProvider = StreamProvider.family<Map<String, PresenceState>, String>((ref, chatId) {
  final currentUid = ref.watch(authStateProvider).user?.uid;
  if (chatId.isEmpty || currentUid == null) {
    return Stream.value({});
  }

  return FirebaseFirestore.instance
      .collection('chats')
      .doc(chatId)
      .collection('presence')
      .snapshots()
      .map((snapshot) {
    final map = <String, PresenceState>{};
    for (final doc in snapshot.docs) {
      final uid = doc.id;
      if (uid == currentUid) continue; // ignore self
      final data = doc.data();
      map[uid] = PresenceState(
        isTyping: data['is_typing'] == true,
        isViewing: data['is_viewing'] == true,
        lastActive: (data['last_active'] as Timestamp?)?.toDate(),
      );
    }
    return map;
  });
});

final presenceControllerProvider = Provider((ref) => PresenceController(ref));

class PresenceController {
  final Ref _ref;
  Timer? _stopTypingTimer;
  DateTime? _lastTypingWriteTime;

  PresenceController(this._ref);

  Future<void> setPresence(String chatId, {required bool isTyping, required bool isViewing}) async {
    if (chatId.isEmpty) return;
    final currentUid = _ref.read(authStateProvider).user?.uid;
    if (currentUid == null) return;

    try {
      // Direct Firestore write: 0ms latency and 0 backend server load
      await FirebaseFirestore.instance
          .collection('chats')
          .doc(chatId)
          .collection('presence')
          .doc(currentUid)
          .set({
        'uid': currentUid,
        'is_typing': isTyping,
        'is_viewing': isViewing,
        'last_active': FieldValue.serverTimestamp(),
      }, SetOptions(merge: true));
    } catch (_) {}
  }

  void onTextChanged(String chatId, String text) {
    if (_stopTypingTimer?.isActive ?? false) _stopTypingTimer?.cancel();
    
    final isTypingNow = text.trim().isNotEmpty;
    final now = DateTime.now();

    // Throttle writes: only send 'is_typing: true' once every 2.5s max while actively typing
    if (isTypingNow) {
      if (_lastTypingWriteTime == null ||
          now.difference(_lastTypingWriteTime!) > const Duration(milliseconds: 2500)) {
        _lastTypingWriteTime = now;
        setPresence(chatId, isTyping: true, isViewing: true);
      }

      // Schedule turning off typing indicator 2.5s after user stops typing
      _stopTypingTimer = Timer(const Duration(milliseconds: 2500), () {
        _lastTypingWriteTime = null;
        setPresence(chatId, isTyping: false, isViewing: true);
      });
    } else {
      _lastTypingWriteTime = null;
      setPresence(chatId, isTyping: false, isViewing: true);
    }
  }
}
