import 'dart:convert';
import 'dart:io';

/// One selectable rendition parsed out of an HLS master playlist.
class HlsVariant {
  const HlsVariant({
    required this.label,
    required this.width,
    required this.height,
    required this.bandwidth,
    required this.url,
  });

  /// "480P" — the label the settings sheet shows.
  final String label;
  final int width;
  final int height;
  final int bandwidth;

  /// Absolute variant playlist URL (already carries the signature token).
  final String url;
}

/// Parse `#EXT-X-STREAM-INF` entries out of a master playlist.
///
/// Bunny serves the signature token as a *path* prefix, so a relative
/// `480p/video.m3u8` resolved against the master URL keeps the token — which
/// is what lets us load one rendition directly and pin it.
List<HlsVariant> parseMasterPlaylist(String text, Uri masterUri) {
  final lines = const LineSplitter().convert(text);
  final variants = <HlsVariant>[];
  for (var i = 0; i < lines.length; i++) {
    final line = lines[i].trim();
    if (!line.startsWith('#EXT-X-STREAM-INF:')) continue;
    final attrs = line.substring('#EXT-X-STREAM-INF:'.length);
    final res = RegExp(r'RESOLUTION=(\d+)x(\d+)').firstMatch(attrs);
    final bandwidth = RegExp(r'(?<!AVERAGE-)BANDWIDTH=(\d+)').firstMatch(attrs);

    // The URI is the next non-blank, non-comment line.
    String? uri;
    for (var j = i + 1; j < lines.length; j++) {
      final candidate = lines[j].trim();
      if (candidate.isEmpty || candidate.startsWith('#')) continue;
      uri = candidate;
      break;
    }
    if (uri == null) continue;

    final width = res == null ? 0 : int.parse(res.group(1)!);
    final height = res == null ? 0 : int.parse(res.group(2)!);
    // Bunny names the ladder rung in the directory (`240p/video.m3u8`), and
    // that name is what the server's `renditions` list and H5 both show.
    // RESOLUTION is portrait here (198x352 for "240p"), so it can't label it.
    final dir = uri.split('/').reversed.skip(1).firstWhere((part) => part.isNotEmpty, orElse: () => '');
    final rung = RegExp(r'^(\d{3,4})p$', caseSensitive: false).firstMatch(dir);
    final label = rung != null ? '${rung.group(1)}P' : (height > 0 ? '${height}P' : 'AUTO');
    variants.add(HlsVariant(
      label: label,
      width: width,
      height: height,
      bandwidth: bandwidth == null ? 0 : int.parse(bandwidth.group(1)!),
      url: masterUri.resolve(uri).toString(),
    ));
  }

  // One entry per rung, cheapest first — the order H5 shows.
  final seen = <String>{};
  final unique = variants.where((variant) => seen.add(variant.label)).toList()
    ..sort((a, b) => _rungNumber(a.label).compareTo(_rungNumber(b.label)));
  return unique;
}

/// "480P" → 480 so the sheet lists 240 / 360 / 480 (not 4800 first).
int _rungNumber(String label) => int.tryParse(label.replaceAll(RegExp(r'[^0-9]'), '')) ?? 0;

/// Fetch and parse the master playlist. Never throws: on any failure the
/// caller simply keeps Auto (the player then adapts on its own).
Future<List<HlsVariant>> loadHlsVariants(String masterUrl) async {
  final client = HttpClient();
  try {
    final request = await client.getUrl(Uri.parse(masterUrl));
    request.headers.set(HttpHeaders.userAgentHeader, 'MiniTV/1.0 (iOS)');
    final response = await request.close();
    if (response.statusCode != 200) return const [];
    final text = await response.transform(utf8.decoder).join();
    return parseMasterPlaylist(text, Uri.parse(masterUrl));
  } catch (_) {
    return const [];
  } finally {
    client.close();
  }
}
