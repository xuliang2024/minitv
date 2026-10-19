import { useEffect, useRef, useState } from "react";

import { fetchMarketing, fetchPreviewCatalog, Marketing, Plan, PreviewDrama } from "./api";
import { SiteFooter, SiteHeader, WEB_APP_URL } from "./layout";
import PreviewPage from "./preview";
import { parseRoute, Route, scrollToRoute } from "./routes";
import { PosterImage, formatPlays } from "./ui";
import WatchPage from "./watch";

type FeatureIcon = "swipe" | "coins" | "diamond";

const features: { icon: FeatureIcon; title: string; body: string }[] = [
  {
    icon: "swipe",
    title: "Vertical swipe",
    body: "Every episode is shot for the phone. Swipe up and the next one starts — no rotation, no waiting.",
  },
  {
    icon: "coins",
    title: "Coins",
    body: "Watch free episodes every day, then spend coins to unlock the ones you cannot stop thinking about.",
  },
  {
    icon: "diamond",
    title: "Membership",
    body: "Skip the coins. Members unlock the whole library, ad-free, in up to 1080p.",
  },
];

function FeatureGlyph({ name }: { name: FeatureIcon }) {
  if (name === "swipe") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="7" y="3" width="10" height="18" rx="3" />
        <path d="M12 16.5h.01" />
        <path d="M12 7.2v4.6" />
        <path d="M10.2 8.8 12 7l1.8 1.8" />
      </svg>
    );
  }
  if (name === "coins") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <ellipse cx="12" cy="6.5" rx="7" ry="3" />
        <path d="M5 6.5v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" />
        <path d="M5 11.5v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3.5 4 9l8 11.5L20 9z" />
      <path d="M4 9h16" />
      <path d="M9.5 9 12 3.7 14.5 9" />
      <path d="M9.5 9 12 20.2 14.5 9" />
    </svg>
  );
}

const faqs = [
  {
    q: "Is MiniTV free?",
    a: "Yes. Many episodes are free to watch. Locked episodes unlock with coins or a membership.",
  },
  {
    q: "Can I watch on the website?",
    a: "Yes — the Free Preview page streams the first free episodes of our shows right in your browser, no account needed. Paid episodes and full series unlock in the app.",
  },
  {
    q: "Where do I download the app?",
    a: "The iOS and Android apps are coming soon. Until they launch, you can watch free episodes and the full web app right here in your browser.",
  },
  {
    q: "How do coins work?",
    a: "Earn coins from daily check-ins and rewards, then spend them to unlock locked episodes. Members skip coins entirely.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes. Cancel from Me → Subscription in the app. Your benefits stay active until the end of the billing period.",
  },
];

const planUnit: Record<string, string> = {
  weekly: "week",
  monthly: "month",
  yearly: "year",
};

function HomePage() {
  const [data, setData] = useState<Marketing | null>(null);
  const [error, setError] = useState("");
  const [teaser, setTeaser] = useState<PreviewDrama[]>([]);
  // The store listings are not live yet: the badges stay, the click explains
  // why and points people at the browser build instead.
  const [storeNotice, setStoreNotice] = useState("");
  const noticeTimer = useRef<number | null>(null);

  const showStoreNotice = (platform: string) => {
    setStoreNotice(`${platform} app is coming soon — watch online in your browser for now.`);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setStoreNotice(""), 5000);
  };

  useEffect(
    () => () => {
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    },
    [],
  );

  const load = () => {
    setError("");
    fetchMarketing()
      .then(setData)
      .catch(() => setError("Could not load MiniTV. Check the local API and retry."));
  };

  useEffect(() => {
    load();
    fetchPreviewCatalog()
      .then((catalog) => setTeaser(catalog.categories.find((category) => category.id === 0)?.dramas.slice(0, 6) ?? []))
      .catch(() => setTeaser([]));
  }, []);

  // Async sections (stats, plans, teaser row) change the page height, which
  // can strand a #download / #premium anchor; re-anchor once content settles.
  useEffect(() => {
    if (data) scrollToRoute(parseRoute(), { instant: true });
  }, [data, teaser]);

  const stats = data?.stats ?? [];
  const rating = stats.find((stat) => stat.label.toLowerCase().includes("rating"));
  const downloads = stats.find((stat) => stat.label.toLowerCase().includes("download"));
  const trending = data?.trending ?? [];
  const plans: Plan[] = data?.plans ?? [];
  const testimonials = data?.testimonials ?? [];
  const loading = !data && !error;

  return (
    <main>
      <section id="home" className="hero">
        <div className="shell hero-inner">
          <div className="hero-copy">
            <span className="eyebrow">Vertical drama · New episodes daily</span>
            <h1>
              Vertical dramas, <em>one swipe away.</em>
            </h1>
            <p className="lede">
              Free episodes play right here on the web. Unlock the rest — and the whole
              library — in the MiniTV app.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary" href="#preview">
                Watch free episodes
              </a>
              <a className="btn btn-ghost" href="#download">
                Download App
              </a>
              <a className="btn btn-ghost" href="#premium">
                See plans
              </a>
            </div>
            <p className="hero-web-link">
              Prefer the browser?{" "}
              <a href={WEB_APP_URL} target="_blank" rel="noopener noreferrer">
                Open the web app ↗
              </a>
            </p>
            {stats.length > 0 && (
              <ul className="hero-meta">
                {rating && (
                  <li>
                    <strong>★★★★★</strong>
                    {rating.value} average rating
                  </li>
                )}
                {downloads && <li>{downloads.value} downloads worldwide</li>}
              </ul>
            )}
          </div>
          <div className="hero-art">
            <div className="phone">
              <img src="/ph_feed.png" alt="A MiniTV drama playing in the mobile app" />
            </div>
            {rating && <div className="chip chip-top">★ {rating.value} rating</div>}
            {downloads && <div className="chip chip-bottom">{downloads.value} downloads</div>}
          </div>
        </div>
      </section>

      <section className="stats">
        <div className="shell">
          <div className="stats-grid">
            {loading
              ? Array.from({ length: 4 }, (_, index) => (
                  <div className="stat" key={index}>
                    <span className="skeleton stat-value-skeleton" />
                    <span className="skeleton stat-label-skeleton" />
                  </div>
                ))
              : stats.map((stat) => (
                  <div className="stat" key={stat.label}>
                    <div className="stat-value">{stat.value}</div>
                    <div className="stat-label">{stat.label}</div>
                  </div>
                ))}
          </div>
          {error && (
            <div className="error-banner">
              <span>{error}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
                Retry
              </button>
            </div>
          )}
        </div>
      </section>

      <section id="about" className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <span className="eyebrow">Why MiniTV</span>
              <h2>Built for the way you watch</h2>
              <p className="section-sub">
                Short, vertical, and always one swipe from the next episode.
              </p>
            </div>
          </div>
          <div className="feature-grid">
            {features.map((feature) => (
              <article className="feature" key={feature.title}>
                <span className="feature-icon">
                  <FeatureGlyph name={feature.icon} />
                </span>
                <h3>{feature.title}</h3>
                <p>{feature.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="shows" className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <span className="eyebrow">Shows</span>
              <h2>Trending now</h2>
              <p className="section-sub">The dramas our viewers keep coming back to.</p>
            </div>
            <a className="text-link" href="#download">
              Watch in the app →
            </a>
          </div>
          <div className="poster-grid">
            {loading
              ? Array.from({ length: 6 }, (_, index) => (
                  <div className="poster" key={index}>
                    <span className="skeleton poster-skeleton" />
                    <span className="skeleton poster-title-skeleton" />
                  </div>
                ))
              : trending.map((drama) => (
                  <a className="poster" key={drama.id} href="#download">
                    <span className="poster-frame">
                      <PosterImage src={drama.cover_url} alt={`${drama.title} cover`} />
                      <span className="poster-overlay">Open in app</span>
                    </span>
                    <span className="poster-title">{drama.title}</span>
                    {drama.play_count > 0 && (
                      <span className="poster-meta">{formatPlays(drama.play_count)} plays</span>
                    )}
                  </a>
                ))}
          </div>
          {!loading && !error && trending.length === 0 && (
            <p className="empty-state">No published dramas yet. Check back after the next release.</p>
          )}
        </div>
      </section>

      {teaser.length > 0 && (
        <section className="section">
          <div className="shell">
            <div className="section-head">
              <div>
                <span className="eyebrow">Free Preview</span>
                <h2>Watch free episodes online</h2>
                <p className="section-sub">
                  The first episodes of our shows stream free in your browser — no app
                  needed. Paid episodes unlock in the MiniTV app.
                </p>
              </div>
              <a className="text-link" href="#preview">
                Open the free preview →
              </a>
            </div>
            <div className="poster-grid">
              {teaser.map((drama) => (
                <a className="poster" key={drama.id} href={`#watch/${drama.id}`}>
                  <span className="poster-frame">
                    <PosterImage src={drama.cover_url} alt={`${drama.title} cover`} />
                    <span className="preview-badge preview-badge-compact">
                      {drama.free_playable_count} free
                    </span>
                    <span className="poster-overlay">Watch free</span>
                  </span>
                  <span className="poster-title">{drama.title}</span>
                  <span className="poster-meta">Free episodes on the web</span>
                </a>
              ))}
            </div>
          </div>
        </section>
      )}

      {testimonials.length > 0 && (
        <section className="section">
          <div className="shell">
            <div className="section-head">
              <div>
                <span className="eyebrow">Loved by viewers</span>
                <h2>What people say</h2>
              </div>
            </div>
            <div className="quote-grid">
              {testimonials.map((item) => (
                <blockquote className="quote" key={item.author}>
                  <span className="quote-stars" aria-label="5 out of 5 stars">
                    ★★★★★
                  </span>
                  <p>{item.body}</p>
                  <footer>{item.author}</footer>
                </blockquote>
              ))}
            </div>
          </div>
        </section>
      )}

      <section id="premium" className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <span className="eyebrow">Premium</span>
              <h2>Pick how you unlock</h2>
              <p className="section-sub">
                Same three plans everywhere. Subscribe on the web with a card (secure
                checkout by Stripe) or in the app through the App Store or Google Play.
              </p>
            </div>
          </div>
          <div className="plan-grid">
            {loading
              ? Array.from({ length: 3 }, (_, index) => (
                  <div className="plan" key={index}>
                    <span className="skeleton stat-label-skeleton" />
                    <span className="skeleton stat-value-skeleton" />
                    <span className="skeleton stat-label-skeleton" />
                  </div>
                ))
              : plans.map((plan) => (
                  <article
                    className={plan.benefits?.best ? "plan best" : "plan"}
                    key={plan.code}
                  >
                    {plan.benefits?.best && <span className="plan-badge">Most popular</span>}
                    <h3 className="plan-name">{plan.name_en}</h3>
                    <p className="plan-price">
                      <strong>${(plan.price_cents / 100).toFixed(2)}</strong>
                      <span>/ {planUnit[plan.code] ?? "period"}</span>
                    </p>
                    <ul className="plan-benefits">
                      {(plan.benefits?.items ?? []).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                    <a
                      className={
                        plan.benefits?.best
                          ? "btn btn-primary btn-block plan-cta"
                          : "btn btn-ghost btn-block plan-cta"
                      }
                      href="https://h5.minitv.us"
                    >
                      Subscribe on the web
                    </a>
                  </article>
                ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <span className="eyebrow">FAQ</span>
              <h2>Good to know</h2>
            </div>
          </div>
          <div className="faq-list">
            {faqs.map((faq) => (
              <details className="faq-item" key={faq.q}>
                <summary>{faq.q}</summary>
                <p>{faq.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section id="download" className="section">
        <div className="shell">
          <div className="download-card">
            <div className="download-copy">
              <span className="eyebrow">Download</span>
              <h2>Get MiniTV on your phone</h2>
              <p>
                Install the app to unlock every episode, download shows, and manage your
                subscription. The app is coming soon — until then, free previews and the
                full web app work right here in your browser.
              </p>
              <div className="store-buttons">
                <button
                  type="button"
                  className="store-btn"
                  onClick={() => showStoreNotice("The iOS")}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.53 4.08zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z" />
                  </svg>
                  <span>
                    <small>Download on the</small>
                    <strong>App Store</strong>
                  </span>
                </button>
                <button
                  type="button"
                  className="store-btn"
                  onClick={() => showStoreNotice("The Android")}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M3.61 1.81 13.79 12 3.61 22.19a1 1 0 0 1-.61-.92V2.73a1 1 0 0 1 .61-.92zM14.5 12.7l2.3 2.3-10.94 6.34 8.64-8.64zm3.2-3.2 2.8 1.63a1 1 0 0 1 0 1.73l-2.8 1.63-2.5-2.49 2.5-2.5zM5.95 3.06l10.94 6.33-2.3 2.3-8.64-8.63z" />
                  </svg>
                  <span>
                    <small>Get it on</small>
                    <strong>Google Play</strong>
                  </span>
                </button>
                <a
                  className="store-btn store-btn-web"
                  href={WEB_APP_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <svg className="icon-stroke" viewBox="0 0 24 24" aria-hidden="true">
                    <circle cx="12" cy="12" r="8.4" />
                    <path d="M3.6 12h16.8" />
                    <path d="M12 3.6c2.4 2.5 2.4 14.4 0 16.8M12 3.6c-2.4 2.5-2.4 14.4 0 16.8" />
                  </svg>
                  <span>
                    <small>No download needed</small>
                    <strong>Watch Online</strong>
                  </span>
                </a>
              </div>
              {storeNotice && (
                <p className="store-note" role="status" aria-live="polite">
                  {storeNotice}
                </p>
              )}
            </div>
            <div className="qr-block">
              <div className="qr-tile">
                <img src="/qr-web.png" alt="QR code to watch MiniTV in your browser" />
              </div>
              <p className="qr-caption">Scan to watch on the web</p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const previousKind = useRef<Route["kind"] | null>(null);

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    // Page switches jump; anchors within the homepage keep smooth scrolling.
    const instant = previousKind.current !== null && previousKind.current !== route.kind;
    previousKind.current = route.kind;
    scrollToRoute(route, { instant });
  }, [route]);

  return (
    <div className="page">
      <SiteHeader />
      {route.kind === "home" && <HomePage />}
      {route.kind === "preview" && <PreviewPage />}
      {route.kind === "watch" && <WatchPage dramaId={route.dramaId} />}
      <SiteFooter />
    </div>
  );
}
