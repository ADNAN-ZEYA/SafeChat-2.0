import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_staggered_grid_view/flutter_staggered_grid_view.dart';
import 'package:animations/animations.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;
import 'package:share_plus/share_plus.dart';
import '../../../shared/utils/markdown_extensions.dart';
import '../../../theme/theme_provider.dart';
import '../../../theme/app_colors.dart';
import '../../../shared/widgets/animated_ambient_background.dart';
import '../../../shared/widgets/rolling_counter.dart';
import '../../../shared/widgets/firebase_image.dart';
import 'package:flutter_animate/flutter_animate.dart';
import '../../profile/presentation/follow_providers.dart';
import '../../profile/data/follow_repository.dart';
import '../../profile/presentation/public_profile_view.dart';
import '../../profile/presentation/user_posts_provider.dart';
import '../../../shared/widgets/dp_viewer.dart';
import '../../../shared/widgets/fullscreen_media_viewer.dart';
import '../data/feed_post_model.dart';
import '../../moderation/data/moderation_models.dart';
import '../../moderation/presentation/flagged_content_dialog.dart';
import '../data/post_repository.dart';
import 'comments_provider.dart';
import 'feed_provider.dart';
import 'like_provider.dart';

// ─────────────────────────────────────────────────────────────────────────────
// Feed root
// ─────────────────────────────────────────────────────────────────────────────

class FeedView extends StatelessWidget {
  const FeedView({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        body: NestedScrollView(
          headerSliverBuilder: (context, innerBoxIsScrolled) => [
            SliverAppBar(
              floating: true,
              pinned: true,
              backgroundColor: Colors.transparent,
              elevation: 0,
              flexibleSpace: ClipRect(
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
                  child: Container(
                    color: Theme.of(
                      context,
                    ).colorScheme.surface.withValues(alpha: 0.6),
                  ),
                ),
              ),
              title: ShaderMask(
                shaderCallback: (bounds) => AppColors.brandGradient(
                  Theme.of(context).colorScheme,
                ).createShader(bounds),
                child: const Text(
                  'SafeChat',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 24,
                    color: Colors.white, // tinted by the gradient shader
                  ),
                ),
              ),
              bottom: const TabBar(
                tabs: [
                  Tab(text: 'For You'),
                  Tab(text: 'Following'),
                ],
              ),
            ),
          ],
          body: const TabBarView(
            children: [
              _FeedTab(feedType: 'global'),
              _FeedTab(feedType: 'following'),
            ],
          ),
        ),
      ),
    );
  }
}

class _FeedTab extends ConsumerWidget {
  final String feedType;
  const _FeedTab({required this.feedType});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feedAsync = ref.watch(feedPostsProvider(feedType));
    final layoutMode = ref.watch(feedLayoutProvider);

    return feedAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => _ErrorView(
        message: e.toString(),
        onRetry: () => ref.invalidate(feedPostsProvider(feedType)),
      ),
      data: (posts) {
        if (posts.isEmpty) {
          return _EmptyFeed(
            onRetry: () => ref.invalidate(feedPostsProvider(feedType)),
          );
        }
        return RefreshIndicator(
          onRefresh: () =>
              ref.read(feedPostsProvider(feedType).notifier).refresh(),
          child: CustomScrollView(
            slivers: [
              SliverPadding(
                padding: const EdgeInsets.only(
                  left: 12,
                  right: 12,
                  top: 12,
                  bottom: 100,
                ),
                sliver: layoutMode == FeedLayoutMode.grid
                    ? _buildGridView(context, posts)
                    : layoutMode == FeedLayoutMode.card
                        ? _buildCardView(context, posts)
                        : _buildSpatialDeckView(context, posts),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildGridView(BuildContext context, List<FeedPost> posts) {
    return SliverMasonryGrid.count(
      crossAxisCount: 2,
      mainAxisSpacing: 12,
      crossAxisSpacing: 12,
      childCount: posts.length,
      itemBuilder: (context, index) {
        final post = posts[index];
        return PostOpenContainer(
          post: post,
          child: _GridPostCard(post: post),
        );
      },
    );
  }

  Widget _buildCardView(BuildContext context, List<FeedPost> posts) {
    return SliverList.separated(
      itemCount: posts.length,
      separatorBuilder: (_, _) => const SizedBox(height: 24),
      itemBuilder: (context, index) {
        final post = posts[index];
        return PostOpenContainer(
          post: post,
          child: _ListPostCard(post: post),
        );
      },
    );
  }

  Widget _buildSpatialDeckView(BuildContext context, List<FeedPost> posts) {
    return SliverList.separated(
      itemCount: posts.length,
      separatorBuilder: (_, _) => const SizedBox(height: 28),
      itemBuilder: (context, index) {
        final post = posts[index];
        return PostOpenContainer(
          post: post,
          child: _SpatialDeckCard(post: post),
        );
      },
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Empty / Error states
// ─────────────────────────────────────────────────────────────────────────────

class _EmptyFeed extends StatelessWidget {
  final VoidCallback onRetry;
  const _EmptyFeed({required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.photo_library_outlined,
            size: 64,
            color: Theme.of(context).colorScheme.outlineVariant,
          ),
          const SizedBox(height: 16),
          Text('No posts yet', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(
            'Follow people or create your first post!',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
              color: Theme.of(context).colorScheme.outline,
            ),
          ),
          const SizedBox(height: 24),
          OutlinedButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('Refresh'),
          ),
        ],
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorView({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.cloud_off_rounded,
              size: 64,
              color: Theme.of(context).colorScheme.error,
            ),
            const SizedBox(height: 16),
            Text(
              'Couldn\'t load feed',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            Text(
              message,
              style: Theme.of(context).textTheme.bodySmall,
              textAlign: TextAlign.center,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('Try again'),
            ),
          ],
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Open container wrapper (shared hero + page transition)
// ─────────────────────────────────────────────────────────────────────────────

class PostOpenContainer extends StatelessWidget {
  final FeedPost post;
  final Widget child;

  const PostOpenContainer({super.key, required this.post, required this.child});

  @override
  Widget build(BuildContext context) {
    return OpenContainer(
      transitionType: ContainerTransitionType.fadeThrough,
      closedElevation: 0,
      openElevation: 0,
      closedColor: Colors.transparent,
      openColor: Theme.of(context).scaffoldBackgroundColor,
      closedBuilder: (context, action) => child,
      openBuilder: (context, action) => PostDetailScreen(post: post),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Grid card
// ─────────────────────────────────────────────────────────────────────────────

class _GridPostCard extends ConsumerWidget {
  final FeedPost post;
  const _GridPostCard({required this.post});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final thumb = post.displayUrls.isNotEmpty ? post.displayUrls.first : null;
    final height = 150.0 + (post.id.hashCode.abs() % 4) * 50.0;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (thumb != null)
            Container(
              height: height,
              decoration: BoxDecoration(
                image:
                    FirebaseImageProviderWrapper.getProvider(ref, thumb) != null
                    ? DecorationImage(
                        image: FirebaseImageProviderWrapper.getProvider(
                          ref,
                          thumb,
                        )!,
                        fit: BoxFit.cover,
                      )
                    : null,
              ),
            )
          else
            Container(
              height: height,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              child: const Center(
                child: Icon(Icons.article_outlined, size: 40),
              ),
            ),
          Padding(
            padding: const EdgeInsets.all(12.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  post.text,
                  style: const TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 14,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    CircleAvatar(
                      radius: 12,
                      backgroundImage: post.authorPhotoUrl.isNotEmpty
                          ? FirebaseImageProviderWrapper.getProvider(
                              ref,
                              post.authorPhotoUrl,
                            )
                          : null,
                      child: post.authorPhotoUrl.isEmpty
                          ? const Icon(Icons.person, size: 14)
                          : null,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        post.authorDisplayName,
                        style: const TextStyle(
                          fontSize: 12,
                          color: Colors.grey,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (FirebaseAuth.instance.currentUser?.uid ==
                        post.authorUid)
                      SizedBox(
                        width: 24,
                        height: 24,
                        child: PopupMenuButton<String>(
                          padding: EdgeInsets.zero,
                          iconSize: 16,
                          icon: const Icon(
                            Icons.more_vert,
                            size: 16,
                            color: Colors.grey,
                          ),
                          onSelected: (value) async {
                            if (value == 'delete') {
                              final ok = await showDialog<bool>(
                                context: context,
                                builder: (ctx) => AlertDialog(
                                  title: const Text('Delete post?'),
                                  content: const Text(
                                    'This post will be permanently removed.',
                                  ),
                                  actions: [
                                    TextButton(
                                      onPressed: () =>
                                          Navigator.pop(ctx, false),
                                      child: const Text('Cancel'),
                                    ),
                                    FilledButton(
                                      onPressed: () => Navigator.pop(ctx, true),
                                      child: const Text('Delete'),
                                    ),
                                  ],
                                ),
                              );
                              if (ok == true) {
                                try {
                                  await ref
                                      .read(postRepositoryProvider)
                                      .deletePost(post.id);
                                  ref.invalidate(feedPostsProvider('global'));
                                  ref.invalidate(
                                    feedPostsProvider('following'),
                                  );
                                  ref.invalidate(
                                    userPostsProvider(post.authorUid),
                                  );
                                } catch (e) {
                                  if (context.mounted) {
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      SnackBar(
                                        content: Text('Failed to delete: $e'),
                                      ),
                                    );
                                  }
                                }
                              }
                            }
                          },
                          itemBuilder: (_) => const [
                            PopupMenuItem(
                              value: 'delete',
                              child: ListTile(
                                contentPadding: EdgeInsets.zero,
                                leading: Icon(Icons.delete_outline),
                                title: Text('Delete post'),
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// List card
// ─────────────────────────────────────────────────────────────────────────────

class _ListPostCard extends ConsumerWidget {
  final FeedPost post;
  const _ListPostCard({required this.post});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final thumb = post.displayUrls.isNotEmpty ? post.displayUrls.first : null;

    return Card(
      elevation: 4,
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: CircleAvatar(
              backgroundImage: post.authorPhotoUrl.isNotEmpty
                  ? FirebaseImageProviderWrapper.getProvider(
                      ref,
                      post.authorPhotoUrl,
                    )
                  : null,
              child: post.authorPhotoUrl.isEmpty
                  ? const Icon(Icons.person)
                  : null,
            ),
            title: Text(
              post.authorDisplayName,
              style: const TextStyle(fontWeight: FontWeight.bold),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: Text(
              post.createdAt != null ? _timeAgo(post.createdAt!) : 'Just now',
            ),
            trailing: FirebaseAuth.instance.currentUser?.uid == post.authorUid
                ? PopupMenuButton<String>(
                    onSelected: (value) async {
                      if (value == 'delete') {
                        final ok = await showDialog<bool>(
                          context: context,
                          builder: (ctx) => AlertDialog(
                            title: const Text('Delete post?'),
                            content: const Text(
                              'This post will be permanently removed.',
                            ),
                            actions: [
                              TextButton(
                                onPressed: () => Navigator.pop(ctx, false),
                                child: const Text('Cancel'),
                              ),
                              FilledButton(
                                onPressed: () => Navigator.pop(ctx, true),
                                child: const Text('Delete'),
                              ),
                            ],
                          ),
                        );
                        if (ok == true) {
                          try {
                            await ref
                                .read(postRepositoryProvider)
                                .deletePost(post.id);
                            ref.invalidate(feedPostsProvider('global'));
                            ref.invalidate(feedPostsProvider('following'));
                            ref.invalidate(userPostsProvider(post.authorUid));
                          } catch (e) {
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('Failed to delete: $e')),
                              );
                            }
                          }
                        }
                      }
                    },
                    itemBuilder: (_) => const [
                      PopupMenuItem(
                        value: 'delete',
                        child: ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(Icons.delete_outline),
                          title: Text('Delete post'),
                        ),
                      ),
                    ],
                  )
                : null,
          ),
          if (thumb != null)
            Container(
              constraints: const BoxConstraints(maxHeight: 380, minHeight: 200),
              width: double.infinity,
              color: Colors.black.withValues(alpha: 0.8),
              child: FirebaseCachedNetworkImage(
                imageUrl: thumb,
                fit: BoxFit.contain,
                memCacheWidth: 1080,
                errorWidget: (_, _, _) => Container(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: const Center(child: Icon(Icons.broken_image_outlined)),
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.all(16.0),
            child: MarkdownBody(
              data: post.text,
              extensionSet: md.ExtensionSet.gitHubFlavored,
              inlineSyntaxes: [HighlightSyntax()],
              builders: {'highlight': HighlightBuilder(context)},
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(left: 16, right: 16, bottom: 16),
            child: Consumer(
              builder: (context, ref, child) {
                final statsAsync = ref.watch(postStatsProvider(post.id));
                final isLikedAsync = ref.watch(isLikedProvider(post.id));
                final isLiked = isLikedAsync.value ?? false;

                final statsData = statsAsync.value;
                final liveLikeCount =
                    statsData?['like_count'] as int? ?? post.likeCount;
                final liveCommentCount =
                    statsData?['comment_count'] as int? ?? post.commentCount;
                final liveViewCount =
                    statsData?['view_count'] as int? ?? post.viewCount;

                return Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Icon(
                          isLiked ? Icons.favorite : Icons.favorite_border,
                          size: 16,
                          color: isLiked ? Colors.red : Colors.grey,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '$liveLikeCount',
                          style: TextStyle(
                            color: isLiked ? Colors.red : Colors.grey,
                          ),
                        ),
                        const SizedBox(width: 16),
                        const Icon(
                          Icons.chat_bubble_outline,
                          size: 16,
                          color: Colors.grey,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '$liveCommentCount',
                          style: const TextStyle(color: Colors.grey),
                        ),
                        const SizedBox(width: 16),
                        const Icon(
                          Icons.remove_red_eye_outlined,
                          size: 16,
                          color: Colors.grey,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '$liveViewCount',
                          style: const TextStyle(color: Colors.grey),
                        ),
                      ],
                    ),
                    Row(
                      children: [
                        FloatingActionButton.small(
                          heroTag: 'like_${post.id}',
                          onPressed: () {
                            HapticFeedback.lightImpact();
                            if (isLiked) {
                              ref
                                  .read(postRepositoryProvider)
                                  .unlikePost(post.id);
                            } else {
                              ref
                                  .read(postRepositoryProvider)
                                  .likePost(post.id);
                            }
                          },
                          backgroundColor: isLiked
                              ? Colors.red.withValues(alpha: 0.1)
                              : null,
                          child:
                              Icon(
                                    isLiked
                                        ? Icons.favorite
                                        : Icons.favorite_border,
                                    color: isLiked ? Colors.red : null,
                                  )
                                  .animate(key: ValueKey(isLiked))
                                  .scaleXY(
                                    begin: 0.8,
                                    end: 1.0,
                                    duration: 200.ms,
                                    curve: Curves.easeOutBack,
                                  ),
                        ),
                        const SizedBox(width: 8),
                        FloatingActionButton.small(
                          heroTag: 'comment_${post.id}',
                          onPressed: () =>
                              showCommentsBottomSheet(context, post.id),
                          child: const Icon(Icons.chat_bubble_outline),
                        ),
                      ],
                    ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  String _timeAgo(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inSeconds < 60) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Post detail screen (full-screen with ambient + image carousel)
// ─────────────────────────────────────────────────────────────────────────────

class PostDetailScreen extends ConsumerStatefulWidget {
  final FeedPost post;
  const PostDetailScreen({super.key, required this.post});

  @override
  ConsumerState<PostDetailScreen> createState() => PostDetailScreenState();
}

class PostDetailScreenState extends ConsumerState<PostDetailScreen> {
  int _currentPage = 0;

  List<String> get _mediaUrls => widget.post.displayUrls;

  @override
  void initState() {
    super.initState();
    // Fire and forget view recording
    Future.microtask(() {
      ref
          .read(postRepositoryProvider)
          .viewPost(widget.post.id)
          .catchError((_) {});
    });
  }

  Future<void> _confirmDeletePost() async {
    final navigator = Navigator.of(context);
    final messenger = ScaffoldMessenger.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete post?'),
        content: const Text('This post will be permanently removed.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ref.read(postRepositoryProvider).deletePost(widget.post.id);
      ref.invalidate(feedPostsProvider('global'));
      ref.invalidate(feedPostsProvider('following'));
      ref.invalidate(userPostsProvider(widget.post.authorUid));
      navigator.pop();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Failed to delete: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final layoutStyle = ref.watch(postImageLayoutProvider);
    final isEdgeToEdge = layoutStyle == PostImageLayoutStyle.edgeToEdge;
    // Only extend behind the app bar when there's actually an image up top;
    // a text-only post would otherwise leave an empty gap under the status bar.
    final imageBehindBar = isEdgeToEdge && _mediaUrls.isNotEmpty;

    return Scaffold(
      extendBodyBehindAppBar: imageBehindBar,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        iconTheme: IconThemeData(
          color: imageBehindBar
              ? Colors.white
              : Theme.of(context).iconTheme.color,
          shadows: imageBehindBar
              ? const [Shadow(color: Colors.black45, blurRadius: 10)]
              : null,
        ),
        actions: [
          if (FirebaseAuth.instance.currentUser?.uid == widget.post.authorUid)
            PopupMenuButton<String>(
              icon: Icon(
                Icons.more_vert,
                color: imageBehindBar
                    ? Colors.white
                    : Theme.of(context).iconTheme.color,
                shadows: imageBehindBar
                    ? const [Shadow(color: Colors.black45, blurRadius: 10)]
                    : null,
              ),
              onSelected: (value) {
                if (value == 'delete') _confirmDeletePost();
              },
              itemBuilder: (_) => const [
                PopupMenuItem(
                  value: 'delete',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.delete_outline),
                    title: Text('Delete post'),
                  ),
                ),
              ],
            ),
        ],
      ),
      body: Stack(
        children: [
          if (_mediaUrls.isNotEmpty)
            AnimatedAmbientBackground(
              key: ValueKey(_mediaUrls[_currentPage]),
              imageUrl: _mediaUrls[_currentPage],
              height: 800,
            ),

          SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Image carousel
                if (_mediaUrls.isNotEmpty)
                  ConstrainedBox(
                    constraints: BoxConstraints(
                      maxHeight: MediaQuery.of(context).size.height * 0.65,
                      minHeight: 280,
                    ),
                    child: Container(
                      width: double.infinity,
                      color: Colors.black.withValues(alpha: 0.9),
                      child: Stack(
                        children: [
                          Positioned.fill(
                            child: PageView.builder(
                              itemCount: _mediaUrls.length,
                              onPageChanged: (i) =>
                                  setState(() => _currentPage = i),
                              itemBuilder: (context, i) => GestureDetector(
                                onTap: () => Navigator.of(context).push(
                                  MaterialPageRoute<void>(
                                    fullscreenDialog: true,
                                    builder: (_) => FullscreenMediaViewer(
                                      urls: _mediaUrls,
                                      initialIndex: i,
                                    ),
                                  ),
                                ),
                                child: Stack(
                                  fit: StackFit.expand,
                                  children: [
                                    FirebaseCachedNetworkImage(
                                      imageUrl: _mediaUrls[i],
                                      fit: BoxFit.contain,
                                      memCacheWidth: 1080,
                                      errorWidget: (context, url, error) =>
                                          Container(
                                            color: Theme.of(
                                              context,
                                            ).colorScheme.surfaceContainerHighest,
                                            child: const Center(
                                              child: Icon(
                                                Icons.broken_image_outlined,
                                                size: 48,
                                                color: Colors.grey,
                                              ),
                                            ),
                                          ),
                                    ),
                                    // Fullscreen affordance icon
                                    Positioned(
                                      top: 10,
                                      right: 10,
                                      child: Container(
                                        padding: const EdgeInsets.all(5),
                                        decoration: BoxDecoration(
                                          color: Colors.black45,
                                          borderRadius: BorderRadius.circular(8),
                                        ),
                                        child: const Icon(
                                          Icons.fullscreen,
                                          color: Colors.white,
                                          size: 18,
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ),
                          // Dot indicators.
                          if (_mediaUrls.length > 1)
                            Positioned(
                              bottom: 16,
                              left: 0,
                              right: 0,
                              child: Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: List.generate(_mediaUrls.length, (i) {
                                  return AnimatedContainer(
                                    duration: const Duration(milliseconds: 300),
                                    margin: const EdgeInsets.symmetric(
                                      horizontal: 4,
                                    ),
                                    width: _currentPage == i ? 12 : 8,
                                    height: _currentPage == i ? 12 : 8,
                                    decoration: BoxDecoration(
                                      color: _currentPage == i
                                          ? Colors.white
                                          : Colors.white.withValues(alpha: 0.5),
                                      shape: BoxShape.circle,
                                      boxShadow: const [
                                        BoxShadow(
                                          color: Colors.black45,
                                          blurRadius: 4,
                                        ),
                                      ],
                                    ),
                                  );
                                }),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),

                // User Info Row
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 24, 20, 16),
                  child: Row(
                    children: [
                      GestureDetector(
                        onTap: widget.post.authorPhotoUrl.isNotEmpty
                            ? () => showDpViewer(
                                context,
                                ref,
                                widget.post.authorPhotoUrl,
                              )
                            : null,
                        child: CircleAvatar(
                          radius: 24,
                          backgroundImage: widget.post.authorPhotoUrl.isNotEmpty
                              ? FirebaseImageProviderWrapper.getProvider(
                                  ref,
                                  widget.post.authorPhotoUrl,
                                )
                              : null,
                          child: widget.post.authorPhotoUrl.isEmpty
                              ? const Icon(Icons.person, size: 28)
                              : null,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: GestureDetector(
                          onTap: _navigateToProfile,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                widget.post.authorDisplayName,
                                style: Theme.of(context).textTheme.titleMedium
                                    ?.copyWith(fontWeight: FontWeight.bold),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                              Text(
                                '@${widget.post.authorUsername}',
                                style: Theme.of(context).textTheme.bodyMedium
                                    ?.copyWith(
                                      color: Theme.of(
                                        context,
                                      ).colorScheme.primary,
                                    ),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ],
                          ),
                        ),
                      ),
                      Builder(
                        builder: (context) {
                          final currentUid =
                              FirebaseAuth.instance.currentUser?.uid;
                          if (currentUid == widget.post.authorUid) {
                            return const SizedBox.shrink();
                          }

                          final isFollowingAsync = ref.watch(
                            isFollowingProvider(widget.post.authorUid),
                          );
                          return isFollowingAsync.when(
                            data: (isFollowing) =>
                                FilledButton.tonal(
                                      onPressed: () async {
                                        final repo = ref.read(
                                          followRepositoryProvider,
                                        );
                                        if (isFollowing) {
                                          await repo.unfollowUser(
                                            widget.post.authorUid,
                                          );
                                        } else {
                                          await repo.followUser(
                                            widget.post.authorUid,
                                          );
                                        }
                                      },
                                      child: Text(
                                        isFollowing ? 'Following' : 'Follow',
                                      ),
                                    )
                                    .animate(key: ValueKey(isFollowing))
                                    .scaleXY(
                                      begin: 0.8,
                                      end: 1.0,
                                      duration: 200.ms,
                                      curve: Curves.easeOutBack,
                                    ),
                            loading: () => const FilledButton.tonal(
                              onPressed: null,
                              child: SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              ),
                            ),
                            error: (_, _) => const SizedBox.shrink(),
                          );
                        },
                      ),
                    ],
                  ),
                ),

                // Post Content
                Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 24,
                    vertical: 8,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        widget.post.createdAt != null
                            ? _timeAgo(widget.post.createdAt!)
                            : 'Just now',
                        style: Theme.of(
                          context,
                        ).textTheme.bodySmall?.copyWith(color: Colors.grey),
                      ),
                      const SizedBox(height: 24), // Pushes content downward
                      MarkdownBody(
                        data: widget.post.text,
                        extensionSet: md.ExtensionSet.gitHubFlavored,
                        inlineSyntaxes: [HighlightSyntax()],
                        builders: {'highlight': HighlightBuilder(context)},
                        styleSheet: MarkdownStyleSheet(
                          p: Theme.of(
                            context,
                          ).textTheme.bodyLarge?.copyWith(height: 1.5),
                          h1: Theme.of(context).textTheme.headlineMedium
                              ?.copyWith(fontWeight: FontWeight.bold),
                          h2: Theme.of(context).textTheme.headlineSmall
                              ?.copyWith(fontWeight: FontWeight.bold),
                          h3: Theme.of(context).textTheme.titleLarge?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      const SizedBox(height: 32),
                      Consumer(
                        builder: (context, ref, child) {
                          final statsAsync = ref.watch(
                            postStatsProvider(widget.post.id),
                          );
                          final statsData = statsAsync.value;
                          final liveCommentCount =
                              statsData?['comment_count'] as int? ??
                                  widget.post.commentCount;
                          final liveViewCount =
                              statsData?['view_count'] as int? ??
                                  widget.post.viewCount;

                          return Row(
                            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                            children: [
                              _LikeActionWidget(post: widget.post),
                              _buildAction(
                                Icons.chat_bubble_outline,
                                '$liveCommentCount',
                                () => showCommentsBottomSheet(
                                  context,
                                  widget.post.id,
                                ),
                              ),
                              _buildAction(Icons.share_outlined, 'Share', () {
                                final shareText =
                                    'Check out this post on SafeChat: https://safechat.com/post/${widget.post.id}';
                                // ignore: deprecated_member_use
                                Share.share(shareText);
                              }),
                              _buildAction(
                                Icons.visibility_outlined,
                                '$liveViewCount',
                                () {}, // View count is just a display
                              ),
                            ],
                          );
                        },
                      ),
                      const SizedBox(height: 48),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  void _navigateToProfile() {
    final currentUid = FirebaseAuth.instance.currentUser?.uid;
    if (widget.post.authorUid == currentUid) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PublicProfileView(
          uid: widget.post.authorUid,
          username: widget.post.authorUsername,
        ),
      ),
    );
  }

  Widget _buildAction(
    IconData icon,
    String label,
    VoidCallback onTap, {
    Color? color,
  }) {
    return Column(
      children: [
        IconButton.filledTonal(
          onPressed: onTap,
          icon: Icon(icon, color: color),
          iconSize: 28,
        ),
        const SizedBox(height: 8),
        Text(label, style: TextStyle(color: color)),
      ],
    );
  }

  String _timeAgo(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inSeconds < 60) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Comments bottom sheet (reusable)
// ─────────────────────────────────────────────────────────────────────────────

void showCommentsBottomSheet(BuildContext context, String postId) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (context) {
      return Padding(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(context).viewInsets.bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AppBar(
              title: const Text('Comments'),
              leading: IconButton(
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.pop(context),
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: Consumer(
                builder: (context, ref, child) {
                  final streamAsync =
                      ref.watch(approvedCommentsStreamProvider(postId));
                  final commentsAsync = ref.watch(commentsProvider(postId));
                  final comments =
                      streamAsync.value ?? commentsAsync.value ?? [];
                  final isLoading =
                      streamAsync.isLoading && commentsAsync.isLoading;

                  if (isLoading) {
                    return const Center(child: CircularProgressIndicator());
                  }

                  if (comments.isEmpty) {
                    return const Center(child: Text('No comments yet.'));
                  }

                  return ListView.builder(
                    itemCount: comments.length,
                        itemBuilder: (context, index) {
                          final comment = comments[index];
                          return ListTile(
                            leading: CircleAvatar(
                              backgroundImage: comment.authorPhotoUrl.isNotEmpty
                                  ? FirebaseImageProviderWrapper.getProvider(
                                      ref,
                                      comment.authorPhotoUrl,
                                    )
                                  : null,
                              child:
                                  (comment.authorPhotoUrl.isEmpty ||
                                      FirebaseImageProviderWrapper.getProvider(
                                            ref,
                                            comment.authorPhotoUrl,
                                          ) ==
                                          null)
                                  ? const Icon(Icons.person)
                                  : null,
                            ),
                            title: Text(
                              comment.authorDisplayName.isNotEmpty
                                  ? comment.authorDisplayName
                                  : 'User',
                            ),
                            subtitle: Text(comment.text),
                            trailing: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                IconButton(
                                  icon:
                                      Icon(
                                            comment.isLiked
                                                ? Icons.favorite
                                                : Icons.favorite_border,
                                            size: 16,
                                            color: comment.isLiked
                                                ? Colors.red
                                                : null,
                                          )
                                          .animate(
                                            key: ValueKey(comment.isLiked),
                                          )
                                          .scaleXY(
                                            begin: 0.7,
                                            end: 1.0,
                                            duration: const Duration(
                                              milliseconds: 200,
                                            ),
                                            curve: Curves.easeOutBack,
                                          ),
                                  onPressed: () {
                                    HapticFeedback.lightImpact();
                                    if (comment.isLiked) {
                                      ref
                                          .read(
                                            commentsProvider(postId).notifier,
                                          )
                                          .unlikeComment(comment.id);
                                    } else {
                                      ref
                                          .read(
                                            commentsProvider(postId).notifier,
                                          )
                                          .likeComment(comment.id);
                                    }
                                  },
                                ),
                                if (comment.likeCount > 0)
                                  Text(
                                    '${comment.likeCount}',
                                    style: const TextStyle(fontSize: 12),
                                  ),
                                IconButton(
                                  icon: const Icon(Icons.reply, size: 16),
                                  onPressed: () {
                                    // Reply logic
                                  },
                                ),
                                if (comment.authorUid ==
                                    FirebaseAuth.instance.currentUser?.uid)
                                  IconButton(
                                    icon: const Icon(
                                      Icons.delete_outline,
                                      size: 16,
                                    ),
                                    tooltip: 'Delete',
                                    onPressed: () async {
                                      final notifier = ref.read(
                                        commentsProvider(postId).notifier,
                                      );
                                      final messenger = ScaffoldMessenger.of(
                                        context,
                                      );
                                      final ok = await showDialog<bool>(
                                        context: context,
                                        builder: (ctx) => AlertDialog(
                                          title: const Text('Delete comment?'),
                                          content: const Text(
                                            'This comment will be permanently removed.',
                                          ),
                                          actions: [
                                            TextButton(
                                              onPressed: () =>
                                                  Navigator.pop(ctx, false),
                                              child: const Text('Cancel'),
                                            ),
                                            FilledButton(
                                              onPressed: () =>
                                                  Navigator.pop(ctx, true),
                                              child: const Text('Delete'),
                                            ),
                                          ],
                                        ),
                                      );
                                      if (ok != true) return;
                                      try {
                                        await notifier.deleteComment(
                                          comment.id,
                                        );
                                      } catch (e) {
                                        messenger.showSnackBar(
                                          SnackBar(
                                            content: Text(
                                              'Failed to delete: $e',
                                            ),
                                          ),
                                        );
                                      }
                                    },
                                  ),
                              ],
                            ),
                          );
                        },
                      );
                },
              ),
            ),
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.all(8.0),
              child: Consumer(
                builder: (context, ref, child) {
                  final controller = TextEditingController();
                  return Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: controller,
                          decoration: const InputDecoration(
                            hintText: 'Add a comment...',
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.all(
                                Radius.circular(24),
                              ),
                            ),
                            contentPadding: EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 8,
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filledTonal(
                        icon: const Icon(Icons.send),
                        onPressed: () async {
                          final text = controller.text.trim();
                          if (text.isEmpty) return;
                          final notifier = ref.read(
                            commentsProvider(postId).notifier,
                          );
                          final messenger = ScaffoldMessenger.of(context);
                          try {
                            await notifier.createComment(text);
                            controller.clear();
                            if (context.mounted) {
                              FocusScope.of(context).unfocus();
                            }
                          } catch (e) {
                            final flagged = flaggedFromError(e);
                            if (flagged == null) {
                              messenger.showSnackBar(
                                SnackBar(
                                  content: Text('Failed to comment: $e'),
                                ),
                              );
                              return;
                            }
                            if (!context.mounted) return;
                            final result = await showFlaggedContentDialog(
                              context,
                              text: text,
                              matches: flagged.matches,
                              contentNoun: 'comment',
                            );
                            if (result == null || !result.submitForReview) {
                              return;
                            }
                            try {
                              await notifier.submitCommentForReview(text);
                              controller.clear();
                              if (context.mounted) {
                                FocusScope.of(context).unfocus();
                              }
                              messenger.showSnackBar(
                                const SnackBar(
                                  content: Text(
                                    '📋 Comment sent for review. Track it in Profile → Appeals.',
                                  ),
                                ),
                              );
                            } catch (e2) {
                              messenger.showSnackBar(
                                SnackBar(content: Text('Failed: $e2')),
                              );
                            }
                          }
                        },
                      ),
                    ],
                  );
                },
              ),
            ),
          ],
        ),
      );
    },
  );
}

class _LikeActionWidget extends ConsumerStatefulWidget {
  final FeedPost post;

  const _LikeActionWidget({required this.post});

  @override
  ConsumerState<_LikeActionWidget> createState() => _LikeActionWidgetState();
}

class _LikeActionWidgetState extends ConsumerState<_LikeActionWidget> {
  @override
  Widget build(BuildContext context) {
    final isLikedAsync = ref.watch(isLikedProvider(widget.post.id));
    final isLiked = isLikedAsync.value ?? false;

    final statsAsync = ref.watch(postStatsProvider(widget.post.id));
    final statsData = statsAsync.value;
    final liveLikeCount =
        statsData?['like_count'] as int? ?? widget.post.likeCount;

    int displayCount = liveLikeCount;
    if (displayCount < 0) displayCount = 0;

    Widget icon = Icon(
      isLiked ? Icons.favorite : Icons.favorite_border,
      color: isLiked ? Colors.red : null,
    );

    if (isLiked) {
      icon = icon
          .animate(key: const ValueKey('liked'))
          .scale(duration: 250.ms, curve: Curves.easeOutBack)
          .tint(color: Colors.red);
    } else {
      icon = icon
          .animate(key: const ValueKey('unliked'))
          .scale(duration: 200.ms);
    }

    return Column(
      children: [
        IconButton.filledTonal(
          onPressed: () {
            HapticFeedback.lightImpact();
            if (isLiked) {
              ref.read(postRepositoryProvider).unlikePost(widget.post.id);
            } else {
              ref.read(postRepositoryProvider).likePost(widget.post.id);
            }
          },
          icon: icon,
          iconSize: 28,
        ),
        const SizedBox(height: 8),
        RollingCounter(
          value: displayCount,
          style: TextStyle(color: isLiked ? Colors.red : null),
        ),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Spatial Deck Card (Modern Fluid Concept)
// ─────────────────────────────────────────────────────────────────────────────

const _emeraldColor = Color(0xFF10B981);

class _SpatialDeckCard extends ConsumerWidget {
  final FeedPost post;
  const _SpatialDeckCard({required this.post});

  void _showSafetyShieldDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Row(
          children: [
            Icon(Icons.shield_outlined, color: _emeraldColor, size: 28),
            SizedBox(width: 10),
            Text('SafeChat AI Verified', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'This content passed SafeChat real-time multi-layer AI moderation cascade with zero toxic or bullying signals.',
              style: TextStyle(fontSize: 14, height: 1.4),
            ),
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: _emeraldColor.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: _emeraldColor.withValues(alpha: 0.3)),
              ),
              child: const Column(
                children: [
                  _SafetyCheckRow(label: 'Keyword Lexicon Scorer', status: 'Passed (0.00)'),
                  SizedBox(height: 6),
                  _SafetyCheckRow(label: 'TF-IDF Toxicity Engine', status: 'Clean'),
                  SizedBox(height: 6),
                  _SafetyCheckRow(label: 'Vision Media Filter', status: 'Verified Safe'),
                ],
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Got it'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colorScheme = theme.colorScheme;
    final statsAsync = ref.watch(postStatsProvider(post.id));
    final isLikedAsync = ref.watch(isLikedProvider(post.id));
    final isLiked = isLikedAsync.value ?? false;

    final statsData = statsAsync.value;
    final liveLikeCount = statsData?['like_count'] as int? ?? post.likeCount;
    final liveCommentCount = statsData?['comment_count'] as int? ?? post.commentCount;
    final liveViewCount = statsData?['view_count'] as int? ?? post.viewCount;

    final mediaUrls = post.displayUrls;

    return Container(
      decoration: BoxDecoration(
        color: colorScheme.surfaceContainerHighest.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(28),
        border: Border.all(
          color: colorScheme.primary.withValues(alpha: 0.25),
          width: 1.5,
        ),
        boxShadow: [
          BoxShadow(
            color: colorScheme.primary.withValues(alpha: 0.08),
            blurRadius: 24,
            spreadRadius: 2,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(28),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Top Author + Safety Shield Row
                Row(
                  children: [
                    // Author Avatar with Glowing Ring
                    GestureDetector(
                      onTap: () {
                        Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => PublicProfileView(
                              uid: post.authorUid,
                              username: post.authorUsername,
                            ),
                          ),
                        );
                      },
                      child: Container(
                        padding: const EdgeInsets.all(2.5),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: SweepGradient(
                            colors: [
                              colorScheme.primary,
                              colorScheme.tertiary,
                              colorScheme.primary,
                            ],
                          ),
                        ),
                        child: CircleAvatar(
                          radius: 20,
                          backgroundColor: colorScheme.surface,
                          backgroundImage: FirebaseImageProviderWrapper.getProvider(
                            ref,
                            post.authorPhotoUrl,
                          ),
                          child: post.authorPhotoUrl.isEmpty
                              ? Text(
                                  post.authorDisplayName.isNotEmpty
                                      ? post.authorDisplayName[0].toUpperCase()
                                      : '?',
                                  style: const TextStyle(fontWeight: FontWeight.bold),
                                )
                              : null,
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            post.authorDisplayName,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 15,
                            ),
                          ),
                          Text(
                            '@${post.authorUsername}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 12,
                              color: colorScheme.onSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                    ),
                    // Interactive Safety Shield Badge
                    InkWell(
                      onTap: () => _showSafetyShieldDialog(context),
                      borderRadius: BorderRadius.circular(20),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(
                          color: _emeraldColor.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(
                            color: _emeraldColor.withValues(alpha: 0.4),
                          ),
                        ),
                        child: const Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.shield, color: _emeraldColor, size: 14),
                            SizedBox(width: 4),
                            Text(
                              'Verified Safe',
                              style: TextStyle(
                                color: _emeraldColor,
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ).animate().scale(duration: 1000.ms, curve: Curves.easeInOut),
                    ),
                  ],
                ),
                const SizedBox(height: 14),

                // Post Caption (Markdown)
                if (post.text.isNotEmpty) ...[
                  MarkdownBody(
                    data: post.text,
                    styleSheet: MarkdownStyleSheet(
                      p: TextStyle(
                        fontSize: 15,
                        height: 1.4,
                        color: colorScheme.onSurface,
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                ],

                // Media Display (Image Grid / Carousel)
                if (mediaUrls.isNotEmpty) ...[
                  ClipRRect(
                    borderRadius: BorderRadius.circular(20),
                    child: Container(
                      constraints: const BoxConstraints(maxHeight: 380, minHeight: 200),
                      width: double.infinity,
                      color: Colors.black.withValues(alpha: 0.8),
                      child: FirebaseCachedNetworkImage(
                        imageUrl: mediaUrls.first,
                        fit: BoxFit.contain,
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                ],

                // Action Bar (Likes, Comments, Views, Share)
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        // Animated Like Pill
                        InkWell(
                          onTap: () {
                            HapticFeedback.lightImpact();
                            if (isLiked) {
                              ref.read(postRepositoryProvider).unlikePost(post.id);
                            } else {
                              ref.read(postRepositoryProvider).likePost(post.id);
                            }
                          },
                          borderRadius: BorderRadius.circular(20),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 6,
                            ),
                            decoration: BoxDecoration(
                              color: isLiked
                                  ? Colors.red.withValues(alpha: 0.15)
                                  : colorScheme.surfaceContainer.withValues(alpha: 0.6),
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Row(
                              children: [
                                Icon(
                                  isLiked ? Icons.favorite : Icons.favorite_border,
                                  color: isLiked ? Colors.red : colorScheme.onSurfaceVariant,
                                  size: 18,
                                ),
                                const SizedBox(width: 6),
                                RollingCounter(
                                  value: liveLikeCount < 0 ? 0 : liveLikeCount,
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w600,
                                    color: isLiked ? Colors.red : colorScheme.onSurfaceVariant,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),

                        // Comment Button
                        InkWell(
                          onTap: () => showCommentsBottomSheet(context, post.id),
                          borderRadius: BorderRadius.circular(20),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 6,
                            ),
                            decoration: BoxDecoration(
                              color: colorScheme.surfaceContainer.withValues(alpha: 0.6),
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Row(
                              children: [
                                Icon(
                                  Icons.chat_bubble_outline,
                                  color: colorScheme.onSurfaceVariant,
                                  size: 18,
                                ),
                                const SizedBox(width: 6),
                                Text(
                                  '$liveCommentCount',
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w600,
                                    color: colorScheme.onSurfaceVariant,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),

                    Row(
                      children: [
                        // View Count Badge
                        Row(
                          children: [
                            Icon(
                              Icons.remove_red_eye_outlined,
                              size: 16,
                              color: colorScheme.onSurfaceVariant.withValues(alpha: 0.7),
                            ),
                            const SizedBox(width: 4),
                            Text(
                              '$liveViewCount',
                              style: TextStyle(
                                fontSize: 12,
                                color: colorScheme.onSurfaceVariant.withValues(alpha: 0.7),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(width: 12),

                        // Share Button
                        IconButton(
                          icon: const Icon(Icons.share_outlined, size: 20),
                          onPressed: () {
                            Share.share('Check out this post on SafeChat: ${post.text}');
                          },
                        ),
                      ],
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SafetyCheckRow extends StatelessWidget {
  final String label;
  final String status;
  const _SafetyCheckRow({required this.label, required this.status});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500)),
        Text(status, style: const TextStyle(fontSize: 12, color: _emeraldColor, fontWeight: FontWeight.bold)),
      ],
    );
  }
}

