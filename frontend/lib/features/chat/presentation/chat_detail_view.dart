import 'package:cryptography/cryptography.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../../../../core/network/dio_client.dart';
import '../../../../shared/widgets/firebase_image.dart';
import '../../moderation/data/moderation_models.dart';
import '../../moderation/presentation/flagged_content_dialog.dart';
import '../../profile/presentation/follow_providers.dart';
import '../data/encryption_service.dart';
import 'chat_encryption_providers.dart';
import 'presence_provider.dart';

class ChatDetailView extends ConsumerStatefulWidget {
  final String chatId;
  final String otherUid;
  final String otherUserName;

  const ChatDetailView({
    super.key,
    required this.chatId,
    required this.otherUid,
    required this.otherUserName,
  });

  @override
  ConsumerState<ChatDetailView> createState() => _ChatDetailViewState();
}

class _ChatDetailViewState extends ConsumerState<ChatDetailView> {
  final TextEditingController _messageController = TextEditingController();
  SecretKey? _sharedKey;
  bool _isUploading = false;

  @override
  void initState() {
    super.initState();
    _markChatRead();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(presenceControllerProvider).setPresence(
            widget.chatId,
            isTyping: false,
            isViewing: true,
          );
    });
  }

  Future<void> _markChatRead() async {
    try {
      await ref.read(dioProvider).post('/api/v1/chats/${widget.chatId}/read');
    } catch (_) {}
  }

  @override
  void dispose() {
    ref.read(presenceControllerProvider).setPresence(
          widget.chatId,
          isTyping: false,
          isViewing: false,
        );
    _messageController.dispose();
    super.dispose();
  }

  void _showSafeChatProtectionDialog(String message) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        title: const Row(
          children: [
            Icon(Icons.shield_outlined, color: Color(0xFF8E2DE2), size: 28),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'SafeChat Protection Active',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        content: Text(
          message.isNotEmpty
              ? message
              : 'This user has SafeChat Protection enabled. Inappropriate or harmful messages cannot be sent to this user.',
          style: const TextStyle(fontSize: 14, height: 1.4),
        ),
        actions: [
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xFF8E2DE2),
            ),
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Got It'),
          ),
        ],
      ),
    );
  }

  Future<void> _sendMessage({
    String? mediaUrl,
    String mediaType = 'text',
    Map<String, dynamic>? metadata,
  }) async {
    final text = _messageController.text.trim();
    if (text.isEmpty && mediaUrl == null) return;

    final messenger = ScaffoldMessenger.of(context);
    final dio = ref.read(dioProvider);

    final String mode;
    try {
      mode = await ref.read(chatEncryptionModeProvider(widget.chatId).future);
    } catch (_) {
      messenger.showSnackBar(
        const SnackBar(
          content: Text(
            'Could not verify this chat\'s SafeChat Mode — message not sent.',
          ),
        ),
      );
      return;
    }

    if (mode == 'trusted') {
      final sharedKey = _sharedKey;
      if (sharedKey == null) {
        messenger.showSnackBar(
          const SnackBar(
            content: Text('Encryption isn\'t ready yet. Try again shortly.'),
          ),
        );
        return;
      }
      try {
        final ciphertext = await ref
            .read(encryptionServiceProvider)
            .encryptText(text.isNotEmpty ? text : '[Media]', sharedKey);
        await dio.post(
          '/api/v1/chats/${widget.chatId}/messages',
          data: {
            'text': ciphertext,
            'image_url': mediaUrl,
            'media_type': mediaType,
            'metadata': metadata ?? {},
            'submit_for_review': false,
          },
        );
        _messageController.clear();
        ref.read(presenceControllerProvider).setPresence(
              widget.chatId,
              isTyping: false,
              isViewing: true,
            );
      } catch (e) {
        messenger.showSnackBar(
          SnackBar(content: Text('Failed to send message: $e')),
        );
      }
      return;
    }

    Future<Response<dynamic>> post({required bool submitForReview}) {
      return dio.post(
        '/api/v1/chats/${widget.chatId}/messages',
        data: {
          'text': text.isNotEmpty ? text : (mediaUrl ?? 'Media'),
          'image_url': mediaUrl,
          'media_type': mediaType,
          'metadata': metadata ?? {},
          'submit_for_review': submitForReview,
        },
        options: Options(
          validateStatus: (s) =>
              s != null && ((s >= 200 && s < 300) || s == 422),
        ),
      );
    }

    try {
      final response = await post(submitForReview: false);
      if (response.statusCode != 422) {
        _messageController.clear();
        ref.read(presenceControllerProvider).setPresence(
              widget.chatId,
              isTyping: false,
              isViewing: true,
            );
        return;
      }

      final errorData = response.data as Map<String, dynamic>?;
      final errObj = (errorData?['error'] as Map<String, dynamic>?) ??
          (errorData?['detail'] as Map<String, dynamic>?);
      final code = errObj?['code'];

      if (code == 'SAFECHAT_PROTECTION_BLOCKED') {
        final msg = errObj?['message'] as String? ??
            'This user has SafeChat Protection enabled. Inappropriate or harmful messages cannot be sent to this user.';
        _showSafeChatProtectionDialog(msg);
        return;
      }

      final flagged = flaggedFromEnvelope(response.data);
      if (!mounted || flagged == null) return;
      final result = await showFlaggedContentDialog(
        context,
        text: text,
        matches: flagged.matches,
        contentNoun: 'message',
      );
      if (result == null || !result.submitForReview) return;

      await post(submitForReview: true);
      _messageController.clear();
      messenger.showSnackBar(
        const SnackBar(
          content: Text(
            '📋 Message sent for review. Track it in Profile → Appeals.',
          ),
        ),
      );
    } on DioException catch (e) {
      final errorData = e.response?.data as Map<String, dynamic>?;
      final errObj = (errorData?['error'] as Map<String, dynamic>?) ??
          (errorData?['detail'] as Map<String, dynamic>?);
      final code = errObj?['code'];
      if (code == 'SAFECHAT_PROTECTION_BLOCKED') {
        final msg = errObj?['message'] as String? ??
            'This user has SafeChat Protection enabled. Inappropriate or harmful messages cannot be sent to this user.';
        _showSafeChatProtectionDialog(msg);
        return;
      }
      messenger.showSnackBar(
        SnackBar(content: Text('Failed to send message: ${e.message}')),
      );
    } catch (e) {
      messenger.showSnackBar(
        SnackBar(content: Text('Failed to send message: $e')),
      );
    }
  }

  Future<void> _pickAndUploadMedia(ImageSource source, String type) async {
    final picker = ImagePicker();
    XFile? file;
    if (type == 'video') {
      file = await picker.pickVideo(source: source);
    } else {
      file = await picker.pickImage(
        source: source,
        imageQuality: 75, // Compress image by ~25-30% on client before send
      );
    }

    if (file == null) return;

    setState(() => _isUploading = true);
    final dio = ref.read(dioProvider);

    try {
      final bytes = await file.readAsBytes();
      final length = bytes.length;
      final contentType = type == 'video' ? 'video/mp4' : 'image/jpeg';

      final signRes = await dio.post('/api/v1/uploads/sign', data: {
        'content_type': contentType,
        'purpose': 'message',
        'size_bytes': length,
      });

      final uploadData = signRes.data['data'] as Map<String, dynamic>;
      final uploadUrl = uploadData['upload_url'] as String;
      final objectPath = uploadData['object_path'] as String;

      await Dio().put(
        uploadUrl,
        data: bytes,
        options: Options(headers: {'Content-Type': contentType}),
      );

      final mediaUrl = 'https://storage.googleapis.com/$objectPath';
      await _sendMessage(
        mediaUrl: mediaUrl,
        mediaType: type,
        metadata: {'file_name': file.name, 'size_bytes': length},
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Media upload failed: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isUploading = false);
    }
  }

  void _showAttachmentSheet() {
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey.shade400,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                _AttachmentTile(
                  icon: Icons.photo_library_outlined,
                  color: Colors.purple,
                  label: 'Gallery Photo',
                  onTap: () {
                    Navigator.pop(ctx);
                    _pickAndUploadMedia(ImageSource.gallery, 'image');
                  },
                ),
                _AttachmentTile(
                  icon: Icons.camera_alt_outlined,
                  color: Colors.blue,
                  label: 'Camera Photo',
                  onTap: () {
                    Navigator.pop(ctx);
                    _pickAndUploadMedia(ImageSource.camera, 'image');
                  },
                ),
                _AttachmentTile(
                  icon: Icons.videocam_outlined,
                  color: Colors.redAccent,
                  label: 'Video',
                  onTap: () {
                    Navigator.pop(ctx);
                    _pickAndUploadMedia(ImageSource.gallery, 'video');
                  },
                ),
              ],
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmToggleSafeChatMode(String currentMode) async {
    final targetMode = currentMode == 'trusted' ? 'pending' : 'trusted';
    final turningEncryptionOn = targetMode == 'trusted';

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(
          turningEncryptionOn
              ? 'Turn SafeChat Mode OFF?'
              : 'Turn SafeChat Mode ON?',
        ),
        content: Text(
          turningEncryptionOn
              ? 'Messages will now be end-to-end encrypted — SafeChat can no '
                    'longer see or moderate them.'
              : 'SafeChat will now analyze messages in this chat for your safety.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final messenger = ScaffoldMessenger.of(context);
    final dio = ref.read(dioProvider);
    try {
      await dio.patch(
        '/api/v1/chats/${widget.chatId}/encryption-mode',
        data: {'mode': targetMode},
      );
    } on DioException catch (e) {
      if (e.response?.statusCode == 403) {
        messenger.showSnackBar(
          const SnackBar(
            content: Text(
              'SafeChat Mode can only be changed between mutual followers.',
            ),
          ),
        );
      } else {
        messenger.showSnackBar(
          SnackBar(
            content: Text('Failed to update SafeChat Mode: ${e.message}'),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final uid = FirebaseAuth.instance.currentUser?.uid;

    if (uid == null) {
      return const Scaffold(body: Center(child: Text('Not logged in')));
    }

    final derivedKey = ref
        .watch(chatSharedKeyProvider((myUid: uid, otherUid: widget.otherUid)))
        .value;
    if (derivedKey != null) {
      _sharedKey = derivedKey;
    }

    final friendsAsync = ref.watch(friendsProvider(uid));
    final isMutualFollow = friendsAsync.maybeWhen(
      data: (friends) => friends.contains(widget.otherUid),
      orElse: () => false,
    );

    // Watch Presence State
    final presenceAsync = ref.watch(chatPresenceProvider(widget.chatId));
    final presenceMap = presenceAsync.value ?? {};
    final otherPresence = presenceMap[widget.otherUid];
    final isOtherTyping = otherPresence?.isTyping == true;
    final isOtherViewing = otherPresence?.isViewing == true;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Row(
          children: [
            CircleAvatar(
              radius: 18,
              backgroundColor: const Color(0xFF8E2DE2).withValues(alpha: 0.2),
              child: Text(
                widget.otherUserName.isNotEmpty
                    ? widget.otherUserName[0].toUpperCase()
                    : '?',
                style: const TextStyle(
                  color: Color(0xFF8E2DE2),
                  fontWeight: FontWeight.bold,
                  fontSize: 16,
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    widget.otherUserName,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (isOtherTyping)
                    const Text(
                      '💬 typing...',
                      style: TextStyle(
                        fontSize: 12,
                        color: Color(0xFF8E2DE2),
                        fontWeight: FontWeight.w600,
                      ),
                    )
                  else if (isOtherViewing)
                    Row(
                      children: const [
                        Icon(Icons.circle, size: 8, color: Colors.greenAccent),
                        SizedBox(width: 4),
                        Text(
                          'Active in chat',
                          style: TextStyle(fontSize: 11, color: Colors.greenAccent),
                        ),
                      ],
                    ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          if (isMutualFollow)
            ref
                .watch(chatEncryptionModeProvider(widget.chatId))
                .when(
                  data: (mode) => Container(
                    margin: const EdgeInsets.only(right: 12),
                    decoration: BoxDecoration(
                      color: (mode == 'trusted'
                              ? Colors.green
                              : const Color(0xFF8E2DE2))
                          .withValues(alpha: 0.15),
                      shape: BoxShape.circle,
                    ),
                    child: IconButton(
                      icon: Icon(
                        mode == 'trusted' ? Icons.lock : Icons.shield_outlined,
                        color: mode == 'trusted'
                            ? Colors.greenAccent
                            : const Color(0xFF8E2DE2),
                        size: 20,
                      ),
                      tooltip: mode == 'trusted'
                          ? 'SafeChat Mode OFF — E2E Encrypted'
                          : 'SafeChat Protection ON',
                      onPressed: () => _confirmToggleSafeChatMode(mode),
                    ),
                  ),
                  loading: () => const SizedBox.shrink(),
                  error: (_, _) => const SizedBox.shrink(),
                ),
        ],
      ),
      body: Column(
        children: [
          if (_isUploading)
            const LinearProgressIndicator(color: Color(0xFF8E2DE2)),
          Expanded(
            child: StreamBuilder<QuerySnapshot>(
              stream: FirebaseFirestore.instance
                  .collection('chats')
                  .doc(widget.chatId)
                  .collection('messages')
                  .orderBy('created_at', descending: true)
                  .snapshots(),
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return Center(child: Text('Error: ${snapshot.error}'));
                }
                if (snapshot.connectionState == ConnectionState.waiting &&
                    !snapshot.hasData) {
                  return const SizedBox.shrink(); // Zero spinner flicker
                }

                final messages = snapshot.data?.docs ?? [];

                return ListView.builder(
                  reverse: true,
                  itemCount: messages.length,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  itemBuilder: (context, index) {
                    final data = messages[index].data() as Map<String, dynamic>;
                    final isMe = data['sender_uid'] == uid;
                    final text = data['text'] as String? ?? '';
                    final imageUrl = data['image_url'] as String?;
                    final mediaType = data['media_type'] as String? ?? 'text';
                    final status = data['status'] as String? ?? 'approved';
                    final isEncrypted = data['encrypted'] as bool? ?? false;

                    if (!isMe && status != 'approved') {
                      return const SizedBox.shrink();
                    }

                    return _buildMessageBubble(
                      key: ValueKey(messages[index].id),
                      rawText: text,
                      imageUrl: imageUrl,
                      mediaType: mediaType,
                      isEncrypted: isEncrypted,
                      isMe: isMe,
                      status: status,
                      rejectionReason: data['rejection_reason'] as String?,
                    );
                  },
                );
              },
            ),
          ),

          // Snapchat-Style Active Viewing / Typing Bubble at Bottom
          if (isOtherTyping || isOtherViewing)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
              child: Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.face_outlined, size: 16, color: Color(0xFF8E2DE2)),
                        const SizedBox(width: 6),
                        Text(
                          isOtherTyping
                              ? '${widget.otherUserName} is typing...'
                              : '${widget.otherUserName} is in chat 👁️',
                          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),

          // Modern Seamless Input Bar
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: Theme.of(context).scaffoldBackgroundColor,
              border: Border(
                top: BorderSide(
                  color: Theme.of(context).dividerColor.withValues(alpha: 0.15),
                  width: 1,
                ),
              ),
            ),
            child: SafeArea(
              top: false,
              child: Row(
                children: [
                  // Attachment Button
                  Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(24),
                      onTap: _showAttachmentSheet,
                      child: Container(
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: const Color(0xFF8E2DE2).withValues(alpha: 0.15),
                        ),
                        child: const Icon(
                          Icons.add_rounded,
                          color: Color(0xFF8E2DE2),
                          size: 22,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),

                  // Pill Text Box
                  Expanded(
                    child: Container(
                      decoration: BoxDecoration(
                        color: Theme.of(context)
                            .colorScheme
                            .surfaceContainerHighest
                            .withValues(alpha: 0.4),
                        borderRadius: BorderRadius.circular(24),
                        border: Border.all(
                          color: Colors.white.withValues(alpha: 0.1),
                          width: 1,
                        ),
                      ),
                      child: TextField(
                        controller: _messageController,
                        style: const TextStyle(fontSize: 15),
                        maxLines: 4,
                        minLines: 1,
                        onChanged: (val) {
                          ref
                              .read(presenceControllerProvider)
                              .onTextChanged(widget.chatId, val);
                        },
                        decoration: const InputDecoration(
                          hintText: 'Type a message...',
                          hintStyle: TextStyle(color: Colors.grey, fontSize: 14),
                          isDense: true,
                          filled: false,
                          fillColor: Colors.transparent,
                          border: InputBorder.none,
                          enabledBorder: InputBorder.none,
                          focusedBorder: InputBorder.none,
                          contentPadding: EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 10,
                          ),
                        ),
                        onSubmitted: (_) => _sendMessage(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),

                  // Send Button
                  Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(24),
                      onTap: () => _sendMessage(),
                      child: Container(
                        padding: const EdgeInsets.all(11),
                        decoration: BoxDecoration(
                          gradient: const LinearGradient(
                            colors: [Color(0xFF8E2DE2), Color(0xFF4A00E0)],
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                          ),
                          shape: BoxShape.circle,
                          boxShadow: [
                            BoxShadow(
                              color: const Color(0xFF8E2DE2).withValues(alpha: 0.4),
                              blurRadius: 8,
                              offset: const Offset(0, 2),
                            ),
                          ],
                        ),
                        child: const Icon(
                          Icons.send_rounded,
                          color: Colors.white,
                          size: 18,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMessageBubble({
    required Key key,
    required String rawText,
    required String? imageUrl,
    required String mediaType,
    required bool isEncrypted,
    required bool isMe,
    required String status,
    required String? rejectionReason,
  }) {
    if (!isEncrypted) {
      return _MessageBubble(
        key: key,
        text: rawText,
        imageUrl: imageUrl,
        mediaType: mediaType,
        isMe: isMe,
        status: status,
        rejectionReason: rejectionReason,
      );
    }

    final sharedKey = _sharedKey;
    if (sharedKey == null) {
      return _MessageBubble(
        key: key,
        text: '🔒 Unable to decrypt',
        imageUrl: imageUrl,
        mediaType: mediaType,
        isMe: isMe,
        status: status,
        rejectionReason: rejectionReason,
      );
    }

    return FutureBuilder<String>(
      key: key,
      future: ref
          .read(encryptionServiceProvider)
          .decryptText(rawText, sharedKey),
      builder: (context, snapshot) {
        final displayText = snapshot.hasData
            ? snapshot.data!
            : (snapshot.hasError ? '🔒 Unable to decrypt' : '🔒 Decrypting…');
        return _MessageBubble(
          text: displayText,
          imageUrl: imageUrl,
          mediaType: mediaType,
          isMe: isMe,
          status: status,
          rejectionReason: rejectionReason,
        );
      },
    );
  }
}

class _AttachmentTile extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String label;
  final VoidCallback onTap;

  const _AttachmentTile({
    required this.icon,
    required this.color,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Column(
        children: [
          CircleAvatar(
            radius: 26,
            backgroundColor: color.withValues(alpha: 0.15),
            child: Icon(icon, color: color, size: 26),
          ),
          const SizedBox(height: 6),
          Text(label, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  final String text;
  final String? imageUrl;
  final String mediaType;
  final bool isMe;
  final String status;
  final String? rejectionReason;

  const _MessageBubble({
    super.key,
    required this.text,
    this.imageUrl,
    this.mediaType = 'text',
    required this.isMe,
    required this.status,
    this.rejectionReason,
  });

  bool _isLink(String val) {
    return val.startsWith('http://') || val.startsWith('https://');
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isPending = status == 'pending_review';
    final isRejected = status == 'rejected';

    final Color bubbleColor;
    if (isRejected) {
      bubbleColor = scheme.errorContainer;
    } else if (isPending) {
      bubbleColor = scheme.surfaceContainerHighest;
    } else if (isMe) {
      bubbleColor = const Color(0xFF8E2DE2);
    } else {
      bubbleColor = scheme.surfaceContainerHighest;
    }

    final textColor = isMe ? Colors.white : scheme.onSurface;

    final isMeNormal = isMe && !isRejected && !isPending;

    return Align(
      alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        decoration: BoxDecoration(
          gradient: isMeNormal
              ? const LinearGradient(
                  colors: [Color(0xFF8E2DE2), Color(0xFF4A00E0)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                )
              : null,
          color: isMeNormal ? null : bubbleColor,
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.15),
              blurRadius: 6,
              offset: const Offset(0, 2),
            ),
          ],
          borderRadius: BorderRadius.circular(20).copyWith(
            bottomRight: isMe ? const Radius.circular(4) : const Radius.circular(20),
            bottomLeft: !isMe ? const Radius.circular(4) : const Radius.circular(20),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Media Image Display
            if (imageUrl != null && imageUrl!.isNotEmpty) ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(14),
                child: FirebaseCachedNetworkImage(
                  imageUrl: imageUrl!,
                  fit: BoxFit.cover,
                ),
              ),
              const SizedBox(height: 6),
            ],

            // Text / Link Preview Display
            if (text.isNotEmpty)
              _isLink(text)
                  ? GestureDetector(
                      onTap: () {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('Opening link: $text')),
                        );
                      },
                      child: Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.link, size: 18, color: Colors.blueAccent),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                text,
                                style: const TextStyle(
                                  color: Colors.lightBlueAccent,
                                  decoration: TextDecoration.underline,
                                ),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      ),
                    )
                  : Text(
                      text,
                      style: TextStyle(color: textColor, fontSize: 14),
                    ),

            if (isPending)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.pending_actions,
                      size: 12,
                      color: isMe ? Colors.white70 : scheme.outline,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Under review',
                      style: TextStyle(
                        fontSize: 10,
                        color: isMe ? Colors.white70 : scheme.outline,
                      ),
                    ),
                  ],
                ),
              ),
            if (isRejected) ...[
              const SizedBox(height: 4),
              Text(
                'Blocked${rejectionReason != null && rejectionReason!.isNotEmpty ? ': $rejectionReason' : ''}',
                style: TextStyle(fontSize: 10, color: scheme.error),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
