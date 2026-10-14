import { useEffect, useState } from "react";

import { fetchPreviewCatalog, PreviewCatalog, PreviewDrama } from "./api";
import { PosterImage, formatPlays } from "./ui";

/**
 * Website trial surface: public data only - for every category the first few
 * shows that have free episodes. Free episodes play here; locked episodes and
 * "view all" send users to the app.
 */
function PreviewCard({ drama }: { drama: PreviewDrama }) {
  const locked = Math.max(0, drama.episode_count - drama.free_playable_count);
  const meta = [
    drama.tags.slice(0, 2).join(" · "),
    drama.play_count > 0 ? `${formatPlays(drama.play_count)} plays` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <a className="poster preview-card" href={`#watch/${drama.id}`}>
      <span className="poster-frame">
        <PosterImage src={drama.cover_url} alt={`${drama.title} cover`} />
        <span className="preview-badge">
          {drama.free_playable_count === 1 ? "1 free episode" : `${drama.free_playable_count} free episodes`}
        </span>
        <span className="poster-overlay">
          <span className="poster-overlay-play">▶ Watch free</span>
          {locked > 0 && (
            <span className="poster-overlay-note">
              {locked === 1 ? "1 locked episode" : `${locked} locked episodes`} in the app
            </span>
          )}
        </span>
      </span>
      <span className="poster-title">{drama.title}</span>
      {meta && <span className="poster-meta">{meta}</span>}
    </a>
  );
}

export default function PreviewPage() {
  const [data, setData] = useState<PreviewCatalog | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    fetchPreviewCatalog()
      .then(setData)
      .catch(() => setError("Could not load the free preview. Check the local API and retry."));
  };

  useEffect(() => {
    load();
  }, []);

  const loading = !data && !error;
  // Categories only surface as their own row when they can fill it; single-show
  // genres still reach viewers through the "All shows" row.
  const rows = (data?.categories ?? []).filter((category) => category.id === 0 || category.total >= 2);

  return (
    <main className="preview-page">
      <section className="section preview-hero">
        <div className="shell">
          <a className="back-link" href="#home">
            ← Back to home
          </a>
          <span className="eyebrow">Free Preview</span>
          <h1>Watch free episodes online</h1>
          <p className="section-sub preview-lede">
            The first {data?.limit ?? 7} shows in every category stream free right here in your browser.
            Paid episodes and the full library unlock in the MiniTV app.
          </p>
          <ul className="preview-notes">
            <li>
              <span className="preview-note-icon" aria-hidden="true">
                ▶
              </span>
              Free episodes play instantly — no account needed
            </li>
            <li>
              <span className="preview-note-icon" aria-hidden="true">
                🔒
              </span>
              Paid episodes stay in the app
            </li>
            <li>
              <span className="preview-note-icon" aria-hidden="true">
                ↓
              </span>
              Download the app to watch every episode
            </li>
          </ul>
        </div>
      </section>

      {error && (
        <div className="shell">
          <div className="error-banner">
            <span>{error}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
              Retry
            </button>
          </div>
        </div>
      )}

      {loading &&
        Array.from({ length: 3 }, (_, row) => (
          <section className="section preview-row" key={row}>
            <div className="shell">
              <div className="section-head">
                <div>
                  <span className="skeleton preview-heading-skeleton" />
                </div>
              </div>
              <div className="poster-grid preview-grid">
                {Array.from({ length: 6 }, (_, index) => (
                  <div className="poster" key={index}>
                    <span className="skeleton poster-skeleton" />
                    <span className="skeleton poster-title-skeleton" />
                  </div>
                ))}
              </div>
            </div>
          </section>
        ))}

      {!loading &&
        !error &&
        rows.map((category) => (
          <section className="section preview-row" key={category.id}>
            <div className="shell">
              <div className="section-head">
                <div>
                  <h2>{category.name}</h2>
                  <p className="section-sub">
                    {category.total > category.dramas.length
                      ? `Showing the first ${category.dramas.length} of ${category.total} shows — the rest are in the app`
                      : `${category.total} ${category.total === 1 ? "show" : "shows"} with free episodes`}
                  </p>
                </div>
                <a className="text-link" href="#download">
                  View all in the app →
                </a>
              </div>
              <div className="poster-grid preview-grid">
                {category.dramas.map((drama) => (
                  <PreviewCard key={drama.id} drama={drama} />
                ))}
              </div>
            </div>
          </section>
        ))}

      {!loading && !error && rows.length === 0 && (
        <div className="shell">
          <p className="empty-state">No free episodes are live yet. Check back after the next release.</p>
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <section className="section preview-footer-cta">
          <div className="shell">
            <div className="download-card">
              <div className="download-copy">
                <span className="eyebrow">Want more?</span>
                <h2>Unlock every episode in the app</h2>
                <p>
                  Coins or a membership unlock paid episodes, downloads and the full library — while free
                  previews stay available here on the web.
                </p>
                <div className="store-buttons">
                  <a className="btn btn-primary" href="#download">
                    Get the MiniTV app
                  </a>
                  <a className="btn btn-ghost" href="#premium">
                    See plans
                  </a>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
