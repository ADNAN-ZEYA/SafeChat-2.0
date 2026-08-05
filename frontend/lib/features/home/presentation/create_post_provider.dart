import 'dart:io';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../moderation/data/moderation_models.dart';
import '../data/post_repository.dart';
import 'feed_provider.dart';

/// The result of submitting a post — passed back to the UI.
enum SubmitOutcome {
  /// Clean content — live in the feed.
  approved,

  /// Submitted for human verification — waiting in the review queue.
  pendingReview,

  /// Flagged by the safety filter — the UI should show the highlighted popup.
  flagged,
}

class CreatePostState {
  final List<File> selectedMedia;
  final String caption;
  final bool isSimpleMode;
  final AsyncValue<void> submissionState;

  /// Flagged spans from the last submit attempt (drives the popup highlight).
  final List<ModerationMatch> flaggedMatches;

  CreatePostState({
    this.selectedMedia = const [],
    this.caption = '',
    this.isSimpleMode = false,
    this.submissionState = const AsyncData(null),
    this.flaggedMatches = const [],
  });

  CreatePostState copyWith({
    List<File>? selectedMedia,
    String? caption,
    bool? isSimpleMode,
    AsyncValue<void>? submissionState,
    List<ModerationMatch>? flaggedMatches,
  }) {
    return CreatePostState(
      selectedMedia: selectedMedia ?? this.selectedMedia,
      caption: caption ?? this.caption,
      isSimpleMode: isSimpleMode ?? this.isSimpleMode,
      submissionState: submissionState ?? this.submissionState,
      flaggedMatches: flaggedMatches ?? this.flaggedMatches,
    );
  }
}

class CreatePostNotifier extends Notifier<CreatePostState> {
  @override
  CreatePostState build() => CreatePostState();

  void setMode(bool isSimple) => state = state.copyWith(isSimpleMode: isSimple);

  void setCaption(String text) => state = state.copyWith(caption: text);

  void addMedia(List<File> files) {
    final newList = List<File>.from(state.selectedMedia)..addAll(files);
    state = state.copyWith(selectedMedia: newList.take(5).toList());
  }

  void removeMedia(int index) {
    final newList = List<File>.from(state.selectedMedia)..removeAt(index);
    state = state.copyWith(selectedMedia: newList);
  }

  /// Attempt to post. Returns:
  ///   * [SubmitOutcome.approved] / [SubmitOutcome.pendingReview] on success,
  ///   * [SubmitOutcome.flagged] when flagged (see [state.flaggedMatches]),
  ///   * null on unexpected failure (see submissionState.error).
  Future<SubmitOutcome?> submitPost() async {
    return _send(submitForReview: false);
  }

  /// Re-submit the same content, knowingly opting into human verification.
  Future<SubmitOutcome?> confirmHumanVerification() async {
    return _send(submitForReview: true);
  }

  Future<SubmitOutcome?> _send({required bool submitForReview}) async {
    if (state.selectedMedia.isEmpty) {
      state = state.copyWith(
        submissionState: AsyncError(
          Exception('Posts cannot be text-only. Please select an image or video to post.'),
          StackTrace.current,
        ),
      );
      return null;
    }
    state = state.copyWith(submissionState: const AsyncLoading());
    try {
      final repo = ref.read(postRepositoryProvider);
      final result = await repo.createPostWithMedia(
        caption: state.caption,
        mediaFiles: state.selectedMedia,
        submitForReview: submitForReview,
      );

      state = state.copyWith(
        submissionState: const AsyncData(null),
        flaggedMatches: const [],
      );

      // Refresh the feed so the new post appears immediately.
      ref.invalidate(feedPostsProvider('global'));
      ref.invalidate(feedPostsProvider('following'));

      return result == PostSubmitResult.pendingReview
          ? SubmitOutcome.pendingReview
          : SubmitOutcome.approved;
    } on FlaggedContentException catch (e) {
      // Not an error — the user gets the highlighted popup + a review option.
      state = state.copyWith(
        submissionState: const AsyncData(null),
        flaggedMatches: e.matches,
      );
      return SubmitOutcome.flagged;
    } catch (e, st) {
      state = state.copyWith(submissionState: AsyncError(e, st));
      return null;
    }
  }

  void reset() => state = CreatePostState();
}

final createPostProvider =
    NotifierProvider<CreatePostNotifier, CreatePostState>(() {
      return CreatePostNotifier();
    });
