// frontend/lib/features/home/data/feed_cache_service.dart

import 'dart:convert';
import 'package:hive_flutter/hive_flutter.dart';
import 'feed_post_model.dart';

class FeedCacheService {
  static const _boxName = 'feed_cache_box';

  static Future<void> init() async {
    if (!Hive.isBoxOpen(_boxName)) {
      await Hive.openBox<String>(_boxName);
    }
  }

  static List<FeedPost>? getCachedFeed(String type) {
    try {
      if (!Hive.isBoxOpen(_boxName)) return null;
      final box = Hive.box<String>(_boxName);
      final jsonString = box.get(type);
      if (jsonString == null || jsonString.isEmpty) return null;

      final List<dynamic> list = jsonDecode(jsonString);
      return list
          .map((e) => FeedPost.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
    } catch (_) {
      return null;
    }
  }

  static Future<void> cacheFeed(String type, List<FeedPost> posts) async {
    try {
      if (!Hive.isBoxOpen(_boxName)) {
        await init();
      }
      final box = Hive.box<String>(_boxName);
      // Serialize post objects to JSON list
      final jsonList = posts.map((p) => p.toJson()).toList();
      await box.put(type, jsonEncode(jsonList));
    } catch (_) {}
  }
}
