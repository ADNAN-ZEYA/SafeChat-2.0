import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Client-side E2E encryption for SafeChat Mode ("trusted" chats).
///
/// Deliberately simple: static X25519 keypairs + ECDH, NOT Signal Protocol /
/// Double Ratchet. There is NO forward secrecy — if a private key is ever
/// compromised, every past message encrypted to it is exposed. Upgrading to a
/// proper ratchet is documented future work (see docs/MODERATION.md and
/// docs/DATABASE_SCHEMA.md section 10).
class EncryptionService {
  EncryptionService({FlutterSecureStorage? secureStorage})
    : _secureStorage = secureStorage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _secureStorage;
  static final X25519 _keyAlgorithm = X25519();

  // AesGcm.with256bits() defaults: 12-byte nonce, 16-byte (128-bit) MAC tag.
  static const int _nonceLength = 12;
  static const int _macLength = 16;

  String _privateKeyStorageKey(String uid) => 'e2e_private_key_$uid';
  String _publicKeyStorageKey(String uid) => 'e2e_public_key_$uid';

  /// Generates a keypair for [uid] on first call (stores the private key in
  /// secure storage, never in Firestore or plaintext local storage) and
  /// publishes the public key to `users/{uid}.public_key`. No-op on
  /// subsequent calls once a local keypair already exists.
  Future<void> ensureKeyPairPublished(String uid, Dio dio) async {
    final existingPrivate = await _secureStorage.read(
      key: _privateKeyStorageKey(uid),
    );
    if (existingPrivate != null) return;

    final keyPair = await _keyAlgorithm.newKeyPair();
    final privateBytes = await keyPair.extractPrivateKeyBytes();
    final publicKey = await keyPair.extractPublicKey();

    final privateB64 = base64Encode(privateBytes);
    final publicB64 = base64Encode(publicKey.bytes);

    await _secureStorage.write(
      key: _privateKeyStorageKey(uid),
      value: privateB64,
    );
    await _secureStorage.write(
      key: _publicKeyStorageKey(uid),
      value: publicB64,
    );

    await dio.patch('/api/v1/auth/profile', data: {'public_key': publicB64});
  }

  Future<SimpleKeyPairData> _loadOwnKeyPair(String uid) async {
    final privateB64 = await _secureStorage.read(
      key: _privateKeyStorageKey(uid),
    );
    final publicB64 = await _secureStorage.read(key: _publicKeyStorageKey(uid));
    if (privateB64 == null || publicB64 == null) {
      throw StateError(
        'No local E2E keypair for this account yet — reopen the app to generate one.',
      );
    }
    return SimpleKeyPairData(
      base64Decode(privateB64),
      publicKey: SimplePublicKey(
        base64Decode(publicB64),
        type: KeyPairType.x25519,
      ),
      type: KeyPairType.x25519,
    );
  }

  /// Derives the shared symmetric key for a 1:1 chat between [myUid] and the
  /// other participant (identified by their base64 public key). Static-static
  /// ECDH is symmetric, so this same key is used for both encrypting messages
  /// this device sends and decrypting messages either side sent.
  Future<SecretKey> deriveSharedKey({
    required String myUid,
    required String otherPublicKeyB64,
  }) async {
    final myKeyPair = await _loadOwnKeyPair(myUid);
    final remotePublicKey = SimplePublicKey(
      base64Decode(otherPublicKeyB64),
      type: KeyPairType.x25519,
    );

    final sharedSecret = await _keyAlgorithm.sharedSecretKey(
      keyPair: myKeyPair,
      remotePublicKey: remotePublicKey,
    );

    // HKDF the raw ECDH output into a key sized for the AEAD cipher below,
    // rather than using the shared secret bytes directly.
    final hkdf = Hkdf(hmac: Hmac.sha256(), outputLength: 32);
    return hkdf.deriveKey(
      secretKey: sharedSecret,
      info: utf8.encode('safechat-e2e-v1'),
    );
  }

  /// Encrypts [plaintext] with [sharedKey]. Returns a base64 string
  /// (nonce + ciphertext + MAC tag concatenated) safe to store as the
  /// message `text` field.
  Future<String> encryptText(String plaintext, SecretKey sharedKey) async {
    final cipher = AesGcm.with256bits();
    final secretBox = await cipher.encrypt(
      utf8.encode(plaintext),
      secretKey: sharedKey,
    );
    final combined = <int>[
      ...secretBox.nonce,
      ...secretBox.cipherText,
      ...secretBox.mac.bytes,
    ];
    return base64Encode(combined);
  }

  /// Reverses [encryptText]. Throws if the ciphertext is malformed or the
  /// MAC doesn't verify (wrong key / tampered data).
  Future<String> decryptText(String ciphertextB64, SecretKey sharedKey) async {
    final combined = base64Decode(ciphertextB64);
    if (combined.length < _nonceLength + _macLength) {
      throw const FormatException('Ciphertext too short to be valid.');
    }

    final nonce = combined.sublist(0, _nonceLength);
    final mac = combined.sublist(combined.length - _macLength);
    final cipherText = combined.sublist(
      _nonceLength,
      combined.length - _macLength,
    );

    final cipher = AesGcm.with256bits();
    final clearBytes = await cipher.decrypt(
      SecretBox(cipherText, nonce: nonce, mac: Mac(mac)),
      secretKey: sharedKey,
    );
    return utf8.decode(clearBytes);
  }
}

final encryptionServiceProvider = Provider<EncryptionService>((ref) {
  return EncryptionService();
});
