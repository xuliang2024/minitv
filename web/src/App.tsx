import { useEffect, useState } from "react";

type Plan = { code: string; name_en: string; price_cents: number; benefits?: { items?: string[]; best?: boolean } };
type Drama = { id: number; title: string; cover_url: string; play_count: number };
type Marketing = {
  stats: { label: string; value: string }[];
  testimonials: { author: string; body: string }[];
  stores: Record<string, string>;
  trending: Drama[];
  plans: Plan[];
};

const nav = [
  ["home", "Home"],
  ["shows", "Shows"],
  ["premium", "Premium"],
  ["about", "About"],
  ["download", "Download App"],
];

export default function App() {
  const [data, setData] = useState<Marketing | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    fetch("/v1/marketing")
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then(setData)
      .catch(() => setError("Could not load MiniTV. Retry."));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div style={{ background: "#0C0A0B", color: "#fff", minHeight: "100vh", fontFamily: "Inter, sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", padding: "20px 48px", maxWidth: 1440, margin: "0 auto" }}>
        <strong style={{ color: "#E53B3E" }}>MiniTV</strong>
        <nav style={{ display: "flex", gap: 20 }}>
          {nav.map(([id, label]) => (
            <a key={id} href={`#${id}`} style={{ color: "#fff", textDecoration: "none" }}>{label}</a>
          ))}
        </nav>
      </header>
      <main style={{ maxWidth: 1440, margin: "0 auto", padding: "0 48px 80px" }}>
        <section id="home" style={{ padding: "48px 0" }}>
          <h1>Vertical dramas, one swipe away.</h1>
          <p>Free episodes. Unlock the rest with coins or membership.</p>
          <a href="#download" style={{ background: "#E53B3E", color: "#fff", padding: "12px 20px", borderRadius: 8, textDecoration: "none" }}>Download App</a>
        </section>
        {error && <button onClick={load}>{error}</button>}
        <section style={{ display: "flex", gap: 24 }}>
          {(data?.stats ?? []).map((stat) => (
            <div key={stat.label}><strong>{stat.value}</strong><div>{stat.label}</div></div>
          ))}
        </section>
        <section id="about" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 40 }}>
          {["Vertical swipe", "Coins", "Membership"].map((title) => (
            <article key={title} style={{ background: "#171013", padding: 16, borderRadius: 12 }}><h3>{title}</h3></article>
          ))}
        </section>
        <section id="shows" style={{ marginTop: 40 }}>
          <h2>Trending</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12 }}>
            {(data?.trending ?? []).map((drama) => (
              <figure key={drama.id} style={{ margin: 0 }}>
                <img src={drama.cover_url} alt="" style={{ width: "100%", aspectRatio: "3 / 4", objectFit: "cover", borderRadius: 8, background: "#222" }} />
                <figcaption>{drama.title}</figcaption>
              </figure>
            ))}
          </div>
        </section>
        <section style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 40 }}>
          {(data?.testimonials ?? []).map((item) => (
            <blockquote key={item.author} style={{ margin: 0, background: "#171013", padding: 16, borderRadius: 12 }}>
              {item.body}<footer>{item.author}</footer>
            </blockquote>
          ))}
        </section>
        <section id="premium" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 40 }}>
          {(data?.plans ?? []).map((plan) => (
            <article key={plan.code} style={{ border: plan.benefits?.best ? "1px solid #E53B3E" : "1px solid #333", borderRadius: 12, padding: 16 }}>
              <h3>{plan.name_en}{plan.benefits?.best ? " · Best" : ""}</h3>
              <p>${(plan.price_cents / 100).toFixed(2)}</p>
              <ul>{(plan.benefits?.items ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
              <a href="#download">Get the app</a>
            </article>
          ))}
        </section>
        <section>
          <h2>FAQ</h2>
          <p>Some episodes are free. Locked episodes use coins or a membership. Cancel from Me, Subscription in the app.</p>
        </section>
        <section id="download" style={{ marginTop: 40 }}>
          <h2>Download</h2>
          <p><a href={data?.stores.ios || "#"}>iOS</a> · <a href={data?.stores.android || "#"}>Android</a></p>
        </section>
        <footer style={{ marginTop: 48, display: "flex", gap: 16 }}>
          <a href="/terms" onClick={(e) => e.preventDefault()}>Terms</a>
          <a href="/privacy" onClick={(e) => e.preventDefault()}>Privacy</a>
          <a href="/cookies" onClick={(e) => e.preventDefault()}>Cookie Settings</a>
        </footer>
      </main>
    </div>
  );
}
