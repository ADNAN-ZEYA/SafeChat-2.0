import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/dio_client.dart';
import '../../moderation/data/moderation_models.dart';
import '../../moderation/presentation/moderation_highlight.dart';
import '../../../shared/widgets/empty_state.dart';
import 'admin_providers.dart';
import '../../profile/presentation/content_status_view.dart';
import '../../home/presentation/feed_provider.dart';

/// Admin review portal: lists content awaiting human verification and lets a
/// moderator approve (publish) or reject (hide + reason) each item.
class AdminModerationView extends ConsumerWidget {
  const AdminModerationView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queueAsync = ref.watch(moderationQueueProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Moderation Queue')),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(moderationQueueProvider);
          await ref.read(moderationQueueProvider.future);
        },
        child: queueAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => ListView(
            children: [
              Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  'Failed to load the queue.\n$e',
                  textAlign: TextAlign.center,
                ),
              ),
            ],
          ),
          data: (items) {
            if (items.isEmpty) {
              return ListView(
                children: const [
                  SizedBox(height: 64),
                  EmptyState(
                    icon: Icons.inbox_outlined,
                    title: 'Queue is empty 🎉',
                    message: 'Nothing is awaiting review right now.',
                  ),
                ],
              );
            }
            return ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: items.length,
              itemBuilder: (context, i) => _QueueCard(
                item: items[i],
                onApprove: (id) => _approve(ref, context, id),
                onReject: (id) => _reject(ref, context, id),
              ),
            );
          },
        ),
      ),
    );
  }

  Future<void> _approve(WidgetRef ref, BuildContext context, String id) async {
    final dio = ref.read(dioProvider);
    try {
      await dio.post('/api/v1/admin/moderation/queue/$id/approve');
      ref.invalidate(moderationQueueProvider);
      ref.invalidate(myAppealsProvider);
      ref.invalidate(feedPostsProvider('global'));
      ref.invalidate(feedPostsProvider('following'));
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Approved ✅')));
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Failed: $e')));
      }
    }
  }

  Future<void> _reject(WidgetRef ref, BuildContext context, String id) async {
    final reason = await _askReason(context);
    if (reason == null) return; // cancelled
    final dio = ref.read(dioProvider);
    try {
      await dio.post(
        '/api/v1/admin/moderation/queue/$id/reject',
        data: {'reason': reason},
      );
      ref.invalidate(moderationQueueProvider);
      ref.invalidate(myAppealsProvider);
      ref.invalidate(feedPostsProvider('global'));
      ref.invalidate(feedPostsProvider('following'));
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Rejected')));
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Failed: $e')));
      }
    }
  }

  Future<String?> _askReason(BuildContext context) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reject — reason'),
        content: TextField(
          controller: controller,
          maxLines: 3,
          decoration: const InputDecoration(
            hintText: 'Why is this being rejected? (shown to the author)',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Reject'),
          ),
        ],
      ),
    );
  }
}

class _QueueCard extends StatelessWidget {
  final Map<String, dynamic> item;
  final void Function(String id) onApprove;
  final void Function(String id) onReject;

  const _QueueCard({
    required this.item,
    required this.onApprove,
    required this.onReject,
  });

  @override
  Widget build(BuildContext context) {
    final id = item['id'] as String? ?? '';
    final text = item['text'] as String? ?? '';
    final contentType = item['content_type'] as String? ?? 'content';
    final author = item['author_username'] as String? ?? 'unknown';
    final matches = ((item['matches'] as List?) ?? const [])
        .whereType<Map>()
        .map((m) => ModerationMatch.fromJson(m.cast<String, dynamic>()))
        .toList();

    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Chip(
                  label: Text(contentType),
                  visualDensity: VisualDensity.compact,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '@$author',
                    style: Theme.of(context).textTheme.labelLarge,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            ModerationHighlightedText(text: text, matches: matches),
            const SizedBox(height: 12),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                OutlinedButton.icon(
                  onPressed: () => onReject(id),
                  icon: const Icon(Icons.block),
                  label: const Text('Reject'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: () => onApprove(id),
                  icon: const Icon(Icons.check),
                  label: const Text('Approve'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
