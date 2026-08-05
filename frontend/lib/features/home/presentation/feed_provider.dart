import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/feed_post_model.dart';
import '../data/post_repository.dart';

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
  Future<List<FeedPost>> build() => _fetch();

  Future<List<FeedPost>> _fetch() async {
    return ref.read(postRepositoryProvider).getFeed(type: arg);
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }

  void prependOptimistic(FeedPost post) {
    final current = state.asData?.value ?? [];
    state = AsyncData([post, ...current]);
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
