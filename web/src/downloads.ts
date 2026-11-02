/** Android build served straight from the marketing-site bucket.
 *
 * Uploaded with `python3 scripts/release.py r2 --dist <dir> --prefix download
 * --allow-no-index`, which lands the file at `https://minitv.us/download/...`.
 * The filename carries the version so a rebuilt APK is never served from a
 * warm CDN edge; bump all three fields together when a new build ships.
 */
export const ANDROID_APK = {
  url: "https://minitv.us/download/minitv-1.0.0-arm64-v8a.apk",
  version: "1.0.0",
  size: "20 MB",
};

/** Minimum Android the current Flutter toolchain targets (minSdk 24). */
export const ANDROID_MIN = "7.0";
