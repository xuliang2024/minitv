const nav: [string, string][] = [
  ["home", "Home"],
  ["shows", "Shows"],
  ["preview", "Free Preview"],
  ["premium", "Premium"],
  ["about", "About"],
  ["download", "Download App"],
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
          {nav.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
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
