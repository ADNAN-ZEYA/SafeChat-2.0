import 'package:json_annotation/json_annotation.dart';
import 'package:firebase_auth/firebase_auth.dart' as firebase;

part 'auth_models.g.dart';

@JsonSerializable()
class UserProfile {
  final String uid;
  final String username;
  @JsonKey(name: 'display_name')
  final String displayName;
  @JsonKey(name: 'phone_number')
  final String? phoneNumber;
  final String dob;
  final String? bio;
  @JsonKey(name: 'photo_url')
  final String? photoUrl;
  @JsonKey(name: 'background_url')
  final String? backgroundUrl;
  @JsonKey(name: 'safechat_protection')
  final bool? safechatProtection;
  @JsonKey(name: 'created_at')
  final String createdAt;
  @JsonKey(name: 'updated_at')
  final String updatedAt;

  UserProfile({
    required this.uid,
    required this.username,
    required this.displayName,
    this.phoneNumber,
    required this.dob,
    this.bio,
    this.photoUrl,
    this.backgroundUrl,
    this.safechatProtection,
    required this.createdAt,
    required this.updatedAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) =>
      _$UserProfileFromJson(json);
  Map<String, dynamic> toJson() => _$UserProfileToJson(this);
}

@JsonSerializable()
class OnboardRequest {
  final String username;
  @JsonKey(name: 'display_name')
  final String displayName;
  @JsonKey(name: 'phone_number')
  final String? phoneNumber;
  final String dob;
  final String? bio;

  OnboardRequest({
    required this.username,
    required this.displayName,
    this.phoneNumber,
    required this.dob,
    this.bio,
  });

  factory OnboardRequest.fromJson(Map<String, dynamic> json) =>
      _$OnboardRequestFromJson(json);
  Map<String, dynamic> toJson() => _$OnboardRequestToJson(this);
}

@JsonSerializable(includeIfNull: false)
class UpdateProfileRequest {
  @JsonKey(name: 'display_name')
  final String? displayName;
  final String? username;
  final String? bio;
  @JsonKey(name: 'photo_url')
  final String? photoUrl;
  @JsonKey(name: 'background_url')
  final String? backgroundUrl;

  UpdateProfileRequest({
    this.displayName,
    this.username,
    this.bio,
    this.photoUrl,
    this.backgroundUrl,
  });

  factory UpdateProfileRequest.fromJson(Map<String, dynamic> json) =>
      _$UpdateProfileRequestFromJson(json);
  Map<String, dynamic> toJson() => _$UpdateProfileRequestToJson(this);
}

@JsonSerializable()
class AuthMeResponse {
  final Map<String, dynamic> user;
  final UserProfile? profile;
  @JsonKey(name: 'needs_onboarding')
  final bool needsOnboarding;

  AuthMeResponse({
    required this.user,
    this.profile,
    required this.needsOnboarding,
  });

  factory AuthMeResponse.fromJson(Map<String, dynamic> json) =>
      _$AuthMeResponseFromJson(json);
  Map<String, dynamic> toJson() => _$AuthMeResponseToJson(this);
}

class AuthState {
  final firebase.User? user;
  final UserProfile? profile;
  final bool needsOnboarding;
  final bool isLoading;
  final String? error;

  AuthState({
    this.user,
    this.profile,
    this.needsOnboarding = false,
    this.isLoading = false,
    this.error,
  });

  bool get isAuthenticated => user != null;
  bool get isOnboarded => profile != null && !needsOnboarding;

  AuthState copyWith({
    firebase.User? user,
    UserProfile? profile,
    bool? needsOnboarding,
    bool? isLoading,
    String? error,
  }) {
    return AuthState(
      user: user ?? this.user,
      profile: profile ?? this.profile,
      needsOnboarding: needsOnboarding ?? this.needsOnboarding,
      isLoading: isLoading ?? this.isLoading,
      error: error ?? this.error,
    );
  }
}
