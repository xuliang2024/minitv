import { useEffect, useState } from "react";
import { getJson } from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { RANK_COLORS } from "../format";
import type { Banner, ChannelTab, ConfigPayload, DramaCard, HomeGroup, HomePayload } from "../types";

interface Props {
  locale: Locale;
  onOpen: (dramaId: number) => void;
}

export function HomePage({ locale, onOpen }: Props) {
  const [tabs, setTabs] = useState<ChannelTab[]>([]);
  const [banners, setBanners] = useState<Banner[]>([]);
  const [groups, setGroups] = useState<HomeGroup[]>([]);
  const [tabIndex, setTabIndex] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [group, setGroup] = useState<HomeGroup | null>(null);

  const load = () => {
    setLoading(true);
    setError(false);
    Promise.all([
      getJson<ConfigPayload>(`/v1/config?locale=${locale}`),
      getJson<HomePayload>("/v1/home/groups"),
    ])
      .then(([config, home]) => {
        setTabs(config.tabs);
        setBanners(home.banners);
        setGroups(home.groups);
        setTabIndex((i) => (i >= config.tabs.length ? 0 : i));
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  useEffect(load, [locale]);

  if (group) {
    return (
      <div className="group-page">
        <div className="group-bar">
          <button className="icon-btn" onClick={() => setGroup(null)}>←</button>
          <h1>{group.name}</h1>
        </div>
        <PosterGrid dramas={group.dramas} onOpen={onOpen} />
      </div>
    );
  }

  if (loading) return <div className="center">…</div>;
  if (error) return <div className="center"><button className="retry" onClick={load}>{t(locale, "retry")}</button></div>;
  if (tabs.length === 0) return <div className="center">{t(locale, "empty")}</div>;

  const tab = tabs[tabIndex];
  const discover = tab.locked || tabIndex === 0;
  const bound = groups.find((item) => item.id === tab.group_id) ?? null;

  return (
    <div>
      <div className="channel">
        {tabs.map((item, index) => (
          <button key={item.code} className={index === tabIndex ? "on" : ""} onClick={() => setTabIndex(index)}>
            {locale === "zh" && item.title_zh ? item.title_zh : item.title_en}
          </button>
        ))}
      </div>
      {discover ? (
        <Discover banners={banners} groups={groups} locale={locale} onOpen={onOpen} onMore={setGroup} />
      ) : (
        <PosterGrid dramas={bound?.dramas ?? []} onOpen={onOpen} />
      )}
    </div>
  );
}

function Discover({
  banners, groups, locale, onOpen, onMore,
}: {
  banners: Banner[];
  groups: HomeGroup[];
  locale: Locale;
  onOpen: (id: number) => void;
  onMore: (group: HomeGroup) => void;
}) {
  const visible = groups.filter((group) => group.dramas.length > 0);
  if (banners.length === 0 && visible.length === 0) return <div className="center">{t(locale, "empty")}</div>;
  return (
    <>
      {banners.length > 0 && <BannerCarousel banners={banners} locale={locale} onOpen={onOpen} />}
      {visible.map((group) => (
        <Floor key={group.id} group={group} locale={locale} onOpen={onOpen} onMore={() => onMore(group)} />
      ))}
    </>
  );
}

function BannerCarousel({ banners, locale, onOpen }: { banners: Banner[]; locale: Locale; onOpen: (id: number) => void }) {
  const [page, setPage] = useState(0);
  return (
    <div className="banner">
      <div
        className="banner-track"
        onScroll={(event) => {
          const el = event.currentTarget;
          setPage(Math.round(el.scrollLeft / el.clientWidth));
        }}
      >
        {banners.map((banner) => (
          <button
            key={banner.id}
            className="banner-slide"
            onClick={() => banner.drama_id != null && onOpen(banner.drama_id)}
          >
            {banner.cover_url && <img src={banner.cover_url} alt="" />}
            <div className="banner-copy">
              <span className="chip">{t(locale, "exclusive")}</span>
              {banner.kicker && <p className="kicker">{banner.kicker}</p>}
              <p className="headline">{banner.headline || banner.title || ""}</p>
            </div>
          </button>
        ))}
      </div>
      {banners.length > 1 && (
        <div className="dots">
          {banners.map((banner, index) => <i key={banner.id} className={index === page ? "on" : ""} />)}
        </div>
      )}
    </div>
  );
}

function Floor({
  group, locale, onOpen, onMore,
}: {
  group: HomeGroup;
  locale: Locale;
  onOpen: (id: number) => void;
  onMore: () => void;
}) {
  const style = group.style || "poster3";
  return (
    <section>
      <div className="floor-head">
        <b />
        <span>{group.name}</span>
        <button onClick={onMore}>{t(locale, "more")} ›</button>
      </div>
      {style === "rank" ? (
        <RankList dramas={group.dramas.slice(0, 3)} onOpen={onOpen} />
      ) : style === "grid2" ? (
        <LandscapeGrid dramas={group.dramas.slice(0, 4)} badge={group.badge} locale={locale} onOpen={onOpen} />
      ) : (
        <PosterGrid dramas={group.dramas.slice(0, 6)} badge={group.badge} onOpen={onOpen} />
      )}
    </section>
  );
}

function PosterGrid({ dramas, onOpen, badge }: { dramas: DramaCard[]; onOpen: (id: number) => void; badge?: string }) {
  if (dramas.length === 0) return null;
  return (
    <div className="grid3">
      {dramas.map((drama, index) => (
        <button key={drama.id} className="card" onClick={() => onOpen(drama.id)}>
          <div className="poster">
            {drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}
            {badge === "rank" && (
              <span className="rank-no" style={{ color: index < 3 ? RANK_COLORS[index] : "rgba(255,255,255,.7)" }}>{index + 1}</span>
            )}
          </div>
          <p>{drama.title}</p>
        </button>
      ))}
    </div>
  );
}

function LandscapeGrid({
  dramas, badge, locale, onOpen,
}: {
  dramas: DramaCard[];
  badge: string;
  locale: Locale;
  onOpen: (id: number) => void;
}) {
  return (
    <div className="grid2">
      {dramas.map((drama) => (
        <button key={drama.id} className="card" onClick={() => onOpen(drama.id)}>
          <div className="land">
            {drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}
            {badge === "label" && <span className="hot">{t(locale, "hot")}</span>}
          </div>
          <p>{drama.title}</p>
        </button>
      ))}
    </div>
  );
}

function RankList({ dramas, onOpen }: { dramas: DramaCard[]; onOpen: (id: number) => void }) {
  return (
    <div className="rank-card">
      {dramas.map((drama, index) => (
        <button key={drama.id} className="rank-row" onClick={() => onOpen(drama.id)}>
          <em style={{ color: RANK_COLORS[Math.min(index, 2)] }}>{index + 1}</em>
          <span className="thumb">{drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}</span>
          <span>
            <strong>{drama.title}</strong>
            <small>NO.{index + 1}</small>
          </span>
        </button>
      ))}
    </div>
  );
}
