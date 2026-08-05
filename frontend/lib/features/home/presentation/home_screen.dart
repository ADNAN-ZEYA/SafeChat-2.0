import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../../theme/theme_provider.dart';
import 'feed_view.dart';
import 'search_view.dart';
import 'create_post_view.dart';
import '../../chat/presentation/chat_list_view.dart';
import '../../profile/presentation/profile_view.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  int _currentIndex = 0;

  final List<Widget> _views = const [
    FeedView(),
    SearchView(),
    CreatePostView(),
    ChatListView(),
    ProfileView(),
  ];

  void _onTabSelected(int index) {
    if (index == 2) {
      // Trigger the Create Post Bottom Sheet
      showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        backgroundColor: Colors.transparent,
        builder: (context) => const CreatePostView(),
      );
      return;
    }

    setState(() {
      _currentIndex = index;
    });
  }

  @override
  Widget build(BuildContext context) {
    final navbarStyle = ref.watch(navbarStyleProvider);

    return Scaffold(
      extendBody: true,
      body: IndexedStack(index: _currentIndex, children: _views)
          .animate()
          .fadeIn(duration: 400.ms, curve: Curves.easeOut)
          .slideY(begin: 0.05, duration: 350.ms, curve: Curves.easeOutCubic),
      bottomNavigationBar: _buildNavbar(navbarStyle),
    );
  }

  Widget _buildNavbar(NavbarStyle style) {
    switch (style) {
      case NavbarStyle.floatingPill:
        return _buildFloatingPillNav();
      case NavbarStyle.hiddenLabels:
        return _buildStandardNav(
          labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
        );
      case NavbarStyle.standard:
        return _buildStandardNav(
          labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        );
    }
  }

  Widget _buildStandardNav({
    required NavigationDestinationLabelBehavior labelBehavior,
  }) {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
        child: NavigationBar(
          backgroundColor: Theme.of(
            context,
          ).colorScheme.surface.withValues(alpha: 0.75),
          elevation: 0,
          selectedIndex: _currentIndex,
          onDestinationSelected: _onTabSelected,
          labelBehavior: labelBehavior,
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.home_outlined),
              selectedIcon: Icon(Icons.home_rounded, color: Color(0xFF8E2DE2)),
              label: 'Home',
            ),
            NavigationDestination(
              icon: Icon(Icons.search_outlined),
              selectedIcon: Icon(Icons.search_rounded, color: Color(0xFF8E2DE2)),
              label: 'Search',
            ),
            NavigationDestination(
              icon: Icon(Icons.add_circle_outline),
              selectedIcon: Icon(Icons.add_circle_rounded, color: Color(0xFF8E2DE2)),
              label: 'Post',
            ),
            NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline),
              selectedIcon: Icon(Icons.chat_bubble_rounded, color: Color(0xFF8E2DE2)),
              label: 'Chat',
            ),
            NavigationDestination(
              icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person_rounded, color: Color(0xFF8E2DE2)),
              label: 'Profile',
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFloatingPillNav() {
    final colorScheme = Theme.of(context).colorScheme;
    final alignmentX = -1.0 + (_currentIndex * 0.5);

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.only(left: 16, right: 16, bottom: 12),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(36),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
            child: Container(
              height: 68,
              decoration: BoxDecoration(
                color: colorScheme.surfaceContainerHighest.withValues(alpha: 0.65),
                borderRadius: BorderRadius.circular(36),
                border: Border.all(
                  color: colorScheme.primary.withValues(alpha: 0.25),
                  width: 1.5,
                ),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.15),
                    blurRadius: 24,
                    offset: const Offset(0, 8),
                  ),
                ],
              ),
              child: Stack(
                children: [
                  // Active Animated Glass Indicator Pill
                  AnimatedAlign(
                    duration: const Duration(milliseconds: 350),
                    curve: Curves.easeOutCubic,
                    alignment: Alignment(alignmentX, 0),
                    child: FractionallySizedBox(
                      widthFactor: 1 / 5,
                      heightFactor: 0.85,
                      child: Container(
                        margin: const EdgeInsets.all(4.0),
                        decoration: BoxDecoration(
                          color: colorScheme.primary.withValues(alpha: 0.18),
                          borderRadius: BorderRadius.circular(28),
                          border: Border.all(
                            color: colorScheme.primary.withValues(alpha: 0.35),
                          ),
                        ),
                      ),
                    ),
                  ),
                  // Navigation Items Row
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _buildFloatingPillItem(
                        Icons.home_outlined,
                        Icons.home_rounded,
                        'Home',
                        0,
                      ),
                      _buildFloatingPillItem(
                        Icons.search_outlined,
                        Icons.search_rounded,
                        'Search',
                        1,
                      ),
                      // Prominent Center "+" Action Button
                      Expanded(
                        child: GestureDetector(
                          onTap: () => _onTabSelected(2),
                          behavior: HitTestBehavior.opaque,
                          child: Center(
                            child: Container(
                              width: 48,
                              height: 48,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                gradient: const LinearGradient(
                                  colors: [Color(0xFF8E2DE2), Color(0xFF4A00E0)],
                                  begin: Alignment.topLeft,
                                  end: Alignment.bottomRight,
                                ),
                                boxShadow: [
                                  BoxShadow(
                                    color: const Color(0xFF8E2DE2).withValues(alpha: 0.45),
                                    blurRadius: 12,
                                    offset: const Offset(0, 4),
                                  ),
                                ],
                              ),
                              child: const Icon(
                                Icons.add_rounded,
                                color: Colors.white,
                                size: 28,
                              ),
                            ),
                          ),
                        ),
                      ),
                      _buildFloatingPillItem(
                        Icons.chat_bubble_outline,
                        Icons.chat_bubble_rounded,
                        'Chat',
                        3,
                      ),
                      _buildFloatingPillItem(
                        Icons.person_outline,
                        Icons.person_rounded,
                        'Profile',
                        4,
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildFloatingPillItem(
    IconData icon,
    IconData selectedIcon,
    String label,
    int index,
  ) {
    final isSelected = _currentIndex == index;
    final colorScheme = Theme.of(context).colorScheme;

    return Expanded(
      child: GestureDetector(
        onTap: () => _onTabSelected(index),
        behavior: HitTestBehavior.opaque,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 200),
                transitionBuilder: (child, animation) {
                  return ScaleTransition(scale: animation, child: child);
                },
                child: Icon(
                  isSelected ? selectedIcon : icon,
                  key: ValueKey<bool>(isSelected),
                  size: isSelected ? 24 : 22,
                  color: isSelected
                      ? colorScheme.primary
                      : colorScheme.onSurfaceVariant,
                ),
              ),
              if (isSelected) ...[
                const SizedBox(height: 2),
                Container(
                  width: 4,
                  height: 4,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: colorScheme.primary,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
