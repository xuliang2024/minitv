/** Full web app (the H5 build on its own domain). Anything that leaves the
 * marketing site goes through this constant so the URL is never typed twice. */
export const WEB_APP_URL = "https://h5.minitv.us";

type NavItem = { label: string; href: string; external?: boolean };

const nav: NavItem[] = [
  { label: "Home", href: "#home" },
  { label: "Shows", href: "#shows" },
  { label: "Free Preview", href: "#preview" },
  { label: "Watch Online", href: WEB_APP_URL, external: true },
  { label: "Premium", href: "#premium" },
  { label: "About", href: "#about" },
  { label: "Download App", href: "#download" },
];

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="shell header-inner">
        <a className="brand" href="#home">
          <img className="brand-mark" src="/brand/minitv-icon.png" alt="" aria-hidden="true" />
          MiniTV
        </a>
        <nav className="site-nav" aria-label="Main">
          {nav.map((item) => (
            <a
              key={item.label}
              href={item.href}
              className={item.external ? "site-nav-external" : undefined}
              {...(item.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
            >
              {item.label}
              {item.external && (
                <span className="nav-ext" aria-hidden="true">
                  ↗
                </span>
              )}
            </a>
          ))}
        </nav>
        <a className="btn btn-primary btn-sm" href="#download">
          Get the app
        </a>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="shell">
        <div className="footer-grid">
          <div className="footer-brand">
            <a className="brand" href="#home">
              <img className="brand-mark" src="/brand/minitv-icon.png" alt="" aria-hidden="true" />
              MiniTV
            </a>
            <p>Vertical short dramas, built for the phone. Watch free episodes on the web, unlock everything in the app.</p>
          </div>
          <div className="footer-col">
            <h4>Product</h4>
            <a href="#shows">Shows</a>
            <a href="#preview">Free Preview</a>
            <a href={WEB_APP_URL} target="_blank" rel="noopener noreferrer">
              Watch Online ↗
            </a>
            <a href="#premium">Premium</a>
            <a href="#download">Download App</a>
          </div>
          <div className="footer-col">
            <h4>Company</h4>
            <a href="#about">About</a>
            <a href="#home">Contact</a>
          </div>
          <div className="footer-col">
            <h4>Legal</h4>
            <a href="/terms" onClick={(event) => event.preventDefault()}>
              Terms
            </a>
            <a href="/privacy.html">Privacy</a>
            <a href="/cookies" onClick={(event) => event.preventDefault()}>
              Cookie Settings
            </a>
          </div>
        </div>
        <div className="footer-bottom">
          <span>© {new Date().getFullYear()} MiniTV. All rights reserved.</span>
          <span>Prices shown in USD. Subscriptions are billed in the app.</span>
        </div>
      </div>
    </footer>
  );
}
