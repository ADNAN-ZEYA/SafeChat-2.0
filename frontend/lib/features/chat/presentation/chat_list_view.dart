import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'chat_detail_view.dart';
import '../../profile/presentation/follow_providers.dart';

/// Cached peer profile lookups for the chat list (CQ-04).
///
/// The previous per-row FutureBuilder issued one Firestore `get()` per chat
/// row on EVERY stream tick (N+1 reads, re-fired constantly). A family
/// FutureProvider caches each uid's profile for the session: one read per
/// distinct peer, shared across rebuilds. Names/avatars changing mid-session
/// is acceptable staleness for a chat list.
final chatPeerProfileProvider =
    FutureProvider.family<Map<String, dynamic>?, String>((ref, uid) async {
      final doc = await FirebaseFirestore.instance
          .collection('users')
          .doc(uid)
          .get();
      return doc.data();
    });

class ChatListView extends ConsumerStatefulWidget {
  const ChatListView({super.key});

  @override
  ConsumerState<ChatListView> createState() => _ChatListViewState();
}

class _ChatListViewState extends ConsumerState<ChatListView>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final uid = FirebaseAuth.instance.currentUser?.uid;
    if (uid == null) {
      return const Scaffold(body: Center(child: Text('Not logged in')));
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Messages',
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        bottom: TabBar(
          controller: _tabController,
          tabs: const [
            Tab(text: 'Friends'),
            Tab(text: 'Requests'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildChatList(uid, isFriends: true),
          _buildChatList(uid, isFriends: false),
        ],
      ),
    );
  }

  Widget _buildChatList(String uid, {required bool isFriends}) {
    final friendsAsync = ref.watch(friendsProvider(uid));

    return friendsAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, st) => Center(child: Text('Error: $e')),
      data: (friendsList) {
        // Find chats where the current user is a participant
        final query = FirebaseFirestore.instance
            .collection('chats')
            .where('participants', arrayContains: uid)
            .orderBy('updated_at', descending: true);

        return StreamBuilder<QuerySnapshot>(
          stream: query.snapshots(),
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return Center(
                child: Text('Error loading chats: ${snapshot.error}'),
              );
            }
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }

            final allChats = snapshot.data?.docs ?? [];

            // Filter chats based on whether the other participant is a mutual friend or not
            final filteredChats = allChats.where((doc) {
              final data = doc.data() as Map<String, dynamic>;
              final participants = List<String>.from(
                data['participants'] ?? [],
              );
              participants.remove(uid);
              final otherUid = participants.isNotEmpty
                  ? participants.first
                  : null;

              if (otherUid == null) return false;

              final isMutual = friendsList.contains(otherUid);
              return isFriends ? isMutual : !isMutual;
            }).toList();

            if (filteredChats.isEmpty) {
              return Center(
                child: Padding(
                  padding: const EdgeInsets.all(32.0),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        padding: const EdgeInsets.all(20),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: const Color(0xFF8E2DE2).withValues(alpha: 0.12),
                        ),
                        child: Icon(
                          isFriends ? Icons.chat_bubble_outline_rounded : Icons.mark_chat_unread_outlined,
                          size: 48,
                          color: const Color(0xFF8E2DE2),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        isFriends
                            ? 'No friend conversations yet.'
                            : 'No message requests.',
                        style: const TextStyle(
                          color: Colors.grey,
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        isFriends
                            ? 'Search for users or visit their profile to start a chat'
                            : 'Message requests from non-friends will appear here',
                        style: const TextStyle(color: Colors.grey, fontSize: 12),
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
              );
            }

            return ListView.builder(
              padding: const EdgeInsets.only(bottom: 100),
              itemCount: filteredChats.length,
              itemBuilder: (context, index) {
                final doc = filteredChats[index];
                final data = doc.data() as Map<String, dynamic>;

                final participants = List<String>.from(
                  data['participants'] ?? [],
                );
                participants.remove(uid);
                final otherUid = participants.isNotEmpty
                    ? participants.first
                    : '';
                // Field names match what the backend writes to the chat doc
                // (services/messages.py): last_message_text + unread_counts.
                final lastMessage = data['last_message_text'] as String? ?? '';
                final unreadCount =
                    (data['unread_counts'] as Map<String, dynamic>?)?[uid]
                        as int? ??
                    0;

                final peerAsync = ref.watch(chatPeerProfileProvider(otherUid));
                return peerAsync.when(
                  loading: () => const ListTile(title: Text('Loading...')),
                  error: (_, _) => const SizedBox(),
                  data: (userData) {
                    if (userData == null) return const SizedBox();

                    final displayName = userData['display_name'] ?? 'User';
                    // Users store their avatar as photo_url (author_photo_url
                    // only exists as a denormalized field on posts).
                    final photoUrl = userData['photo_url'];

                    return ListTile(
                      leading: CircleAvatar(
                        backgroundImage: photoUrl != null
                            ? NetworkImage(photoUrl)
                            : null,
                        child: photoUrl == null
                            ? const Icon(Icons.person)
                            : null,
                      ),
                      title: Text(
                        displayName,
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      ),
                      subtitle: Text(
                        lastMessage,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      trailing: unreadCount > 0
                          ? CircleAvatar(
                              radius: 12,
                              backgroundColor: Colors.blueAccent,
                              child: Text(
                                unreadCount.toString(),
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: Colors.white,
                                ),
                              ),
                            )
                          : null,
                      onTap: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => ChatDetailView(
                              chatId: doc.id,
                              otherUid: otherUid,
                              otherUserName: displayName,
                            ),
                          ),
                        );
                      },
                    );
                  },
                );
              },
            );
          },
        );
      },
    );
  }
}
