import 'package:flutter_test/flutter_test.dart';
import 'package:minitv/img.dart';

void main() {
  group('imgUrl', () {
    test('asks the pull zone for a width/quality variant', () {
      expect(
        imgUrl('https://minitv-dev-img.b-cdn.net/covers/113/cover.jpg', 480),
        'https://minitv-dev-img.b-cdn.net/covers/113/cover.jpg?width=480&quality=75',
      );
    });

    test('keeps the cache-busting version param', () {
      expect(
        imgUrl('https://minitv-dev-img.b-cdn.net/covers/113/cover.jpg?v=17', 180),
        'https://minitv-dev-img.b-cdn.net/covers/113/cover.jpg?v=17&width=180&quality=75',
      );
    });

    test('never double-sizes a url', () {
      const sized = 'https://minitv-dev-img.b-cdn.net/covers/113/cover.jpg?width=960';
      expect(imgUrl(sized, 480), sized);
    });

    test('leaves other hosts untouched (google avatars, marketing site)', () {
      const avatar = 'https://lh3.googleusercontent.com/a/abc=s96-c';
      expect(imgUrl(avatar, 144), avatar);
    });

    test('returns empty for a missing cover', () {
      expect(imgUrl(null, 480), '');
      expect(imgUrl('', 480), '');
    });
  });
}
