import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;
import '../../../shared/utils/markdown_extensions.dart';
import '../../moderation/presentation/flagged_content_dialog.dart';
import 'create_post_provider.dart';

class CreatePostView extends ConsumerStatefulWidget {
  const CreatePostView({super.key});

  @override
  ConsumerState<CreatePostView> createState() => _CreatePostViewState();
}

class _CreatePostViewState extends ConsumerState<CreatePostView> {
  final TextEditingController _captionController = TextEditingController();
  final ImagePicker _picker = ImagePicker();
  final PageController _pageController = PageController();
  int _currentStep = 0;
  String? _validationError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _captionController.text = ref.read(createPostProvider).caption;
    });
  }

  @override
  void dispose() {
    _captionController.dispose();
    _pageController.dispose();
    super.dispose();
  }

  Future<void> _pickMedia() async {
    final List<XFile> images = await _picker.pickMultiImage();
    if (images.isNotEmpty) {
      final files = images.map((x) => File(x.path)).toList();
      ref.read(createPostProvider.notifier).addMedia(files);
      if (_validationError != null) {
        setState(() => _validationError = null);
      }
    }
  }

  Future<void> _submit() async {
    if (ref.read(createPostProvider).selectedMedia.isEmpty) {
      setState(() {
        _validationError =
            '📷 Posts cannot be text-only. Please select an image or video.';
      });
      return;
    }
    setState(() => _validationError = null);
    ref.read(createPostProvider.notifier).setCaption(_captionController.text);
    final outcome = await ref.read(createPostProvider.notifier).submitPost();
    if (!mounted) return;

    switch (outcome) {
      case SubmitOutcome.approved:
        _finishWithMessage('🎉 Post live! Check your feed.', Colors.green);
      case SubmitOutcome.pendingReview:
        _finishWithMessage(
          '📋 Post under review. It\'ll go live once approved!',
          Colors.orange,
          durationSeconds: 5,
        );
      case SubmitOutcome.flagged:
        await _handleFlagged();
      case null:
        _showError();
    }
  }

  void _finishWithMessage(
    String message,
    Color color, {
    int durationSeconds = 3,
  }) {
    ref.read(createPostProvider.notifier).reset();
    Navigator.of(context).pop();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: color,
        duration: Duration(seconds: durationSeconds),
      ),
    );
  }

  /// Flagged: show the highlighted popup. If the user opts into human review,
  /// re-submit; otherwise keep the composer open so they can edit.
  Future<void> _handleFlagged() async {
    final matches = ref.read(createPostProvider).flaggedMatches;
    final result = await showFlaggedContentDialog(
      context,
      text: _captionController.text,
      matches: matches,
    );
    if (!mounted || result == null || !result.submitForReview) return;

    final outcome = await ref
        .read(createPostProvider.notifier)
        .confirmHumanVerification();
    if (!mounted) return;
    switch (outcome) {
      case SubmitOutcome.pendingReview:
      case SubmitOutcome.approved:
        _finishWithMessage(
          '📋 Sent for human review. Track it in Profile → Appeals.',
          Colors.orange,
          durationSeconds: 5,
        );
      case SubmitOutcome.flagged:
      case null:
        _showError();
    }
  }

  void _showError() {
    final error = ref.read(createPostProvider).submissionState.error;
    final message = error != null
        ? error.toString().replaceFirst('Exception: ', '').split('\n').first
        : 'Failed to create post. Please try again.';
    setState(() {
      _validationError = message;
    });
  }

  void _nextStep() {
    if (_currentStep == 0 &&
        ref.read(createPostProvider).selectedMedia.isEmpty) {
      setState(() {
        _validationError =
            '📷 Please select at least one image or video to proceed.';
      });
      return;
    }
    setState(() => _validationError = null);
    if (_currentStep < 2) {
      setState(() => _currentStep++);
      _pageController.nextPage(
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeInOut,
      );
    }
  }

  void _prevStep() {
    if (_currentStep > 0) {
      setState(() => _currentStep--);
      _pageController.previousPage(
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeInOut,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(createPostProvider);
    final isLoading = state.submissionState is AsyncLoading;

    return Container(
      decoration: BoxDecoration(
        color: Theme.of(context).scaffoldBackgroundColor,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
      ),
      child: SafeArea(
        child: Padding(
          padding: EdgeInsets.only(
            bottom: MediaQuery.of(context).viewInsets.bottom,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox(height: 12),
              Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: Colors.grey.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(height: 16),

              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      'Create Post',
                      style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    if (!state.isSimpleMode || _currentStep == 2)
                      FilledButton(
                        onPressed: isLoading ? null : _submit,
                        child: const Text('Post'),
                      ),
                  ],
                ),
              ),
              const Divider(),

              if (_validationError != null)
                Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16.0,
                    vertical: 4.0,
                  ),
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.warning_amber_rounded,
                          color: Theme.of(
                            context,
                          ).colorScheme.onErrorContainer,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _validationError!,
                            style: TextStyle(
                              color: Theme.of(
                                context,
                              ).colorScheme.onErrorContainer,
                              fontWeight: FontWeight.bold,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        GestureDetector(
                          onTap: () => setState(() => _validationError = null),
                          child: Icon(
                            Icons.close,
                            size: 18,
                            color: Theme.of(
                              context,
                            ).colorScheme.onErrorContainer,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),

              if (isLoading) ...[
                const Padding(
                  padding: EdgeInsets.all(16.0),
                  child: Column(
                    children: [
                      LinearProgressIndicator(),
                      SizedBox(height: 8),
                      Text(
                        'Verifying post with AI Moderation...',
                        style: TextStyle(color: Colors.grey, fontSize: 12),
                      ),
                    ],
                  ),
                ),
              ],

              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16.0,
                  vertical: 8.0,
                ),
                child: SegmentedButton<bool>(
                  segments: const [
                    ButtonSegment(value: false, label: Text('Advanced Canvas')),
                    ButtonSegment(value: true, label: Text('Simple Wizard')),
                  ],
                  selected: {state.isSimpleMode},
                  onSelectionChanged: (Set<bool> selection) {
                    ref
                        .read(createPostProvider.notifier)
                        .setMode(selection.first);
                  },
                ),
              ),

              Expanded(
                child: state.isSimpleMode
                    ? _buildSimpleWizard(state, isLoading)
                    : SingleChildScrollView(
                        child: _buildAdvancedCanvas(state, isLoading),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildAdvancedCanvas(CreatePostState state, bool isLoading) {
    return Column(
      children: [
        _buildMediaSection(state),
        const SizedBox(height: 16),
        _buildEditorSection(isLoading),
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _buildSimpleWizard(CreatePostState state, bool isLoading) {
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(3, (index) {
            return AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              margin: const EdgeInsets.symmetric(horizontal: 4),
              height: 8,
              width: _currentStep == index ? 24 : 8,
              decoration: BoxDecoration(
                color: _currentStep == index
                    ? Theme.of(context).colorScheme.primary
                    : Colors.grey.withValues(alpha: 0.3),
                borderRadius: BorderRadius.circular(4),
              ),
            );
          }),
        ),
        const SizedBox(height: 16),
        Expanded(
          child: PageView(
            controller: _pageController,
            physics: const NeverScrollableScrollPhysics(),
            children: [
              // Step 0: Media
              Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  children: [
                    Text(
                      'Step 1: Add Media',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 16),
                    Expanded(child: _buildMediaSection(state)),
                    const SizedBox(height: 16),
                    FilledButton.icon(
                      onPressed: _nextStep,
                      icon: const Icon(Icons.arrow_forward),
                      label: const Text('Next: Write Caption'),
                    ),
                  ],
                ),
              ),
              // Step 1: Text
              Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  children: [
                    Text(
                      'Step 2: Write a Caption',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 16),
                    Expanded(child: _buildEditorSection(isLoading)),
                    const SizedBox(height: 16),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        TextButton.icon(
                          onPressed: _prevStep,
                          icon: const Icon(Icons.arrow_back),
                          label: const Text('Back'),
                        ),
                        FilledButton.icon(
                          onPressed: _nextStep,
                          icon: const Icon(Icons.arrow_forward),
                          label: const Text('Next: Review'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              // Step 2: Review
              Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  children: [
                    Text(
                      'Step 3: Review',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 16),
                    Expanded(
                      child: SingleChildScrollView(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (state.selectedMedia.isNotEmpty)
                              SizedBox(
                                height: 200,
                                child: ListView.builder(
                                  scrollDirection: Axis.horizontal,
                                  itemCount: state.selectedMedia.length,
                                  itemBuilder: (context, index) => Container(
                                    margin: const EdgeInsets.only(right: 8),
                                    width: 150,
                                    decoration: BoxDecoration(
                                      borderRadius: BorderRadius.circular(12),
                                      image: DecorationImage(
                                        image: FileImage(
                                          state.selectedMedia[index],
                                        ),
                                        fit: BoxFit.cover,
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            const SizedBox(height: 16),
                            const Text(
                              'Caption:',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              _captionController.text.isEmpty
                                  ? '(No caption)'
                                  : _captionController.text,
                            ),
                          ],
                        ),
                      ),
                    ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        TextButton.icon(
                          onPressed: _prevStep,
                          icon: const Icon(Icons.arrow_back),
                          label: const Text('Back'),
                        ),
                        FilledButton.icon(
                          onPressed: isLoading ? null : _submit,
                          icon: const Icon(Icons.check),
                          label: const Text('Post Now'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildMediaSection(CreatePostState state) {
    if (state.selectedMedia.isNotEmpty) {
      return SizedBox(
        height: 120,
        child: ListView.builder(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          itemCount: state.selectedMedia.length + 1,
          itemBuilder: (context, index) {
            if (index == state.selectedMedia.length) {
              if (state.selectedMedia.length >= 5) {
                return const SizedBox.shrink();
              }
              return Padding(
                padding: const EdgeInsets.only(left: 8.0),
                child: InkWell(
                  onTap: _pickMedia,
                  child: Container(
                    width: 100,
                    height: 100,
                    decoration: BoxDecoration(
                      border: Border.all(color: Colors.grey),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Icon(Icons.add_photo_alternate, size: 40),
                  ),
                ),
              );
            }
            final file = state.selectedMedia[index];
            return Stack(
              children: [
                Container(
                  margin: const EdgeInsets.only(right: 8),
                  width: 100,
                  height: 100,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(12),
                    image: DecorationImage(
                      image: FileImage(file),
                      fit: BoxFit.cover,
                    ),
                  ),
                ),
                Positioned(
                  top: 4,
                  right: 12,
                  child: GestureDetector(
                    onTap: () => ref
                        .read(createPostProvider.notifier)
                        .removeMedia(index),
                    child: const CircleAvatar(
                      radius: 12,
                      backgroundColor: Colors.black54,
                      child: Icon(Icons.close, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ],
            );
          },
        ),
      );
    } else {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16.0),
        child: InkWell(
          onTap: _pickMedia,
          borderRadius: BorderRadius.circular(12),
          child: Container(
            height: 100,
            width: double.infinity,
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.add_photo_alternate, size: 32),
                SizedBox(height: 8),
                Text('Add Photos or Videos'),
              ],
            ),
          ),
        ),
      );
    }
  }

  void _addFormat(String prefix, String suffix) {
    final text = _captionController.text;
    final selection = _captionController.selection;

    if (!selection.isValid || selection.start == selection.end) {
      final insertPos = selection.isValid ? selection.start : text.length;
      final newText = text.replaceRange(insertPos, insertPos, '$prefix$suffix');
      _captionController.value = _captionController.value.copyWith(
        text: newText,
        selection: TextSelection.collapsed(offset: insertPos + prefix.length),
      );
      return;
    }

    final selectedText = selection.textInside(text);
    final newText = text.replaceRange(
      selection.start,
      selection.end,
      '$prefix$selectedText$suffix',
    );
    _captionController.value = _captionController.value.copyWith(
      text: newText,
      selection: TextSelection.collapsed(
        offset: selection.end + prefix.length + suffix.length,
      ),
    );
  }

  Widget _buildEditorSection(bool isLoading) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(12),
            ),
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
            margin: const EdgeInsets.only(bottom: 8),
            child: Wrap(
              spacing: 4,
              children: [
                IconButton(
                  icon: const Icon(Icons.title),
                  tooltip: 'Heading',
                  onPressed: isLoading ? null : () => _addFormat('### ', ''),
                ),
                IconButton(
                  icon: const Icon(Icons.format_bold),
                  tooltip: 'Bold',
                  onPressed: isLoading ? null : () => _addFormat('**', '**'),
                ),
                IconButton(
                  icon: const Icon(Icons.format_italic),
                  tooltip: 'Italic',
                  onPressed: isLoading ? null : () => _addFormat('_', '_'),
                ),
                IconButton(
                  icon: const Icon(Icons.highlight),
                  tooltip: 'Highlight',
                  onPressed: isLoading ? null : () => _addFormat('==', '=='),
                ),
                IconButton(
                  icon: const Icon(Icons.format_strikethrough),
                  tooltip: 'Strikethrough',
                  onPressed: isLoading ? null : () => _addFormat('~~', '~~'),
                ),
              ],
            ),
          ),
          TextField(
            controller: _captionController,
            maxLines: null,
            minLines: 5,
            enabled: !isLoading,
            onChanged: (val) =>
                ref.read(createPostProvider.notifier).setCaption(val),
            decoration: const InputDecoration(
              hintText:
                  'What\'s on your mind? Select text or use the toolbar to format!',
              border: OutlineInputBorder(),
              filled: false,
            ),
            contextMenuBuilder:
                (BuildContext context, EditableTextState editableTextState) {
                  final List<ContextMenuButtonItem> buttonItems =
                      editableTextState.contextMenuButtonItems;

                  buttonItems.insert(
                    0,
                    ContextMenuButtonItem(
                      label: 'Heading',
                      onPressed: () {
                        _addFormat('### ', '');
                        ContextMenuController.removeAny();
                      },
                    ),
                  );
                  buttonItems.insert(
                    1,
                    ContextMenuButtonItem(
                      label: 'Bold',
                      onPressed: () {
                        _addFormat('**', '**');
                        ContextMenuController.removeAny();
                      },
                    ),
                  );
                  buttonItems.insert(
                    2,
                    ContextMenuButtonItem(
                      label: 'Italic',
                      onPressed: () {
                        _addFormat('_', '_');
                        ContextMenuController.removeAny();
                      },
                    ),
                  );
                  buttonItems.insert(
                    3,
                    ContextMenuButtonItem(
                      label: 'Highlight',
                      onPressed: () {
                        _addFormat('==', '==');
                        ContextMenuController.removeAny();
                      },
                    ),
                  );
                  buttonItems.insert(
                    4,
                    ContextMenuButtonItem(
                      label: 'Strike',
                      onPressed: () {
                        _addFormat('~~', '~~');
                        ContextMenuController.removeAny();
                      },
                    ),
                  );

                  return AdaptiveTextSelectionToolbar.buttonItems(
                    anchors: editableTextState.contextMenuAnchors,
                    buttonItems: buttonItems,
                  );
                },
          ),
          const SizedBox(height: 16),
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: _captionController,
            builder: (context, value, child) {
              if (value.text.isEmpty) return const SizedBox.shrink();
              return Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Theme.of(
                    context,
                  ).colorScheme.surfaceContainerHighest.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: Theme.of(context).colorScheme.outlineVariant,
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Live Preview',
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: Theme.of(context).colorScheme.primary,
                      ),
                    ),
                    const SizedBox(height: 8),
                    MarkdownBody(
                      data: value.text,
                      extensionSet: md.ExtensionSet.gitHubFlavored,
                      inlineSyntaxes: [HighlightSyntax()],
                      builders: {'highlight': HighlightBuilder(context)},
                      styleSheet: MarkdownStyleSheet(
                        p: Theme.of(
                          context,
                        ).textTheme.bodyLarge?.copyWith(height: 1.5),
                        h1: Theme.of(context).textTheme.headlineMedium
                            ?.copyWith(fontWeight: FontWeight.bold),
                        h2: Theme.of(context).textTheme.headlineSmall?.copyWith(
                          fontWeight: FontWeight.bold,
                        ),
                        h3: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}
