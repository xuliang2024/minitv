import 'package:flutter/material.dart';

import 'img.dart';

const bg = Color(0xFF0C0A0B);
const red = Color(0xFFE53B3E);
const gold = Color(0xFFF5B942);
const sheet = Color(0xFF171013);

/// 12K / 3.4万 style counts, matching H5 `formatCount`.
String formatCount(int value, {bool zh = false}) {
  String trim(double v) {
    final text = v.toStringAsFixed(1);
    return text.endsWith('.0') ? text.substring(0, text.length - 2) : text;
  }

  if (zh) return value >= 10000 ? '${trim(value / 10000)}万' : '$value';
  if (value >= 1000000) return '${trim(value / 1000000)}M';
  if (value >= 1000) return '${trim(value / 1000)}K';
  return '$value';
}

class Poster extends StatelessWidget {
  const Poster({super.key, this.url, this.imgWidth = Img.card});
  final String? url;

  /// Bunny Optimizer variant width in physical px, see [Img].
  final int imgWidth;

  @override
  Widget build(BuildContext context) {
    final src = imgUrl(url, imgWidth);
    return AspectRatio(
      aspectRatio: 3 / 4,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: src.isEmpty
            ? const ColoredBox(color: sheet)
            : Image.network(
                src,
                fit: BoxFit.cover,
                cacheWidth: imgWidth,
                headers: imageAcceptHeaders,
                errorBuilder: (_, _, _) => const ColoredBox(color: sheet),
              ),
      ),
    );
  }
}

/// Poster image with optional fixed pixel size.
///
/// [imgWidth] asks the CDN for a thumbnail of that physical pixel width (Bunny
/// Optimizer, same scheme as `h5/src/img.ts`); it also caps the decode size so
/// we never hold a full-size poster in memory. Leave it null to load the
/// original asset.
class PosterImage extends StatelessWidget {
  const PosterImage({super.key, this.url, this.width, this.height, this.imgWidth});
  final String? url;
  final double? width;
  final double? height;
  final int? imgWidth;

  @override
  Widget build(BuildContext context) {
    final src = imgWidth == null ? (url ?? '') : imgUrl(url, imgWidth!);
    final image = src.isEmpty
        ? const ColoredBox(color: sheet)
        : Image.network(
            src,
            fit: BoxFit.cover,
            cacheWidth: imgWidth,
            headers: imageAcceptHeaders,
            errorBuilder: (_, _, _) => const ColoredBox(color: sheet),
          );
    final clipped = ClipRRect(borderRadius: BorderRadius.circular(8), child: image);
    if (width != null && height != null) {
      return SizedBox(width: width, height: height, child: clipped);
    }
    return clipped;
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

/// Rounded avatar with a letter fallback, used by comments and Me.
class UserAvatar extends StatelessWidget {
  const UserAvatar({super.key, this.url, this.name = '', this.size = 36, this.radius = 10});
  final String? url;
  final String name;
  final double size;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final src = imgUrl(url, Img.avatar);
    final initial = (name.trim().isEmpty ? 'M' : name.trim().substring(0, 1)).toUpperCase();
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: sheet,
        borderRadius: BorderRadius.circular(radius),
      ),
      clipBehavior: Clip.antiAlias,
      child: src.isEmpty
          ? Center(child: Text(initial, style: const TextStyle(color: red, fontSize: 15, fontWeight: FontWeight.w900)))
          : Image.network(
              src,
              fit: BoxFit.cover,
              headers: imageAcceptHeaders,
              errorBuilder: (_, _, _) =>
                  Center(child: Text(initial, style: const TextStyle(color: red, fontSize: 15, fontWeight: FontWeight.w900))),
            ),
    );
  }
}

/// Bottom sheet host: a dim mask plus a dark panel pinned to the bottom edge.
/// Drop into a `Positioned.fill` inside the page's outermost [Stack] so it
/// paints above the video and the tab bar (the H5 `Sheet` equivalent).
class AppSheet extends StatelessWidget {
  const AppSheet({
    super.key,
    required this.title,
    required this.onClose,
    required this.child,
    this.heightFactor = 0.62,
    this.showClose = true,
  });

  final String title;
  final VoidCallback onClose;
  final Widget child;

  /// Panel height as a fraction of the screen, capped by content.
  final double heightFactor;
  final bool showClose;

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return Material(
      color: Colors.transparent,
      child: Stack(
        fit: StackFit.expand,
        children: [
          GestureDetector(onTap: onClose, child: const ColoredBox(color: Color(0x80000000))),
          Align(
            alignment: Alignment.bottomCenter,
            child: Container(
              constraints: BoxConstraints(maxHeight: media.size.height * heightFactor),
              padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
              decoration: const BoxDecoration(
                color: sheet,
                borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
              ),
              child: SafeArea(
                top: false,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const SizedBox(height: 8),
                    Container(
                      width: 36,
                      height: 4,
                      decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)),
                    ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 12, 6, 4),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(title,
                                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w700)),
                          ),
                          if (showClose)
                            IconButton(
                              onPressed: onClose,
                              icon: const Icon(Icons.close, color: Colors.white70, size: 20),
                              visualDensity: VisualDensity.compact,
                            ),
                        ],
                      ),
                    ),
                    Flexible(child: child),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
