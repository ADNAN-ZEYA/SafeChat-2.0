import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../providers/firebase_storage_provider.dart';
import 'shimmer.dart';

/// Stable cache key for a (possibly signed) media URL.
///
/// Our private GCS bucket re-signs media URLs on every backend fetch, so the
/// query string (signature + timestamp) changes constantly. Keying the cache on
/// the path only (URL minus query) means the same object hits the cache across
/// re-signs and app sessions instead of re-downloading every single time.
String stableCacheKey(String url) => url.split('?').first;

class FirebaseCachedNetworkImage extends ConsumerWidget {
  final String imageUrl;
  final BoxFit? fit;
  final Alignment alignment;
  final Widget Function(BuildContext, String)? placeholder;
  final Widget Function(BuildContext, String, dynamic)? errorWidget;

  /// Decode the image at this target width (device pixels). Big win for grids /
  /// thumbnails: avoids decoding a 4000px photo into a 130px cell.
  final int? memCacheWidth;

  const FirebaseCachedNetworkImage({
    super.key,
    required this.imageUrl,
    this.fit,
    this.alignment = Alignment.center,
    this.placeholder,
    this.errorWidget,
    this.memCacheWidth,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (imageUrl.isEmpty) {
      return errorWidget?.call(context, imageUrl, 'Empty URL') ??
          const SizedBox.shrink();
    }

    if (imageUrl.startsWith('http://') || imageUrl.startsWith('https://')) {
      return CachedNetworkImage(
        imageUrl: imageUrl,
        cacheKey: stableCacheKey(imageUrl),
        fit: fit,
        alignment: alignment,
        memCacheWidth: memCacheWidth,
        fadeInDuration: const Duration(milliseconds: 200),
        placeholder: placeholder ?? (ctx, _) => const ShimmerFill(),
        errorWidget: errorWidget,
      );
    }

    final asyncUrl = ref.watch(firebaseImageUrlProvider(imageUrl));

    return asyncUrl.when(
      data: (resolvedUrl) {
        return CachedNetworkImage(
          imageUrl: resolvedUrl,
          cacheKey: stableCacheKey(resolvedUrl),
          fit: fit,
          alignment: alignment,
          memCacheWidth: memCacheWidth,
          fadeInDuration: const Duration(milliseconds: 200),
          placeholder: placeholder ?? (ctx, _) => const ShimmerFill(),
          errorWidget: errorWidget,
        );
      },
      loading: () =>
          placeholder?.call(context, imageUrl) ?? const ShimmerFill(),
      error: (e, _) =>
          errorWidget?.call(context, imageUrl, e) ??
          const Center(child: Icon(Icons.broken_image_outlined)),
    );
  }
}

class FirebaseImageProviderWrapper {
  /// A helper for `backgroundImage` slots like CircleAvatar.
  static ImageProvider? getProvider(WidgetRef ref, String rawUrl) {
    if (rawUrl.isEmpty) return null;
    if (rawUrl.startsWith('http://') || rawUrl.startsWith('https://')) {
      return CachedNetworkImageProvider(
        rawUrl,
        cacheKey: stableCacheKey(rawUrl),
      );
    }
    final asyncUrl = ref.watch(firebaseImageUrlProvider(rawUrl));
    return asyncUrl.when(
      data: (url) =>
          CachedNetworkImageProvider(url, cacheKey: stableCacheKey(url)),
      loading: () => null, // Will just show background color
      error: (_, _) => null,
    );
  }
}
