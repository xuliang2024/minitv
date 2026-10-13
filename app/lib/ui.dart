import 'package:flutter/material.dart';

const bg = Color(0xFF0C0A0B);
const red = Color(0xFFE53B3E);
const gold = Color(0xFFF5B942);
const sheet = Color(0xFF171013);

class Poster extends StatelessWidget {
  const Poster({super.key, this.url});
  final String? url;
  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 3 / 4,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: url == null || url!.isEmpty
            ? const ColoredBox(color: sheet)
            : Image.network(url!, fit: BoxFit.cover, errorBuilder: (_, _, _) => const ColoredBox(color: sheet)),
      ),
    );
  }
}

class RetryBody extends StatelessWidget {
  const RetryBody({super.key, required this.label, required this.onRetry});
  final String label;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) {
    return Center(
      child: TextButton(onPressed: onRetry, child: Text(label, style: const TextStyle(color: gold))),
    );
  }
}
