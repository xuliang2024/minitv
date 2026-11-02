import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/hls.dart';

/// The real master playlist Bunny serves for a 480-tall source.
const _master = '''
#EXTM3U
#EXT-X-VERSION:3

#EXT-X-STREAM-INF:BANDWIDTH=1430680,AVERAGE-BANDWIDTH=716819,CODECS="avc1.64001e,mp4a.40.2",RESOLUTION=360x640,CLOSED-CAPTIONS=NONE
360p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2007840,AVERAGE-BANDWIDTH=1125641,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=480x854,CLOSED-CAPTIONS=NONE
480p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=682816,AVERAGE-BANDWIDTH=361496,CODECS="avc1.64000d,mp4a.40.2",RESOLUTION=198x352,CLOSED-CAPTIONS=NONE
240p/video.m3u8
''';

void main() {
  // Bunny puts the signature token in the path, so relative variants keep it.
  final masterUri = Uri.parse(
    'https://vz-114a9213-95b.b-cdn.net/bcdn_token=HS256-abc&token_path=%2Fvid%2F&expires=1792468301/vid/playlist.m3u8',
  );

  test('parses every rendition, cheapest first', () {
    final variants = parseMasterPlaylist(_master, masterUri);
    expect(variants.map((v) => v.label).toList(), ['240P', '360P', '480P']);
    expect(variants.first.height, 352);
    expect(variants.last.width, 480);
  });

  test('keeps the signature token when resolving variant urls', () {
    final variants = parseMasterPlaylist(_master, masterUri);
    expect(
      variants.last.url,
      'https://vz-114a9213-95b.b-cdn.net/bcdn_token=HS256-abc&token_path=%2Fvid%2F&expires=1792468301/vid/480p/video.m3u8',
    );
  });

  test('reads BANDWIDTH, not AVERAGE-BANDWIDTH', () {
    final variants = parseMasterPlaylist(_master, masterUri);
    expect(variants.first.bandwidth, 682816);
  });

  test('ignores comments between the tag and the uri', () {
    const withComments = '''
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720
# a comment line

720p/video.m3u8
''';
    final variants = parseMasterPlaylist(withComments, masterUri);
    expect(variants.single.label, '720P');
    expect(variants.single.url.endsWith('/vid/720p/video.m3u8'), isTrue);
  });

  test('returns nothing for a media playlist', () {
    const media = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n';
    expect(parseMasterPlaylist(media, masterUri), isEmpty);
  });
}
