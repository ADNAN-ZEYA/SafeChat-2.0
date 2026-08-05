import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:hive_flutter/hive_flutter.dart';

class AmbientModeNotifier extends Notifier<bool> {
  @override
  bool build() {
    try {
      final box = Hive.box('settings');
      return box.get('ambient_mode', defaultValue: true) as bool;
    } catch (e) {
      return true; // Fallback if Hive fails to initialize
    }
  }

  void toggleAmbientMode() {
    try {
      final box = Hive.box('settings');
      final newState = !state;
      box.put('ambient_mode', newState);
      state = newState;
    } catch (e) {
      state = !state;
    }
  }
}

final ambientModeProvider = NotifierProvider<AmbientModeNotifier, bool>(() {
  return AmbientModeNotifier();
});

enum FeedLayoutMode { grid, card, spatialDeck }

class FeedLayoutNotifier extends Notifier<FeedLayoutMode> {
  @override
  FeedLayoutMode build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get('feed_layout', defaultValue: FeedLayoutMode.grid.index)
              as int;
      if (index >= 0 && index < FeedLayoutMode.values.length) {
        return FeedLayoutMode.values[index];
      }
      return FeedLayoutMode.grid;
    } catch (e) {
      return FeedLayoutMode.grid;
    }
  }

  void toggleLayout() {
    final nextIndex = (state.index + 1) % FeedLayoutMode.values.length;
    setLayout(FeedLayoutMode.values[nextIndex]);
  }

  void setLayout(FeedLayoutMode mode) {
    try {
      final box = Hive.box('settings');
      box.put('feed_layout', mode.index);
      state = mode;
    } catch (e) {
      state = mode;
    }
  }
}

class BrightnessNotifier extends Notifier<ThemeMode> {
  @override
  ThemeMode build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get('theme_mode', defaultValue: ThemeMode.system.index) as int;
      return ThemeMode.values[index];
    } catch (e) {
      return ThemeMode.system;
    }
  }

  void toggleBrightness() {
    if (state == ThemeMode.system) {
      setBrightness(ThemeMode.dark);
    } else if (state == ThemeMode.light) {
      setBrightness(ThemeMode.dark);
    } else {
      setBrightness(ThemeMode.light);
    }
  }

  void setBrightness(ThemeMode mode) {
    try {
      final box = Hive.box('settings');
      box.put('theme_mode', mode.index);
      state = mode;
    } catch (e) {
      state = mode;
    }
  }
}

final feedLayoutProvider = NotifierProvider<FeedLayoutNotifier, FeedLayoutMode>(
  () {
    return FeedLayoutNotifier();
  },
);

final brightnessProvider = NotifierProvider<BrightnessNotifier, ThemeMode>(() {
  return BrightnessNotifier();
});

enum NavbarStyle { standard, hiddenLabels, floatingPill }

class NavbarStyleNotifier extends Notifier<NavbarStyle> {
  @override
  NavbarStyle build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get('navbar_style', defaultValue: NavbarStyle.standard.index)
              as int;
      return NavbarStyle.values[index];
    } catch (e) {
      return NavbarStyle.standard;
    }
  }

  void setStyle(NavbarStyle style) {
    try {
      final box = Hive.box('settings');
      box.put('navbar_style', style.index);
      state = style;
    } catch (e) {
      state = style;
    }
  }
}

final navbarStyleProvider = NotifierProvider<NavbarStyleNotifier, NavbarStyle>(
  () {
    return NavbarStyleNotifier();
  },
);

enum ColorThemeStyle { pastelPop, cyberNeon, ultraMinimalist }

class ColorThemeNotifier extends Notifier<ColorThemeStyle> {
  @override
  ColorThemeStyle build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get('color_theme', defaultValue: ColorThemeStyle.pastelPop.index)
              as int;
      return ColorThemeStyle.values[index];
    } catch (e) {
      return ColorThemeStyle.pastelPop;
    }
  }

  void setStyle(ColorThemeStyle style) {
    try {
      final box = Hive.box('settings');
      box.put('color_theme', style.index);
      state = style;
    } catch (e) {
      state = style;
    }
  }
}

final colorThemeProvider =
    NotifierProvider<ColorThemeNotifier, ColorThemeStyle>(() {
      return ColorThemeNotifier();
    });

enum ProfileLayoutStyle { modernCover, centeredMinimalist }

class ProfileLayoutNotifier extends Notifier<ProfileLayoutStyle> {
  @override
  ProfileLayoutStyle build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get(
                'profile_layout',
                defaultValue: ProfileLayoutStyle.modernCover.index,
              )
              as int;
      return ProfileLayoutStyle.values[index];
    } catch (e) {
      return ProfileLayoutStyle.modernCover;
    }
  }

  void setStyle(ProfileLayoutStyle style) {
    try {
      final box = Hive.box('settings');
      box.put('profile_layout', style.index);
      state = style;
    } catch (e) {
      state = style;
    }
  }
}

final profileLayoutProvider =
    NotifierProvider<ProfileLayoutNotifier, ProfileLayoutStyle>(() {
      return ProfileLayoutNotifier();
    });

enum AmbientPhysicsMode { pulse, aurora, wave }

class AmbientPhysicsNotifier extends Notifier<AmbientPhysicsMode> {
  @override
  AmbientPhysicsMode build() {
    try {
      final box = Hive.box('settings');
      final index = box.get('ambient_physics', defaultValue: 0) as int;
      return AmbientPhysicsMode.values[index];
    } catch (e) {
      return AmbientPhysicsMode.pulse;
    }
  }

  void setMode(AmbientPhysicsMode mode) {
    try {
      final box = Hive.box('settings');
      box.put('ambient_physics', mode.index);
      state = mode;
    } catch (e) {
      state = mode;
    }
  }
}

final ambientPhysicsProvider =
    NotifierProvider<AmbientPhysicsNotifier, AmbientPhysicsMode>(() {
      return AmbientPhysicsNotifier();
    });

enum PostImageLayoutStyle { edgeToEdge, padded }

class PostImageLayoutNotifier extends Notifier<PostImageLayoutStyle> {
  @override
  PostImageLayoutStyle build() {
    try {
      final box = Hive.box('settings');
      final index =
          box.get(
                'post_image_layout',
                defaultValue: PostImageLayoutStyle.edgeToEdge.index,
              )
              as int;
      return PostImageLayoutStyle.values[index];
    } catch (e) {
      return PostImageLayoutStyle.edgeToEdge;
    }
  }

  void setStyle(PostImageLayoutStyle style) {
    try {
      final box = Hive.box('settings');
      box.put('post_image_layout', style.index);
      state = style;
    } catch (e) {
      state = style;
    }
  }
}

final postImageLayoutProvider =
    NotifierProvider<PostImageLayoutNotifier, PostImageLayoutStyle>(() {
      return PostImageLayoutNotifier();
    });
