// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'auth_models.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

UserProfile _$UserProfileFromJson(Map<String, dynamic> json) => UserProfile(
  uid: json['uid'] as String,
  username: json['username'] as String,
  displayName: json['display_name'] as String,
  phoneNumber: json['phone_number'] as String?,
  dob: json['dob'] as String,
  bio: json['bio'] as String?,
  photoUrl: json['photo_url'] as String?,
  backgroundUrl: json['background_url'] as String?,
  safechatProtection: json['safechat_protection'] as bool?,
  createdAt: json['created_at'] as String,
  updatedAt: json['updated_at'] as String,
);

Map<String, dynamic> _$UserProfileToJson(UserProfile instance) =>
    <String, dynamic>{
      'uid': instance.uid,
      'username': instance.username,
      'display_name': instance.displayName,
      'phone_number': instance.phoneNumber,
      'dob': instance.dob,
      'bio': instance.bio,
      'photo_url': instance.photoUrl,
      'background_url': instance.backgroundUrl,
      'safechat_protection': instance.safechatProtection,
      'created_at': instance.createdAt,
      'updated_at': instance.updatedAt,
    };

OnboardRequest _$OnboardRequestFromJson(Map<String, dynamic> json) =>
    OnboardRequest(
      username: json['username'] as String,
      displayName: json['display_name'] as String,
      phoneNumber: json['phone_number'] as String?,
      dob: json['dob'] as String,
      bio: json['bio'] as String?,
    );

Map<String, dynamic> _$OnboardRequestToJson(OnboardRequest instance) =>
    <String, dynamic>{
      'username': instance.username,
      'display_name': instance.displayName,
      'phone_number': instance.phoneNumber,
      'dob': instance.dob,
      'bio': instance.bio,
    };

UpdateProfileRequest _$UpdateProfileRequestFromJson(
  Map<String, dynamic> json,
) => UpdateProfileRequest(
  displayName: json['display_name'] as String?,
  username: json['username'] as String?,
  bio: json['bio'] as String?,
  photoUrl: json['photo_url'] as String?,
  backgroundUrl: json['background_url'] as String?,
);

Map<String, dynamic> _$UpdateProfileRequestToJson(
  UpdateProfileRequest instance,
) => <String, dynamic>{
  'display_name': ?instance.displayName,
  'username': ?instance.username,
  'bio': ?instance.bio,
  'photo_url': ?instance.photoUrl,
  'background_url': ?instance.backgroundUrl,
};

AuthMeResponse _$AuthMeResponseFromJson(Map<String, dynamic> json) =>
    AuthMeResponse(
      user: json['user'] as Map<String, dynamic>,
      profile: json['profile'] == null
          ? null
          : UserProfile.fromJson(json['profile'] as Map<String, dynamic>),
      needsOnboarding: json['needs_onboarding'] as bool,
    );

Map<String, dynamic> _$AuthMeResponseToJson(AuthMeResponse instance) =>
    <String, dynamic>{
      'user': instance.user,
      'profile': instance.profile,
      'needs_onboarding': instance.needsOnboarding,
    };
