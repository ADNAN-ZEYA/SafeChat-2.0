import 'dart:async';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/comment_model.dart';
import '../data/post_repository.dart';
import 'feed_provider.dart';

final commentsProvider =
    AsyncNotifierProvider.family<CommentsNotifier, List<Comment>, String>(
      (arg) => CommentsNotifier(arg),
    );

/// Real-time stream of approved comments for a post directly from Firestore.
final approvedCommentsStreamProvider =
    StreamProvider.family<List<Comment>, String>((ref, postId) {
  return FirebaseFirestore.instance
      .collection('posts')
      .doc(postId)
      .collection('comments')
      .where('status', isEqualTo: 'approved')
      .orderBy('created_at', descending: false)
      .snapshots()
      .map((snapshot) {
    return snapshot.docs.map((doc) {
      final data = doc.data();
      DateTime? createdAt;
      if (data['created_at'] is Timestamp) {
        createdAt = (data['created_at'] as Timestamp).toDate();
      } else if (data['created_at'] != null) {
        createdAt = DateTime.tryParse(data['created_at'].toString());
      }
      return Comment(
        id: doc.id,
        postId: data['post_id'] as String? ?? postId,
        authorUid: data['author_uid'] as String? ?? '',
        authorDisplayName:
            data['author_display_name'] as String? ?? 'Anonymous',
        authorUsername: data['author_username'] as String? ?? 'unknown',
        authorPhotoUrl: data['author_photo_url'] as String? ?? '',
        text: data['text'] as String? ?? '',
        parentCommentId: data['parent_comment_id'] as String?,
        likeCount: data['like_count'] as int? ?? 0,
        isLiked: false,
        createdAt: createdAt,
      );
    }).toList();
  });
});

class CommentsNotifier extends AsyncNotifier<List<Comment>> {
  final String arg;
  CommentsNotifier(this.arg);

  @override
  FutureOr<List<Comment>> build() async {
    return ref.read(postRepositoryProvider).getComments(arg);
  }

  /// Post a comment. On success it's appended to the thread. Throws
  /// [FlaggedContentException] (without disturbing the thread) when flagged, so
  /// the UI can show the popup; the state is left intact for a retry.
  Future<void> createComment(String text, {String? parentCommentId}) async {
    final repo = ref.read(postRepositoryProvider);
    final newComment = await repo.createComment(
      arg,
      text,
      parentCommentId: parentCommentId,
    );
    if (state.hasValue) {
      state = AsyncValue.data([...state.value!, newComment]);
    }
    ref.invalidate(feedPostsProvider('global'));
    ref.invalidate(feedPostsProvider('following'));
  }

  /// Re-submit a flagged comment for human verification. The resulting
  /// pending_review comment stays hidden from the thread (shown in Appeals),
  /// so it is intentionally not appended here.
  Future<void> submitCommentForReview(
    String text, {
    String? parentCommentId,
  }) async {
    final repo = ref.read(postRepositoryProvider);
    await repo.createComment(
      arg,
      text,
      parentCommentId: parentCommentId,
      submitForReview: true,
    );
  }

  Future<void> deleteComment(String commentId) async {
    final repo = ref.read(postRepositoryProvider);
    try {
      await repo.deleteComment(arg, commentId);
      if (state.hasValue) {
        state = AsyncValue.data(
          state.value!.where((c) => c.id != commentId).toList(),
        );
      }
      ref.invalidate(feedPostsProvider('global'));
      ref.invalidate(feedPostsProvider('following'));
    } catch (e) {
      rethrow;
    }
  }

  Future<void> likeComment(String commentId) async {
    if (!state.hasValue) return;

    // Optimistic update
    final comments = state.value!;
    final index = comments.indexWhere((c) => c.id == commentId);
    if (index == -1) return;

    final oldComment = comments[index];
    if (oldComment.isLiked) return;

    final newComments = List<Comment>.from(comments);
    newComments[index] = oldComment.copyWith(
      isLiked: true,
      likeCount: oldComment.likeCount + 1,
    );
    state = AsyncValue.data(newComments);

    try {
      await ref.read(postRepositoryProvider).likeComment(arg, commentId);
    } catch (e) {
      // Revert
      newComments[index] = oldComment;
      state = AsyncValue.data(newComments);
      rethrow;
    }
  }

  Future<void> unlikeComment(String commentId) async {
    if (!state.hasValue) return;

    // Optimistic update
    final comments = state.value!;
    final index = comments.indexWhere((c) => c.id == commentId);
    if (index == -1) return;

    final oldComment = comments[index];
    if (!oldComment.isLiked) return;

    final newComments = List<Comment>.from(comments);
    newComments[index] = oldComment.copyWith(
      isLiked: false,
      likeCount: (oldComment.likeCount - 1).clamp(0, 999999),
    );
    state = AsyncValue.data(newComments);

    try {
      await ref.read(postRepositoryProvider).unlikeComment(arg, commentId);
    } catch (e) {
      // Revert
      newComments[index] = oldComment;
      state = AsyncValue.data(newComments);
      rethrow;
    }
  }
}
