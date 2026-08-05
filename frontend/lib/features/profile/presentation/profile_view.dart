import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../../theme/theme_provider.dart';
import '../../../shared/widgets/animated_ambient_background.dart';
import 'edit_profile_view.dart';
import 'follow_providers.dart';
import 'network_graph_view.dart';
import 'content_status_view.dart';
import '../../../shared/widgets/firebase_image.dart';
import '../../../shared/widgets/rolling_counter.dart';
import '../../../shared/widgets/empty_state.dart';
import '../../admin/presentation/admin_providers.dart';
import '../../admin/presentation/admin_moderation_view.dart';
import 'user_posts_provider.dart';
import '../../home/presentation/feed_view.dart';

class ProfileView extends ConsumerWidget {
  const ProfileView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authStateProvider);
    final user = authState.user;
    final layout = ref.watch(profileLayoutProvider);

    // Authenticated but the profile is still being fetched from the backend —
    // show a skeleton instead of fallback text + spinners.
    if (authState.profile == null && authState.isLoading) {
      return const Scaffold(body: _ProfileSkeleton());
    }

    return Scaffold(
      body: layout == ProfileLayoutStyle.modernCover
          ? _buildModernCover(context, user, ref)
          : _buildCenteredMinimalist(context, user, ref),
    );
  }

  Widget _buildModernCover(BuildContext context, dynamic user, WidgetRef ref) {
    final scaffoldColor = Theme.of(context).scaffoldBackgroundColor;
    final colorScheme = Theme.of(context).colorScheme;
    final profile = ref.watch(authStateProvider).profile;

    return Stack(
      children: [
        // 1. Dynamic Animated Ambient Background
        if (profile?.backgroundUrl != null)
          AnimatedAmbientBackground(
            imageUrl: profile!.backgroundUrl!,
            height: 800,
          ),

        // 2. Scrolling Content
        CustomScrollView(
          slivers: [
            // Edge-to-Edge Cover Photo & Avatar Header
            SliverToBoxAdapter(
              child: SizedBox(
                height: 270,
                child: Stack(
                  children: [
                    // Cover Photo
                    Positioned(
                      top: 0,
                      left: 0,
                      right: 0,
                      height: 200,
                      child: ClipRRect(
                        borderRadius: const BorderRadius.vertical(
                          bottom: Radius.circular(28),
                        ),
                        child: profile?.backgroundUrl != null
                            ? FirebaseCachedNetworkImage(
                                imageUrl: profile!.backgroundUrl!,
                                fit: BoxFit.cover,
                                placeholder: (_, _) => _buildGradientCover(user),
                                errorWidget: (_, _, _) => _buildGradientCover(user),
                              )
                            : _buildGradientCover(user),
                      ),
                    ),

                    // Top Action Buttons in Safe Area
                    Positioned(
                      top: 0,
                      right: 12,
                      child: SafeArea(
                        child: Row(
                          children: [
                            IconButton.filledTonal(
                              style: IconButton.styleFrom(
                                backgroundColor: colorScheme.surface.withValues(alpha: 0.7),
                              ),
                              icon: const Icon(Icons.edit_outlined, size: 20),
                              onPressed: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => const EditProfileView(),
                                ),
                              ),
                            ),
                            const SizedBox(width: 6),
                            IconButton.filledTonal(
                              style: IconButton.styleFrom(
                                backgroundColor: colorScheme.surface.withValues(alpha: 0.7),
                              ),
                              icon: const Icon(Icons.settings_outlined, size: 20),
                              onPressed: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => const SettingsView(),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),

                    // Avatar with Glowing Multi-Color Ring
                    Positioned(
                      top: 145,
                      left: 20,
                      child: Container(
                        padding: const EdgeInsets.all(3.5),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: LinearGradient(
                            colors: [
                              colorScheme.primary,
                              colorScheme.tertiary,
                              const Color(0xFF8E2DE2),
                            ],
                          ),
                          boxShadow: [
                            BoxShadow(
                              color: colorScheme.primary.withValues(alpha: 0.3),
                              blurRadius: 16,
                              spreadRadius: 2,
                            ),
                          ],
                        ),
                        child: CircleAvatar(
                          radius: 44,
                          backgroundColor: scaffoldColor,
                          child: ClipOval(
                            child: SizedBox(
                              width: 84,
                              height: 84,
                              child: _buildAvatarImage(profile, user),
                            ),
                          ),
                        ),
                      ),
                    ),

                    // Secondary Quick Action Pills to the right of avatar
                    Positioned(
                      top: 212,
                      right: 16,
                      child: Row(
                        children: [
                          IconButton.filledTonal(
                            style: IconButton.styleFrom(
                              backgroundColor: colorScheme.surfaceContainerHighest.withValues(alpha: 0.8),
                            ),
                            onPressed: () => Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => const NetworkGraphView(),
                              ),
                            ),
                            icon: const Icon(Icons.hub_outlined, size: 20),
                            tooltip: 'Network Graph',
                          ),
                          const SizedBox(width: 6),
                          IconButton.filledTonal(
                            style: IconButton.styleFrom(
                              backgroundColor: colorScheme.surfaceContainerHighest.withValues(alpha: 0.8),
                            ),
                            onPressed: () => Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => const ContentStatusView(),
                              ),
                            ),
                            icon: const Icon(Icons.gavel_outlined, size: 20),
                            tooltip: 'Content Status / Appeals',
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),

            // Profile Info & Bio
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      profile?.displayName ??
                          user?.displayName ??
                          'SafeChat User',
                      style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                            letterSpacing: -0.5,
                          ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '@${profile?.username ?? user?.displayName?.toLowerCase().replaceAll(' ', '') ?? 'user'}',
                      style: TextStyle(
                        fontSize: 14,
                        color: colorScheme.onSurfaceVariant,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 14),
                    Text(
                      profile?.bio?.isNotEmpty == true
                          ? profile!.bio!
                          : 'Creating a safer social space 🛡️\n#flutter #dev',
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.4,
                        color: colorScheme.onSurface.withValues(alpha: 0.9),
                      ),
                    ),
                    const SizedBox(height: 20),

                    // Frosted Glass Stats Card
                    Container(
                      padding: const EdgeInsets.symmetric(vertical: 18),
                      decoration: BoxDecoration(
                        color: colorScheme.surfaceContainerHighest.withValues(alpha: 0.45),
                        borderRadius: BorderRadius.circular(24),
                        border: Border.all(
                          color: colorScheme.primary.withValues(alpha: 0.2),
                          width: 1.5,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.05),
                            blurRadius: 16,
                            offset: const Offset(0, 4),
                          ),
                        ],
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(24),
                        child: BackdropFilter(
                          filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
                          child: Consumer(
                            builder: (context, ref, _) {
                              final uid = user?.uid ?? '';
                              final followersAsync = ref.watch(
                                followersCountProvider(uid),
                              );
                              final followingAsync = ref.watch(
                                followingCountProvider(uid),
                              );
                              final friendsAsync = ref.watch(friendsProvider(uid));

                              return Row(
                                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                                children: [
                                  _StatColumn(
                                    label: 'Followers',
                                    count: followersAsync.value,
                                  ),
                                  Container(
                                    width: 1,
                                    height: 32,
                                    color: colorScheme.outlineVariant.withValues(alpha: 0.5),
                                  ),
                                  _StatColumn(
                                    label: 'Following',
                                    count: followingAsync.value,
                                  ),
                                  Container(
                                    width: 1,
                                    height: 32,
                                    color: colorScheme.outlineVariant.withValues(alpha: 0.5),
                                  ),
                                  _StatColumn(
                                    label: 'Friends',
                                    count: friendsAsync.value?.length,
                                  ),
                                ],
                              );
                            },
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),

            const SliverToBoxAdapter(child: SizedBox(height: 20)),
            _buildGrid(ref, user?.uid ?? ''),
            const SliverToBoxAdapter(child: SizedBox(height: 100)),
          ],
        ),
      ],
    );
  }

  Widget _buildCenteredMinimalist(
    BuildContext context,
    dynamic user,
    WidgetRef ref,
  ) {
    final profile = ref.watch(authStateProvider).profile;
    return CustomScrollView(
      slivers: [
        SliverAppBar(
          title: Text(user?.displayName ?? 'Profile'),
          centerTitle: true,
          actions: [
            IconButton(
              icon: const Icon(Icons.edit_outlined),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const EditProfileView()),
              ),
            ),
            IconButton(
              icon: const Icon(Icons.settings_outlined),
              onPressed: () => Navigator.of(
                context,
              ).push(MaterialPageRoute(builder: (_) => const SettingsView())),
            ),
          ],
        ),
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                ClipOval(
                  child: SizedBox(
                    width: 100,
                    height: 100,
                    child: _buildAvatarImage(profile, user),
                  ),
                ),
                const SizedBox(height: 16),
                Text(
                  profile?.displayName ?? user?.displayName ?? 'SafeChat User',
                  style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  profile?.bio?.isNotEmpty == true
                      ? profile!.bio!
                      : 'Creating a safer social space 🛡️\n#flutter #dev',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 24),
                Consumer(
                  builder: (context, ref, _) {
                    final uid = user?.uid ?? '';
                    final followersAsync = ref.watch(
                      followersCountProvider(uid),
                    );
                    final followingAsync = ref.watch(
                      followingCountProvider(uid),
                    );
                    final friendsAsync = ref.watch(friendsProvider(uid));

                    return Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        _StatColumn(
                          label: 'Followers',
                          count: followersAsync.value,
                        ),
                        const SizedBox(width: 32),
                        _StatColumn(
                          label: 'Following',
                          count: followingAsync.value,
                        ),
                        const SizedBox(width: 32),
                        _StatColumn(
                          label: 'Friends',
                          count: friendsAsync.value?.length,
                        ),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 24),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    IconButton.filledTonal(
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => const NetworkGraphView(),
                        ),
                      ),
                      icon: const Icon(Icons.hub),
                      tooltip: 'Network Graph',
                    ),
                    const SizedBox(width: 16),
                    IconButton.filledTonal(
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => const ContentStatusView(),
                        ),
                      ),
                      icon: const Icon(Icons.gavel),
                      tooltip: 'Content Status / Appeals',
                    ),
                    const SizedBox(width: 16),
                    FilledButton.tonal(
                      onPressed: () {},
                      child: const Text('Share Profile'),
                    ),
                  ],
                ),
                const SizedBox(height: 32),
                const Divider(),
              ],
            ),
          ),
        ),
        _buildGrid(ref, user?.uid ?? ''),
        const SliverToBoxAdapter(child: SizedBox(height: 100)),
      ],
    );
  }

  Widget _buildGrid(WidgetRef ref, String uid) {
    final userPostsAsync = ref.watch(userPostsProvider(uid));

    return userPostsAsync.when(
      loading: () => const SliverToBoxAdapter(
        child: Center(
          child: Padding(
            padding: EdgeInsets.all(32),
            child: CircularProgressIndicator(),
          ),
        ),
      ),
      error: (e, _) =>
          SliverToBoxAdapter(child: Center(child: Text('Error: $e'))),
      data: (posts) {
        if (posts.isEmpty) {
          return const SliverToBoxAdapter(
            child: Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: EmptyState(
                icon: Icons.grid_view_rounded,
                title: 'No posts yet',
                message: "Share your first post — it'll show up here.",
              ),
            ),
          );
        }
        return SliverPadding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          sliver: SliverGrid(
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 3,
              mainAxisSpacing: 8,
              crossAxisSpacing: 8,
            ),
            delegate: SliverChildBuilderDelegate((context, index) {
              final post = posts[index];
              final thumb = post.displayUrls.isNotEmpty
                  ? post.displayUrls.first
                  : '';
              return PostOpenContainer(
                post: post,
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(14),
                  child: Container(
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(14),
                    ),
                    child: thumb.isNotEmpty
                        ? FirebaseCachedNetworkImage(
                            imageUrl: thumb,
                            fit: BoxFit.cover,
                            memCacheWidth: 400,
                            errorWidget: (_, _, _) => const Center(
                              child: Icon(Icons.broken_image_outlined),
                            ),
                          )
                        : const Center(child: Icon(Icons.article_outlined)),
                  ),
                ),
              );
            }, childCount: posts.length),
          ),
        );
      },
    );
  }

  Widget _buildGradientCover(dynamic user) {
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            HSLColor.fromAHSL(
              1.0,
              ((user?.uid ?? 'a').hashCode % 360).toDouble(),
              0.7,
              0.6,
            ).toColor(),
            HSLColor.fromAHSL(
              1.0,
              (((user?.uid ?? 'a').hashCode >> 8) % 360).toDouble(),
              0.7,
              0.6,
            ).toColor(),
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
    );
  }

  Widget _buildAvatarImage(dynamic profile, dynamic user) {
    final photoUrl =
        (profile?.photoUrl as String?) ?? (user?.photoURL as String?);
    if (photoUrl != null && photoUrl.isNotEmpty) {
      // Image is already framed at upload (baked crop), so just cover-fit it.
      return FirebaseCachedNetworkImage(
        imageUrl: photoUrl,
        fit: BoxFit.cover,
        memCacheWidth: 240,
        placeholder: (_, _) => const ColoredBox(color: Colors.black12),
        errorWidget: (_, _, _) => const Icon(Icons.person, size: 45),
      );
    }
    return const Icon(Icons.person, size: 45);
  }
}

// ---------------------------------------------------------------------------
// Skeleton loading
// ---------------------------------------------------------------------------

/// Animated shimmer wrapper — sweeps a highlight across any opaque child.
class _Shimmer extends StatefulWidget {
  final Widget child;
  const _Shimmer({required this.child});

  @override
  State<_Shimmer> createState() => _ShimmerState();
}

class _ShimmerState extends State<_Shimmer>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1300),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.surfaceContainerHighest;
    final highlight = Color.lerp(
      base,
      Theme.of(context).colorScheme.onSurface,
      0.10,
    )!;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return ShaderMask(
          blendMode: BlendMode.srcATop,
          shaderCallback: (bounds) {
            return LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: [base, highlight, base],
              stops: const [0.30, 0.50, 0.70],
              transform: _SlideGradient(_controller.value * 2 - 1),
            ).createShader(bounds);
          },
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

class _SlideGradient extends GradientTransform {
  final double slidePercent;
  const _SlideGradient(this.slidePercent);

  @override
  Matrix4? transform(Rect bounds, {TextDirection? textDirection}) =>
      Matrix4.translationValues(bounds.width * slidePercent, 0, 0);
}

class _SkeletonBox extends StatelessWidget {
  final double? width;
  final double height;
  final double radius;
  final bool circle;

  const _SkeletonBox({
    this.width,
    required this.height,
    this.radius = 8,
    this.circle = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: circle ? height : width,
      height: height,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        shape: circle ? BoxShape.circle : BoxShape.rectangle,
        borderRadius: circle ? null : BorderRadius.circular(radius),
      ),
    );
  }
}

class _ProfileSkeleton extends StatelessWidget {
  const _ProfileSkeleton();

  @override
  Widget build(BuildContext context) {
    final scaffoldColor = Theme.of(context).scaffoldBackgroundColor;
    final cellColor = Theme.of(context).colorScheme.surfaceContainerHighest;
    return _Shimmer(
      child: SingleChildScrollView(
        physics: const NeverScrollableScrollPhysics(),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Cover + overlapping avatar
            SizedBox(
              height: 260,
              child: Stack(
                children: [
                  const _SkeletonBox(
                    height: 200,
                    width: double.infinity,
                    radius: 0,
                  ),
                  Positioned(
                    top: 155,
                    left: 16,
                    child: Container(
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(color: scaffoldColor, width: 4),
                      ),
                      child: const _SkeletonBox(height: 90, circle: true),
                    ),
                  ),
                ],
              ),
            ),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(height: 12),
                  _SkeletonBox(height: 26, width: 200),
                  SizedBox(height: 10),
                  _SkeletonBox(height: 14, width: 120),
                  SizedBox(height: 18),
                  _SkeletonBox(height: 14, width: double.infinity),
                  SizedBox(height: 8),
                  _SkeletonBox(height: 14, width: 220),
                  SizedBox(height: 22),
                  _SkeletonBox(height: 72, width: double.infinity, radius: 16),
                  SizedBox(height: 24),
                ],
              ),
            ),
            // Posts grid
            GridView.count(
              crossAxisCount: 3,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              mainAxisSpacing: 2,
              crossAxisSpacing: 2,
              children: List.generate(6, (_) => ColoredBox(color: cellColor)),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatColumn extends StatelessWidget {
  final String label;
  final int? count;

  const _StatColumn({required this.label, required this.count});

  @override
  Widget build(BuildContext context) {
    const numberStyle = TextStyle(fontWeight: FontWeight.bold, fontSize: 18);
    return Column(
      children: [
        count == null
            ? const Text('—', style: numberStyle)
            : RollingCounter(value: count!, style: numberStyle),
        Text(label, style: const TextStyle(fontSize: 14)),
      ],
    );
  }
}

class SettingsView extends ConsumerWidget {
  const SettingsView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final currentLayout = ref.watch(feedLayoutProvider);
    final navbarStyle = ref.watch(navbarStyleProvider);
    final profileLayout = ref.watch(profileLayoutProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Feed Layout',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              _LayoutCard(
                label: 'Grid',
                icon: Icons.grid_view_rounded,
                color: Theme.of(context).colorScheme.primary,
                isSelected: currentLayout == FeedLayoutMode.grid,
                onTap: () => ref
                    .read(feedLayoutProvider.notifier)
                    .setLayout(FeedLayoutMode.grid),
              ),
              const SizedBox(width: 8),
              _LayoutCard(
                label: 'Card',
                icon: Icons.view_agenda_rounded,
                color: Theme.of(context).colorScheme.secondary,
                isSelected: currentLayout == FeedLayoutMode.card,
                onTap: () => ref
                    .read(feedLayoutProvider.notifier)
                    .setLayout(FeedLayoutMode.card),
              ),
              const SizedBox(width: 8),
              _LayoutCard(
                label: 'Spatial Deck',
                icon: Icons.auto_awesome_rounded,
                color: Theme.of(context).colorScheme.tertiary,
                isSelected: currentLayout == FeedLayoutMode.spatialDeck,
                isNew: true,
                onTap: () => ref
                    .read(feedLayoutProvider.notifier)
                    .setLayout(FeedLayoutMode.spatialDeck),
              ),
            ],
          ),
          const SizedBox(height: 24),
          const Text(
            'Post Image Layout',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          Consumer(
            builder: (context, ref, _) {
              final postLayout = ref.watch(postImageLayoutProvider);
              return SegmentedButton<PostImageLayoutStyle>(
                segments: const [
                  ButtonSegment(
                    value: PostImageLayoutStyle.edgeToEdge,
                    label: Text('Edge-to-Edge', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.fullscreen),
                  ),
                  ButtonSegment(
                    value: PostImageLayoutStyle.padded,
                    label: Text('Padded', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.padding),
                  ),
                ],
                selected: {postLayout},
                onSelectionChanged: (Set<PostImageLayoutStyle> newSelection) {
                  ref
                      .read(postImageLayoutProvider.notifier)
                      .setStyle(newSelection.first);
                },
              );
            },
          ),
          const SizedBox(height: 24),
          const Text(
            'Profile Layout',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          SegmentedButton<ProfileLayoutStyle>(
            segments: const [
              ButtonSegment(
                value: ProfileLayoutStyle.modernCover,
                label: Text('Modern Cover', style: TextStyle(fontSize: 12)),
                icon: Icon(Icons.panorama),
              ),
              ButtonSegment(
                value: ProfileLayoutStyle.centeredMinimalist,
                label: Text('Minimalist', style: TextStyle(fontSize: 12)),
                icon: Icon(Icons.align_vertical_center),
              ),
            ],
            selected: {profileLayout},
            onSelectionChanged: (Set<ProfileLayoutStyle> newSelection) {
              ref
                  .read(profileLayoutProvider.notifier)
                  .setStyle(newSelection.first);
            },
          ),
          const SizedBox(height: 24),
          const Text(
            'Navigation Style',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          SegmentedButton<NavbarStyle>(
            segments: const [
              ButtonSegment(
                value: NavbarStyle.standard,
                label: Text('Standard', style: TextStyle(fontSize: 12)),
                icon: Icon(Icons.horizontal_rule_rounded),
              ),
              ButtonSegment(
                value: NavbarStyle.hiddenLabels,
                label: Text('Hidden', style: TextStyle(fontSize: 12)),
                icon: Icon(Icons.more_horiz),
              ),
              ButtonSegment(
                value: NavbarStyle.floatingPill,
                label: Text('Floating', style: TextStyle(fontSize: 12)),
                icon: Icon(Icons.lens_blur),
              ),
            ],
            selected: {navbarStyle},
            onSelectionChanged: (Set<NavbarStyle> newSelection) {
              ref
                  .read(navbarStyleProvider.notifier)
                  .setStyle(newSelection.first);
            },
          ),
          const SizedBox(height: 24),
          const Text(
            'Color Theme',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          Consumer(
            builder: (context, ref, _) {
              final colorTheme = ref.watch(colorThemeProvider);
              return SegmentedButton<ColorThemeStyle>(
                segments: const [
                  ButtonSegment(
                    value: ColorThemeStyle.pastelPop,
                    label: Text('Pastel Pop', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.bubble_chart),
                  ),
                  ButtonSegment(
                    value: ColorThemeStyle.cyberNeon,
                    label: Text('Cyber Neon', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.bolt),
                  ),
                  ButtonSegment(
                    value: ColorThemeStyle.ultraMinimalist,
                    label: Text('Minimalist', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.architecture),
                  ),
                ],
                selected: {colorTheme},
                onSelectionChanged: (Set<ColorThemeStyle> newSelection) {
                  ref
                      .read(colorThemeProvider.notifier)
                      .setStyle(newSelection.first);
                },
              );
            },
          ),
          const SizedBox(height: 24),
          const Text(
            'Dark Mode',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          Consumer(
            builder: (context, ref, _) {
              final brightness = ref.watch(brightnessProvider);
              return SegmentedButton<ThemeMode>(
                segments: const [
                  ButtonSegment(
                    value: ThemeMode.system,
                    label: Text('System', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.settings_system_daydream),
                  ),
                  ButtonSegment(
                    value: ThemeMode.light,
                    label: Text('Light', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.light_mode),
                  ),
                  ButtonSegment(
                    value: ThemeMode.dark,
                    label: Text('Dark', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.dark_mode),
                  ),
                ],
                selected: {brightness},
                onSelectionChanged: (Set<ThemeMode> newSelection) {
                  ref
                      .read(brightnessProvider.notifier)
                      .setBrightness(newSelection.first);
                },
              );
            },
          ),
          const SizedBox(height: 24),
          const Text(
            'Ambient Mode',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 12),
          Consumer(
            builder: (context, ref, _) {
              final isAmbientEnabled = ref.watch(ambientModeProvider);
              return SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'Dynamic Ambient Glow',
                  style: TextStyle(fontSize: 14),
                ),
                subtitle: const Text(
                  'Creates a breathing light effect behind content',
                  style: TextStyle(fontSize: 12, color: Colors.grey),
                ),
                value: isAmbientEnabled,
                onChanged: (value) {
                  ref.read(ambientModeProvider.notifier).toggleAmbientMode();
                },
              );
            },
          ),
          const SizedBox(height: 12),
          Consumer(
            builder: (context, ref, _) {
              final physicsMode = ref.watch(ambientPhysicsProvider);
              return SegmentedButton<AmbientPhysicsMode>(
                segments: const [
                  ButtonSegment(
                    value: AmbientPhysicsMode.pulse,
                    label: Text('Pulse', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.waves),
                  ),
                  ButtonSegment(
                    value: AmbientPhysicsMode.aurora,
                    label: Text('Aurora', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.blur_on),
                  ),
                  ButtonSegment(
                    value: AmbientPhysicsMode.wave,
                    label: Text('Wave', style: TextStyle(fontSize: 12)),
                    icon: Icon(Icons.water),
                  ),
                ],
                selected: {physicsMode},
                onSelectionChanged: (Set<AmbientPhysicsMode> newSelection) {
                  ref
                      .read(ambientPhysicsProvider.notifier)
                      .setMode(newSelection.first);
                },
              );
            },
          ),
          const SizedBox(height: 24),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.phone_android),
            title: const Text('Verify Phone Number'),
            subtitle: const Text(
              'Link your phone number to secure your account',
            ),
            onTap: () {
              // Note: Implementation for phone verification in settings.
              // We just show a snackbar for now to signify the entry point.
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text(
                    'Phone Verification flow will open here. Please make sure Phone Auth is enabled in Firebase.',
                  ),
                ),
              );
            },
          ),
          ref
              .watch(isAdminProvider)
              .maybeWhen(
                data: (isAdmin) => isAdmin
                    ? ListTile(
                        leading: const Icon(Icons.shield_outlined),
                        title: const Text('Moderation Queue'),
                        subtitle: const Text('Review flagged content (admin)'),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => const AdminModerationView(),
                          ),
                        ),
                      )
                    : const SizedBox.shrink(),
                orElse: () => const SizedBox.shrink(),
              ),
          ListTile(
            leading: const Icon(Icons.logout, color: Colors.red),
            title: const Text('Log Out', style: TextStyle(color: Colors.red)),
            onTap: () {
              ref.read(authControllerProvider.notifier).signOut();
            },
          ),
        ],
      ),
    );
  }
}

class _LayoutCard extends StatelessWidget {
  final String label;
  final IconData icon;
  final Color color;
  final bool isSelected;
  final bool isNew;
  final VoidCallback onTap;

  const _LayoutCard({
    required this.label,
    required this.icon,
    required this.color,
    required this.isSelected,
    this.isNew = false,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding: const EdgeInsets.symmetric(vertical: 14),
              decoration: BoxDecoration(
                color: isSelected
                    ? color.withValues(alpha: 0.15)
                    : Theme.of(context).cardColor,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: isSelected ? color : Colors.grey.withValues(alpha: 0.3),
                  width: isSelected ? 2 : 1,
                ),
              ),
              child: Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(icon, color: isSelected ? color : Colors.grey, size: 26),
                    const SizedBox(height: 6),
                    Text(
                      label,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: isSelected ? color : Colors.grey,
                        fontWeight: isSelected ? FontWeight.w700 : FontWeight.w400,
                        fontSize: 12,
                      ),
                    ),
                    if (isSelected) ...[
                      const SizedBox(height: 4),
                      Container(
                        width: 6,
                        height: 6,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: color,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            if (isNew)
              Positioned(
                top: -6,
                right: -4,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                      colors: [Color(0xFF8E2DE2), Color(0xFF4A00E0)],
                    ),
                    borderRadius: BorderRadius.circular(8),
                    boxShadow: [
                      BoxShadow(
                        color: const Color(0xFF8E2DE2).withValues(alpha: 0.4),
                        blurRadius: 4,
                        offset: const Offset(0, 2),
                      ),
                    ],
                  ),
                  child: const Text(
                    'NEW',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 9,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 0.5,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
