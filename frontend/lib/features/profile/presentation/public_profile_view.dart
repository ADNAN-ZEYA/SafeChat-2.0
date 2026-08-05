// frontend/lib/features/profile/presentation/public_profile_view.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/dio_client.dart';
import '../../../shared/widgets/dp_viewer.dart';
import '../../../shared/widgets/firebase_image.dart';
import '../../home/data/feed_post_model.dart';
import '../data/follow_repository.dart';
import 'follow_providers.dart';
import 'user_posts_provider.dart';
import '../../chat/presentation/chat_detail_view.dart';

final publicProfileProvider =
    FutureProvider.family<Map<String, dynamic>, String>((ref, username) async {
      final dio = ref.watch(dioProvider);
      final response = await dio.get('/api/v1/users/$username');
      return response.data['data'] as Map<String, dynamic>;
    });

class PublicProfileView extends ConsumerWidget {
  final String uid;
  final String username;

  const PublicProfileView({
    super.key,
    required this.uid,
    required this.username,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profileAsync = ref.watch(publicProfileProvider(username));

    return profileAsync.when(
      loading: () => Scaffold(
        appBar: AppBar(title: Text('@$username')),
        body: const Center(child: CircularProgressIndicator()),
      ),
      error: (e, _) => Scaffold(
        appBar: AppBar(title: Text('@$username')),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.person_off_outlined, size: 64),
              const SizedBox(height: 16),
              const Text('Could not load profile'),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: () =>
                    ref.invalidate(publicProfileProvider(username)),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
      ),
      data: (data) => _ProfileBody(uid: uid, username: username, data: data),
    );
  }
}

class _ProfileBody extends ConsumerWidget {
  final String uid;
  final String username;
  final Map<String, dynamic> data;

  const _ProfileBody({
    required this.uid,
    required this.username,
    required this.data,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final photoUrl = data['photo_url'] as String? ?? '';
    final displayName = data['display_name'] as String? ?? username;
    final bio = data['bio'] as String? ?? '';
    final followerCount = data['follower_count'] as int? ?? 0;
    final followingCount = data['following_count'] as int? ?? 0;
    final postCount = data['post_count'] as int? ?? 0;

    final isFollowingAsync = ref.watch(isFollowingProvider(uid));
    final postsAsync = ref.watch(userPostsProvider(uid));

    return Scaffold(
      body: CustomScrollView(
        slivers: [
          SliverAppBar(
            title: Text('@$username'),
            pinned: true,
            backgroundColor: Theme.of(context).scaffoldBackgroundColor,
            elevation: 0,
          ),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 24, 20, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  // Avatar — tap to view DP
                  GestureDetector(
                    onTap: photoUrl.isNotEmpty
                        ? () => showDpViewer(context, ref, photoUrl)
                        : null,
                    child: Stack(
                      alignment: Alignment.bottomRight,
                      children: [
                        CircleAvatar(
                          radius: 50,
                          backgroundImage: photoUrl.isNotEmpty
                              ? FirebaseImageProviderWrapper.getProvider(
                                  ref,
                                  photoUrl,
                                )
                              : null,
                          child: photoUrl.isEmpty
                              ? const Icon(Icons.person, size: 50)
                              : null,
                        ),
                        if (photoUrl.isNotEmpty)
                          Container(
                            padding: const EdgeInsets.all(4),
                            decoration: BoxDecoration(
                              color: Theme.of(
                                context,
                              ).colorScheme.surfaceContainer,
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: Theme.of(
                                  context,
                                ).scaffoldBackgroundColor,
                                width: 2,
                              ),
                            ),
                            child: Icon(
                              Icons.zoom_in,
                              size: 14,
                              color: Theme.of(context).colorScheme.onSurface,
                            ),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),

                  // Display name
                  Text(
                    displayName,
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '@$username',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: Theme.of(context).colorScheme.outline,
                    ),
                  ),

                  if (bio.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    Text(bio, textAlign: TextAlign.center),
                  ],
                  const SizedBox(height: 20),

                  // Stats row
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      _Stat(label: 'Posts', value: postCount),
                      _Stat(label: 'Followers', value: followerCount),
                      _Stat(label: 'Following', value: followingCount),
                    ],
                  ),
                  const SizedBox(height: 20),

                  // Follow & Message Action Row
                  Row(
                    children: [
                      Expanded(
                        child: isFollowingAsync.when(
                          data: (following) => following
                              ? OutlinedButton.icon(
                                  style: OutlinedButton.styleFrom(
                                    padding: const EdgeInsets.symmetric(vertical: 12),
                                    side: BorderSide(
                                      color: Theme.of(context).colorScheme.outline.withValues(alpha: 0.4),
                                    ),
                                  ),
                                  onPressed: () => ref
                                      .read(followRepositoryProvider)
                                      .unfollowUser(uid),
                                  icon: const Icon(Icons.check, size: 16, color: Color(0xFF8E2DE2)),
                                  label: const Text('Following'),
                                )
                              : FilledButton.icon(
                                  style: FilledButton.styleFrom(
                                    padding: const EdgeInsets.symmetric(vertical: 12),
                                  ),
                                  onPressed: () => ref
                                      .read(followRepositoryProvider)
                                      .followUser(uid),
                                  icon: const Icon(Icons.person_add, size: 16),
                                  label: const Text('Follow'),
                                ),
                          loading: () => const SizedBox(
                            height: 44,
                            child: Center(child: CircularProgressIndicator()),
                          ),
                          error: (_, _) => const SizedBox.shrink(),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton.icon(
                          style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFF8E2DE2),
                            padding: const EdgeInsets.symmetric(vertical: 12),
                          ),
                          onPressed: () async {
                            final dio = ref.read(dioProvider);
                            try {
                              final res = await dio.post('/api/v1/chats/$uid');
                              final chatData = res.data['data']['chat'] as Map<String, dynamic>;
                              final chatId = chatData['id'] as String;
                              if (context.mounted) {
                                Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) => ChatDetailView(
                                      chatId: chatId,
                                      otherUid: uid,
                                      otherUserName: displayName,
                                    ),
                                  ),
                                );
                              }
                            } catch (e) {
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text('Failed to start chat: $e')),
                                );
                              }
                            }
                          },
                          icon: const Icon(Icons.chat_bubble_outline, size: 18),
                          label: const Text('Message'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  const Divider(),
                ],
              ),
            ),
          ),

          // Posts grid
          postsAsync.when(
            loading: () => const SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator()),
              ),
            ),
            error: (_, _) => const SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: Text('Could not load posts')),
              ),
            ),
            data: (posts) {
              if (posts.isEmpty) {
                return const SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.all(40),
                    child: Center(
                      child: Column(
                        children: [
                          Icon(Icons.photo_library_outlined, size: 48),
                          SizedBox(height: 12),
                          Text('No posts yet'),
                        ],
                      ),
                    ),
                  ),
                );
              }
              return SliverGrid(
                gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 3,
                  mainAxisSpacing: 2,
                  crossAxisSpacing: 2,
                ),
                delegate: SliverChildBuilderDelegate((context, index) {
                  final FeedPost post = posts[index];
                  final thumb = post.displayUrls.isNotEmpty
                      ? post.displayUrls.first
                      : '';
                  return Container(
                    color: Theme.of(
                      context,
                    ).colorScheme.surfaceContainerHighest,
                    child: thumb.isNotEmpty
                        ? FirebaseCachedNetworkImage(
                            imageUrl: thumb,
                            fit: BoxFit.cover,
                            placeholder: (_, _) => const SizedBox.shrink(),
                            errorWidget: (_, _, _) => const Center(
                              child: Icon(Icons.broken_image_outlined),
                            ),
                          )
                        : const Center(child: Icon(Icons.article_outlined)),
                  );
                }, childCount: posts.length),
              );
            },
          ),

          const SliverToBoxAdapter(child: SizedBox(height: 32)),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  final String label;
  final int value;

  const _Stat({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(
          value.toString(),
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 20),
        ),
        const SizedBox(height: 2),
        Text(
          label,
          style: TextStyle(
            fontSize: 13,
            color: Theme.of(context).colorScheme.outline,
          ),
        ),
      ],
    );
  }
}
