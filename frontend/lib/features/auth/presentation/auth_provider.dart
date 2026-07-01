import 'dart:async';

import 'package:hive_flutter/hive_flutter.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/network/dio_client.dart';
import '../../chat/data/encryption_service.dart';
import '../data/auth_api_service.dart';
import '../data/auth_repository.dart';
import '../domain/models/auth_models.dart';

final authApiServiceProvider = Provider<AuthApiService>((ref) {
  final dio = ref.watch(dioProvider);
  return AuthApiService(dio);
});

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  final apiService = ref.watch(authApiServiceProvider);
  return AuthRepository(apiService: apiService);
});

class AuthController extends Notifier<AuthState> {
  @override
  AuthState build() {
    final isCachedAuth = Hive.box(
      'settings',
    ).get('isAuthenticated', defaultValue: false);
    if (isCachedAuth) {
      // Trigger background verification but start as loading to skip splash & redirect
      Future.microtask(() => checkAuthStatus());
      return AuthState(isLoading: true);
    }
    return AuthState();
  }

  Future<void> checkAuthStatus() async {
    state = state.copyWith(isLoading: true, error: null);
    final repo = ref.read(authRepositoryProvider);
    final result = await repo.checkAuthStatus();

    // Cache auth state
    if (result.isAuthenticated && !result.needsOnboarding) {
      Hive.box('settings').put('isAuthenticated', true);
      _ensureE2eKeys(result.user!.uid);
    } else {
      Hive.box('settings').put('isAuthenticated', false);
    }

    state = result.copyWith(isLoading: false);
  }

  /// Generates (once per device) and publishes this user's SafeChat Mode
  /// E2E keypair. Fire-and-forget: a failure here must never block login —
  /// chats simply stay unavailable for SafeChat Mode until it succeeds on a
  /// later login.
  void _ensureE2eKeys(String uid) {
    final dio = ref.read(dioProvider);
    final encryption = ref.read(encryptionServiceProvider);
    unawaited(encryption.ensureKeyPairPublished(uid, dio).catchError((_) {}));
  }

  Future<void> signUpWithEmailAndPassword({
    required String email,
    required String password,
  }) async {
    state = state.copyWith(isLoading: true, error: null);
    final repo = ref.read(authRepositoryProvider);
    final result = await repo.signUpWithEmailAndPassword(email, password);
    state = result.copyWith(isLoading: false);
  }

  Future<void> signInWithEmailAndPassword({
    required String email,
    required String password,
  }) async {
    state = state.copyWith(isLoading: true, error: null);
    final repo = ref.read(authRepositoryProvider);
    final result = await repo.signInWithEmailAndPassword(email, password);

    if (result.isAuthenticated && !result.needsOnboarding) {
      Hive.box('settings').put('isAuthenticated', true);
      _ensureE2eKeys(result.user!.uid);
    }

    state = result.copyWith(isLoading: false);
  }

  Future<void> signInWithGoogle() async {
    state = state.copyWith(isLoading: true, error: null);

    final repo = ref.read(authRepositoryProvider);
    final result = await repo.signInWithGoogle();

    if (result.isAuthenticated && !result.needsOnboarding) {
      Hive.box('settings').put('isAuthenticated', true);
      _ensureE2eKeys(result.user!.uid);
    }

    state = result.copyWith(isLoading: false);
  }

  Future<void> onboard({
    required String username,
    required String displayName,
    String? phoneNumber,
    required String dob,
    String? bio,
  }) async {
    state = state.copyWith(isLoading: true, error: null);

    final repo = ref.read(authRepositoryProvider);
    final result = await repo.onboard(
      username: username,
      displayName: displayName,
      phoneNumber: phoneNumber,
      dob: dob,
      bio: bio,
    );

    if (result.isAuthenticated && !result.needsOnboarding) {
      Hive.box('settings').put('isAuthenticated', true);
      _ensureE2eKeys(result.user!.uid);
    }

    state = result.copyWith(isLoading: false);
  }

  Future<void> updateProfile({
    String? displayName,
    String? username,
    String? bio,
    String? photoUrl,
    String? backgroundUrl,
  }) async {
    state = state.copyWith(isLoading: true, error: null);

    final repo = ref.read(authRepositoryProvider);
    final result = await repo.updateProfile(
      displayName: displayName,
      username: username,
      bio: bio,
      photoUrl: photoUrl,
      backgroundUrl: backgroundUrl,
    );

    state = result.copyWith(isLoading: false);
  }

  Future<void> signOut() async {
    final repo = ref.read(authRepositoryProvider);
    await repo.signOut();
    Hive.box('settings').put('isAuthenticated', false);
    state = AuthState();
  }
}

final authControllerProvider = NotifierProvider<AuthController, AuthState>(() {
  return AuthController();
});

// For backward compatibility with the router if needed, or we can just use the controller
final authStateProvider = Provider<AuthState>((ref) {
  return ref.watch(authControllerProvider);
});
