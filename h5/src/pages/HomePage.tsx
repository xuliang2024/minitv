import { useEffect, useState } from "react";
import { getJson } from "../api";
import type { Locale } from "../copy";
import { t, floorName } from "../copy";
import { formatCount } from "../format";
import {
  IconChevronRight,
  IconCrown,
  IconFire,
  IconPlaySmall,
  IconSparkle,
} from "../icons";
import type { Banner, ChannelTab, ConfigPayload, DramaCard, HomeGroup, HomePayload } from "../types";

interface Props {
  locale: Locale;
  onOpen: (dramaId: number) => void;
}

/**
 * The ops backend can be empty on a fresh environment (no ChannelTab rows).
 * Discover is the one pinned tab, so render it anyway instead of a blank page.
 */
const DEFAULT_TAB: ChannelTab = {
  code: "discover",
  title_en: "Discover",
  title_zh: "发现",
  position: 0,
  locked: true,
  group_id: null,
};

/** No banner configured: promote the first drama of the first floor instead. */
function heroFromGroups(groups: HomeGroup[], locale: Locale): Banner[] {
  for (const group of groups) {
    const [first] = group.dramas;
    if (first?.cover_url) {
      return [{
        id: -first.id,
        drama_id: first.id,
        slot: "hero",
        title: first.title,
        cover_url: first.cover_url,
        kicker: floorName(group.name, locale),
        headline: first.title,
      }];
    }
  }
  return [];
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
          <h1>{floorName(group.name, locale)}</h1>
        </div>
        <PosterGrid dramas={group.dramas} onOpen={onOpen} />
      </div>
    );
  }

  if (loading) return <HomeSkeleton />;
  if (error) return <div className="center"><button className="retry" onClick={load}>{t(locale, "retry")}</button></div>;

  const shown = tabs.length > 0 ? tabs : [DEFAULT_TAB];
  const tab = shown[tabIndex] ?? DEFAULT_TAB;
  const discover = tab.locked || tabIndex === 0;
  const bound = groups.find((item) => item.id === tab.group_id) ?? null;
  const hasContent = banners.length > 0 || groups.some((group) => group.dramas.length > 0);
  const hero = banners.length > 0 ? banners : heroFromGroups(groups, locale);

  return (
    <div className="home-page">
      <div className="channel-wrap">
        <div className="channel" role="tablist">
          {shown.map((item, index) => {
            const active = index === tabIndex;
            return (
              <button
                key={item.code}
                role="tab"
                aria-selected={active}
                className={active ? "channel-tab on" : "channel-tab"}
                onClick={() => setTabIndex(index)}
              >
                <span>{locale === "zh" && item.title_zh ? item.title_zh : item.title_en}</span>
                {active && <span className="channel-indicator" />}
              </button>
            );
          })}
        </div>
      </div>
      {!hasContent ? (
        <div className="center" style={{ height: "60vh" }}>{t(locale, "empty")}</div>
      ) : discover ? (
        <Discover banners={hero} groups={groups} locale={locale} onOpen={onOpen} onMore={setGroup} />
      ) : bound ? (
        <PosterGrid dramas={bound.dramas} onOpen={onOpen} />
      ) : (
        <div className="center" style={{ height: "50vh" }}>{t(locale, "empty")}</div>
      )}
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="home-skeleton" aria-hidden>
      <div className="sk-hero" />
      <div className="sk-floor" />
      <div className="grid3">
        {Array.from({ length: 6 }, (_, index) => <div key={index} className="sk-card" />)}
      </div>
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
          <div
            key={banner.id}
            className="banner-slide"
            role="button"
            tabIndex={0}
            onClick={() => banner.drama_id != null && onOpen(banner.drama_id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (banner.drama_id != null) onOpen(banner.drama_id);
              }
            }}
          >
            <span className="banner-media">
              {banner.cover_url && <img src={banner.cover_url} alt="" />}
            </span>
            <div className="banner-copy">
              <div className="banner-badge-row">
                <span className="chip">
                  <IconSparkle size={10} />
                  <span>{t(locale, "exclusive")}</span>
                </span>
              </div>
              {banner.kicker && <p className="kicker">{banner.kicker}</p>}
              <div className="banner-title-row">
                <h2 className="headline">{banner.headline || banner.title || ""}</h2>
                <span className="banner-play-btn" aria-hidden="true">
                  <IconPlaySmall size={14} />
                  <span>{locale === "zh" ? "立即观看" : "Watch"}</span>
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
      {banners.length > 1 && (
        <div className="dots" aria-hidden="true">
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
  const limit = style === "rank" ? 3 : style === "grid2" ? 4 : 6;
  const isHot = group.name.toLowerCase().includes("hot") || group.name.includes("热");
  return (
    <section className="floor-section">
      <div className="floor-head">
        <span className="floor-bar" />
        <h3 className="floor-title">
          <span>{floorName(group.name, locale)}</span>
          {isHot && (
            <span className="floor-fire-badge">
              <IconFire size={12} />
              <span>HOT</span>
            </span>
          )}
        </h3>
        {group.dramas.length > limit && (
          <button className="floor-more-btn" onClick={onMore} aria-label={t(locale, "more")}>
            <span>{t(locale, "more")}</span>
            <IconChevronRight size={12} />
          </button>
        )}
      </div>
      {style === "rank" ? (
        <RankList dramas={group.dramas.slice(0, limit)} onOpen={onOpen} locale={locale} />
      ) : style === "grid2" ? (
        <LandscapeGrid dramas={group.dramas.slice(0, limit)} badge={group.badge} locale={locale} onOpen={onOpen} />
      ) : (
        <PosterGrid dramas={group.dramas.slice(0, limit)} badge={group.badge} onOpen={onOpen} />
      )}
    </section>
  );
}

function PosterGrid({ dramas, onOpen, badge }: { dramas: DramaCard[]; onOpen: (id: number) => void; badge?: string }) {
  if (dramas.length === 0) return null;
  return (
    <div className="grid3">
      {dramas.map((drama, index) => (
        <button key={drama.id} className="card" onClick={() => onOpen(drama.id)} type="button">
          <div className="poster">
            <span className="poster-media">
              {drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}
            </span>
            {badge === "rank" && (
              <span className={`poster-rank-badge rank-${index + 1}`}>
                {index === 0 && <IconCrown size={10} />}
                <span>{index + 1}</span>
              </span>
            )}
            <div className="poster-gradient" />
          </div>
          <p className="card-title">{drama.title}</p>
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
        <button key={drama.id} className="card" onClick={() => onOpen(drama.id)} type="button">
          <div className="land">
            <span className="land-media">
              {drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}
            </span>
            {badge === "label" && (
              <span className="hot-pill">
                <IconFire size={10} />
                <span>{t(locale, "hot")}</span>
              </span>
            )}
            <div className="land-gradient" />
          </div>
          <p className="card-title">{drama.title}</p>
        </button>
      ))}
    </div>
  );
}

function RankList({
  dramas, onOpen, locale,
}: {
  dramas: DramaCard[];
  onOpen: (id: number) => void;
  locale: Locale;
}) {
  return (
    <div className="rank-list">
      {dramas.map((drama, index) => {
        const isTop1 = index === 0;
        const isTop2 = index === 1;
        const isTop3 = index === 2;
        const rankClass = isTop1 ? "rank-item rank-1" : isTop2 ? "rank-item rank-2" : isTop3 ? "rank-item rank-3" : "rank-item";

        return (
          <button
            key={drama.id}
            className={rankClass}
            onClick={() => onOpen(drama.id)}
            type="button"
          >
            <div className="rank-num-box">
              {isTop1 && <span className="rank-crown"><IconCrown size={14} /></span>}
              <span className="rank-num">{index + 1}</span>
            </div>

            <div className="rank-thumb-box">
              {drama.cover_url && <img className="cover" src={drama.cover_url} alt="" />}
            </div>

            <div className="rank-info-col">
              <strong className="rank-title">{drama.title}</strong>
              <div className="rank-meta-row">
                <span className="rank-tag">
                  {isTop1 ? "TOP 1" : isTop2 ? "TOP 2" : isTop3 ? "TOP 3" : `NO.${index + 1}`}
                </span>
                <span className="rank-trend">
                  <IconFire size={11} />
                  <span>{formatCount(isTop1 ? 1200000 : isTop2 ? 850000 : 560000, locale)}</span>
                </span>
              </div>
            </div>

            <span className="rank-play-btn" aria-hidden="true">
              <IconPlaySmall size={14} />
            </span>
          </button>
        );
      })}
    </div>
  );
}
