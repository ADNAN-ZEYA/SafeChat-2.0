// Real unit tests for the moderation highlight span builder (CQ-12).
//
// buildHighlightSpans is pure (no Firebase / network), so it exercises the
// defensive span logic directly — the exact behavior that protects the
// flagged-content popup from a malformed span crashing the widget tree.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:safechat/features/moderation/data/moderation_models.dart';
import 'package:safechat/features/moderation/presentation/moderation_highlight.dart';

ModerationMatch m(int start, int end, [String term = 'x']) =>
    ModerationMatch(term: term, category: 'test', start: start, end: end);

String spansText(List<InlineSpan> spans) =>
    spans.whereType<TextSpan>().map((s) => s.text ?? '').join();

bool isHighlighted(InlineSpan span) =>
    span is TextSpan && span.style?.backgroundColor != null;

void main() {
  Future<List<InlineSpan>> build(
    WidgetTester tester,
    String text,
    List<ModerationMatch> matches,
  ) async {
    late List<InlineSpan> result;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) {
            result = buildHighlightSpans(
              text: text,
              matches: matches,
              base: const TextStyle(),
              context: context,
            );
            return const SizedBox.shrink();
          },
        ),
      ),
    );
    return result;
  }

  testWidgets('no matches returns the whole text as one span', (tester) async {
    final spans = await build(tester, 'hello world', const []);
    expect(spansText(spans), 'hello world');
    expect(spans.any(isHighlighted), isFalse);
  });

  testWidgets('a single match is highlighted, surrounding text is not', (
    tester,
  ) async {
    // "you idiot" — highlight "idiot" (index 4..9)
    final spans = await build(tester, 'you idiot', [m(4, 9, 'idiot')]);
    expect(spansText(spans), 'you idiot');
    final highlighted = spans.where(isHighlighted).cast<TextSpan>().toList();
    expect(highlighted, hasLength(1));
    expect(highlighted.first.text, 'idiot');
  });

  testWidgets('out-of-range spans are ignored, never crash', (tester) async {
    final spans = await build(tester, 'short', [m(2, 999), m(-5, 3)]);
    // Full text preserved; nothing highlighted (both spans invalid).
    expect(spansText(spans), 'short');
    expect(spans.any(isHighlighted), isFalse);
  });

  testWidgets('overlapping matches do not double-cover text', (tester) async {
    // Two overlapping spans over "abcdef": 0..4 and 2..6.
    final spans = await build(tester, 'abcdef', [m(0, 4), m(2, 6)]);
    // Reconstructed text must equal the original exactly (no duplication/loss).
    expect(spansText(spans), 'abcdef');
  });

  testWidgets('unsorted matches are handled', (tester) async {
    // Provide later match first; builder sorts by start.
    final spans = await build(tester, 'a b c', [m(4, 5, 'c'), m(0, 1, 'a')]);
    expect(spansText(spans), 'a b c');
    final highlighted = spans.where(isHighlighted).cast<TextSpan>().toList();
    expect(highlighted.map((s) => s.text), containsAll(<String>['a', 'c']));
  });
}
