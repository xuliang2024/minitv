import { useState } from "react";
import { loadLocale, saveLocale, t, type Locale } from "./copy";
import { ForYouPage } from "./pages/ForYouPage";
import { HomePage } from "./pages/HomePage";
import { PlayPage } from "./pages/PlayPage";
import { SearchPage } from "./pages/SearchPage";

type Tab = "home" | "foryou" | "rewards" | "me";

const TABS: { id: Tab; key: string; icon: string }[] = [
  { id: "home", key: "nav.home", icon: "⌂" },
  { id: "foryou", key: "nav.foryou", icon: "▷" },
  { id: "rewards", key: "nav.rewards", icon: "✦" },
  { id: "me", key: "nav.me", icon: "☺" },
];

export function App() {
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
          {searching ? null : tab === "home" ? <div className="brand"><i>M</i>iniTV</div> : <h1>{t(locale, TABS.find((item) => item.id === tab)!.key)}</h1>}
          <span style={{ flex: 1 }} />
          <button className="icon-btn" onClick={switchLocale}>{locale === "zh" ? "EN" : "中"}</button>
          {!searching && <button className="icon-btn" onClick={() => setSearching(true)}>⌕</button>}
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
        ) : (
          <div className="center">{t(locale, "coming")}</div>
        )}
      </main>
      {!playing && !searching && (
        <nav className="tabs">
          {TABS.map((item) => (
            <a key={item.id} className={item.id === tab ? "on" : ""} onClick={() => setTab(item.id)}>
              <span>{item.icon}</span>
              {t(locale, item.key)}
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}
