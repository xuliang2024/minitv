import { useState, type ReactNode } from "react";
import { AuthProvider } from "./auth/AuthProvider";
import { LoginSheet } from "./auth/LoginSheet";
import { loadLocale, saveLocale, t, type Locale } from "./copy";
import { IconForYou, IconHome, IconMe, IconRewards, IconSearch } from "./icons";
import { ForYouPage } from "./pages/ForYouPage";
import { HomePage } from "./pages/HomePage";
import { MePage } from "./pages/MePage";
import { PlayPage } from "./pages/PlayPage";
import { SearchPage } from "./pages/SearchPage";

type Tab = "home" | "foryou" | "rewards" | "me";

interface TabItem {
  id: Tab;
  key: string;
  renderIcon: (active: boolean) => ReactNode;
}

const TABS: TabItem[] = [
  { id: "home", key: "nav.home", renderIcon: (active) => <IconHome active={active} /> },
  { id: "foryou", key: "nav.foryou", renderIcon: (active) => <IconForYou active={active} /> },
  { id: "rewards", key: "nav.rewards", renderIcon: (active) => <IconRewards active={active} /> },
  { id: "me", key: "nav.me", renderIcon: (active) => <IconMe active={active} /> },
];

export function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

function AppShell() {
  const [locale, setLocale] = useState<Locale>(loadLocale);
  const [tab, setTab] = useState<Tab>("home");
  const [dramaId, setDramaId] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);

  const switchLocale = () => {
    const next = locale === "zh" ? "en" : "zh";
    saveLocale(next);
    setLocale(next);
  };

  const playing = dramaId != null;

  return (
    <div className="app">
      {!playing && (
        <header className="topbar">
          {searching ? null : tab === "home" ? (
            <div className="brand" role="heading" aria-level={1}>
              <span className="brand-m">M</span>
              <span className="brand-rest">iniTV</span>
            </div>
          ) : (
            <h1>{t(locale, TABS.find((item) => item.id === tab)!.key)}</h1>
          )}
          <span style={{ flex: 1 }} />
          <button className="icon-btn" onClick={switchLocale}>{locale === "zh" ? "EN" : "中"}</button>
          {!searching && (
            <button className="icon-btn search-btn" onClick={() => setSearching(true)} aria-label={t(locale, "search")}>
              <IconSearch />
            </button>
          )}
        </header>
      )}
      <main className={playing || tab === "foryou" ? "stage bleed" : "stage"}>
        {playing ? (
          <PlayPage dramaId={dramaId} locale={locale} onBack={() => setDramaId(null)} />
        ) : searching ? (
          <SearchPage locale={locale} onBack={() => setSearching(false)} onOpen={(id) => { setSearching(false); setDramaId(id); }} />
        ) : tab === "home" ? (
          <HomePage locale={locale} onOpen={setDramaId} />
        ) : tab === "foryou" ? (
          <ForYouPage locale={locale} onOpenDrama={setDramaId} />
        ) : tab === "me" ? (
          <MePage locale={locale} />
        ) : (
          <div className="center">{t(locale, "coming")}</div>
        )}
      </main>
      {!playing && !searching && (
        <nav className="tabs" role="tablist">
          {TABS.map((item) => {
            const active = item.id === tab;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                data-tab={item.id}
                aria-selected={active}
                className={active ? "tab-item on" : "tab-item"}
                onClick={() => setTab(item.id)}
              >
                <span className="tab-icon">{item.renderIcon(active)}</span>
                <span className="tab-label">{t(locale, item.key)}</span>
              </button>
            );
          })}
        </nav>
      )}
      <LoginSheet locale={locale} />
    </div>
  );
}
