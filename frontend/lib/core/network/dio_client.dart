import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Local-dev fallback backend (Android emulator loopback). Only used in debug
/// builds — a release build with no API_BASE_URL is a misconfiguration and
/// must fail loudly rather than silently target a dev address (HC-04).
const _devFallbackBaseUrl = 'http://10.0.2.2:8000';

String _resolveBaseUrl() {
  final configured = dotenv.isInitialized ? dotenv.env['API_BASE_URL'] : null;
  if (configured != null && configured.isNotEmpty) return configured;
  if (kReleaseMode) {
    throw StateError(
      'API_BASE_URL is not set. A release build must be built with a real '
      'backend URL in .env (see frontend/.env.example).',
    );
  }
  return _devFallbackBaseUrl;
}

final dioProvider = Provider<Dio>((ref) {
  final dio = Dio();
  dio.options.baseUrl = _resolveBaseUrl();
  dio.options.connectTimeout = const Duration(seconds: 10);
  dio.options.receiveTimeout = const Duration(seconds: 10);
  dio.options.headers['Accept-Encoding'] = 'gzip';

  dio.interceptors.add(
    InterceptorsWrapper(
      onRequest: (options, handler) async {
        // Attempt to attach Firebase ID Token to every request
        final user = FirebaseAuth.instance.currentUser;
        if (user != null) {
          try {
            // getIdToken(false) uses the cached token if valid (~1 hour TTL)
            final token = await user.getIdToken(false);
            if (token != null) {
              options.headers['Authorization'] = 'Bearer $token';
            }
          } catch (e) {
            // Token refresh might fail if offline or logged out
          }
        }
        return handler.next(options);
      },
      onError: (error, handler) {
        // Handle global error responses here if needed (e.g., refreshing token)
        return handler.next(error);
      },
    ),
  );

  return dio;
});
