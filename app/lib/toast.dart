import 'package:flutter/material.dart';

/// Tiny toast: a dark pill pinned just above the bottom bar, auto-dismissed.
void toast(BuildContext context, String message) {
  final overlay = Overlay.maybeOf(context);
  if (overlay == null) return;
  final entry = OverlayEntry(builder: (_) => _ToastPill(message: message));
  overlay.insert(entry);
  Future<void>.delayed(const Duration(milliseconds: 2200), () {
    if (entry.mounted) entry.remove();
  });
}

class _ToastPill extends StatelessWidget {
  const _ToastPill({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.of(context).padding.bottom;
    return IgnorePointer(
      child: Align(
        alignment: Alignment.bottomCenter,
        child: Padding(
          padding: EdgeInsets.only(bottom: 96 + bottom),
          child: Container(
            constraints: const BoxConstraints(maxWidth: 320),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: BoxDecoration(
              color: const Color(0xE62B2024),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: Colors.white12),
            ),
            child: Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white, fontSize: 13, decoration: TextDecoration.none),
            ),
          ),
        ),
      ),
    );
  }
}
