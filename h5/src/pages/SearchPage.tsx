import { useEffect, useState } from "react";
import { getJson } from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";

interface DramaHit {
  id: number;
  title: string;
}

export function SearchPage({ locale, onOpen, onBack }: { locale: Locale; onOpen: (id: number) => void; onBack: () => void }) {
  const [query, setQuery] = useState("");
  const [trending, setTrending] = useState<string[]>([]);
  const [items, setItems] = useState<DramaHit[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    getJson<{ items: string[] }>("/v1/search/trending")
      .then((data) => setTrending(data.items))
      .catch(() => setError(true));
  }, []);

  const search = (term = query) => {
    const q = term.trim();
    if (!q) return;
    setQuery(q);
    getJson<{ items: DramaHit[] }>(`/v1/search?q=${encodeURIComponent(q)}`)
      .then((data) => { setItems(data.items); setError(false); })
      .catch(() => setError(true));
  };

  return (
    <div>
      <div className="group-bar">
        <button className="icon-btn" onClick={onBack}>←</button>
        <h1>{t(locale, "search")}</h1>
      </div>
      <form className="search" onSubmit={(event) => { event.preventDefault(); search(); }}>
        <input value={query} placeholder={t(locale, "search")} onChange={(event) => setQuery(event.target.value)} />
      </form>
      {error && <div className="center"><button className="retry" onClick={() => search()}>{t(locale, "retry")}</button></div>}
      <div className="search">
        {trending.map((term) => (
          <button key={term} className="term" onClick={() => search(term)}>{term}</button>
        ))}
        {items.map((item) => (
          <button key={item.id} className="result" onClick={() => onOpen(item.id)}>{item.title}</button>
        ))}
      </div>
    </div>
  );
}
