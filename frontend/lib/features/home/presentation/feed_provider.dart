import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/feed_post_model.dart';
import '../data/post_repository.dart';

import '../data/feed_cache_service.dart';

/// Fetches the feed via the backend API so media URLs are signed before
/// reaching the client. Reading directly from Firestore returns raw GCS URLs
/// that the private bucket will reject with 403.
final feedPostsProvider =
    AsyncNotifierProvider.family<FeedPostsNotifier, List<FeedPost>, String>(
      (arg) => FeedPostsNotifier(arg),
    );

class FeedPostsNotifier extends AsyncNotifier<List<FeedPost>> {
  final String arg;

  FeedPostsNotifier(this.arg);

  @override
  Future<List<FeedPost>> build() async {
    // 1. Instantly return cached feed if available for zero-latency UI boot
    final cached = FeedCacheService.getCachedFeed(arg);
    if (cached != null && cached.isNotEmpty) {
      // Trigger background update
      Future.microtask(() => _fetchAndUpdateCache());
      return cached;
    }

    // 2. Initial fetch if no cache exists
    return _fetchAndUpdateCache();
  }

  Future<List<FeedPost>> _fetchAndUpdateCache() async {
    try {
      final posts = await ref.read(postRepositoryProvider).getFeed(type: arg);
      state = AsyncData(posts);
      await FeedCacheService.cacheFeed(arg, posts);
      return posts;
    } catch (e, st) {
      if (state.hasValue && state.value!.isNotEmpty) {
        // Keep showing cached state on error
        return state.value!;
      }
      state = AsyncError(e, st);
      rethrow;
    }
  }

  Future<void> refresh() async {
    state = await AsyncValue.guard(() => _fetchAndUpdateCache());
  }

  void prependOptimistic(FeedPost post) {
    final current = state.asData?.value ?? [];
    final updated = [post, ...current];
    state = AsyncData(updated);
    FeedCacheService.cacheFeed(arg, updated);
  }
}

/// Live stats for a post directly from Firestore (like_count, comment_count, view_count).
final postStatsProvider =
    StreamProvider.family<Map<String, dynamic>?, String>((ref, postId) {
  return FirebaseFirestore.instance
      .collection('posts')
      .doc(postId)
      .snapshots()
      .map((snap) => snap.data());
});
