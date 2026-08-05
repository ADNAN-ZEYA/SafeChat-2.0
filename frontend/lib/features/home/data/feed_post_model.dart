// frontend/lib/features/home/data/feed_post_model.dart

class FeedPost {
  final String id;
  final String authorUid;
  final String authorUsername;
  final String authorDisplayName;
  final String authorPhotoUrl;
  final String text;
  final String? imageUrl;
  final List<String> mediaUrls;
  final String mediaType;
  final String status;
  final int likeCount;
  final int commentCount;
  final int viewCount;
  final DateTime? createdAt;

  const FeedPost({
    required this.id,
    required this.authorUid,
    required this.authorUsername,
    required this.authorDisplayName,
    required this.authorPhotoUrl,
    required this.text,
    this.imageUrl,
    this.mediaUrls = const [],
    this.mediaType = 'text',
    this.status = 'approved',
    this.likeCount = 0,
    this.commentCount = 0,
    this.viewCount = 0,
    this.createdAt,
  });

  factory FeedPost.fromJson(Map<String, dynamic> json) {
    return FeedPost(
      id: json['id'] as String? ?? '',
      authorUid: json['author_uid'] as String? ?? '',
      authorUsername: json['author_username'] as String? ?? 'unknown',
      authorDisplayName: json['author_display_name'] as String? ?? 'Anonymous',
      authorPhotoUrl: json['author_photo_url'] as String? ?? '',
      text: json['text'] as String? ?? '',
      imageUrl: json['image_url'] as String?,
      mediaUrls:
          (json['media_urls'] as List<dynamic>?)
              ?.map((e) => e as String)
              .toList() ??
          [],
      mediaType: json['media_type'] as String? ?? 'text',
      status: json['status'] as String? ?? 'approved',
      likeCount: json['like_count'] as int? ?? 0,
      commentCount: json['comment_count'] as int? ?? 0,
      viewCount: json['view_count'] as int? ?? 0,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'].toString())
          : null,
    );
  }

  factory FeedPost.fromFirestore(Map<String, dynamic> data, String docId) {
    dynamic createdAtData = data['created_at'];
    DateTime? parsedDate;
    if (createdAtData != null) {
      if (createdAtData.runtimeType.toString() == 'Timestamp') {
        // Hack to avoid importing cloud_firestore if we don't want to couple the model
        parsedDate = createdAtData.toDate();
      } else {
        parsedDate = DateTime.tryParse(createdAtData.toString());
      }
    }

    return FeedPost(
      id: docId,
      authorUid: data['author_uid'] as String? ?? '',
      authorUsername: data['author_username'] as String? ?? 'unknown',
      authorDisplayName: data['author_display_name'] as String? ?? 'Anonymous',
      authorPhotoUrl: data['author_photo_url'] as String? ?? '',
      text: data['text'] as String? ?? data['caption'] as String? ?? '',
      imageUrl: data['image_url'] as String?,
      mediaUrls:
          (data['media_urls'] as List<dynamic>?)
              ?.map((e) => e as String)
              .toList() ??
          [],
      mediaType: data['media_type'] as String? ?? 'text',
      status: data['status'] as String? ?? 'approved',
      likeCount: data['like_count'] as int? ?? 0,
      commentCount: data['comment_count'] as int? ?? 0,
      viewCount: data['view_count'] as int? ?? 0,
      createdAt: parsedDate,
    );
  }

  /// All displayable image/video URLs, falling back to image_url for backward compat.
  List<String> get displayUrls {
    if (mediaUrls.isNotEmpty) {
      return mediaUrls.where((e) => e.isNotEmpty).toList();
    }
    if (imageUrl != null && imageUrl!.isNotEmpty) return [imageUrl!];
    return [];
  }

  /// True when this post is still being reviewed by a human moderator.
  bool get isPendingReview => status == 'pending_review';

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'author_uid': authorUid,
      'author_username': authorUsername,
      'author_display_name': authorDisplayName,
      'author_photo_url': authorPhotoUrl,
      'text': text,
      'image_url': imageUrl,
      'media_urls': mediaUrls,
      'media_type': mediaType,
      'status': status,
      'like_count': likeCount,
      'comment_count': commentCount,
      'view_count': viewCount,
      'created_at': createdAt?.toIso8601String(),
    };
  }
}
