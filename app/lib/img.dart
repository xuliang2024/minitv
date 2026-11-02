/// Bunny Optimizer thumbnail helpers — the App twin of `h5/src/img.ts`.
///
/// Covers live in the Bunny Storage zone behind the image pull zone. With
/// Optimizer enabled, appending width/quality makes the edge produce (once)
/// and cache a smaller WebP variant instead of shipping the original poster.
///
/// Only the image pull zone understands these params, so every other host —
/// Google avatars, marketing-site assets — is returned untouched. Signed URLs
/// (subtitles) must never go through here.
const imageCdnHost = 'minitv-dev-img.b-cdn.net';

/// Bunny Optimizer only returns WebP when the client advertises it — browsers
/// do that for H5, Dart's `HttpClient` does not (it would get the JPEG variant,
/// ~58 KB instead of ~34 KB at card size).
const imageAcceptHeaders = {'Accept': 'image/webp,image/*,*/*'};

/// Display widths in physical pixels (logical px × DPR headroom), matching the
/// layout in `screens/home.dart` / `screens/channel.dart` and the scale used by
/// `h5/src/img.ts`.
class Img {
  /// Full-bleed hero carousel. Call sites prefer an exact device width; this is
  /// the fallback for boxes whose width is not known up front.
  static const banner = 1080;

  /// Poster grid (3 columns) and "my list".
  static const card = 480;

  /// Landscape grid (2 columns).
  static const landscape = 720;

  /// Rank list thumb / small poster (48×64 box).
  static const rank = 180;

  /// Feed rail avatar (46×46 box).
  static const avatar = 144;
}

/// Returns [url] with Bunny Optimizer width/quality params, or untouched when
/// the URL is not on the image pull zone (or already carries a size).
String imgUrl(String? url, int width, {int quality = 75}) {
  if (url == null || url.isEmpty) return '';

  final parsed = Uri.tryParse(url);
  if (parsed == null) return url;
  if (parsed.scheme != 'http' && parsed.scheme != 'https') return url;
  if (parsed.host != imageCdnHost) return url;

  final query = parsed.queryParameters;
  if (query.containsKey('width') || query.containsKey('height')) return url;

  return parsed.replace(queryParameters: {
    ...query,
    'width': '$width',
    'quality': '$quality',
  }).toString();
}
