import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  authToken,
  checkin as postCheckin,
  claimTask,
  getPublicTasks,
  getTasks,
  type DailyTaskItem,
  type DailyTasksPayload,
} from "../api";
import { useAuth } from "../auth/AuthProvider";
import { t, type Locale } from "../copy";
import {
  IconCalendarCheck,
  IconCoin,
  IconHeart,
  IconPlaySmall,
  IconStar,
} from "../icons";
import { toast } from "../ui/toast";

type LoadState = "idle" | "loading" | "ready" | "error";

const CHECKIN_KEY = "minitv_h5_checkin";

/** Server names are Chinese; the task centre is bilingual, so map by code. */
const TASK_COPY: Record<string, { name: string; desc: string }> = {
  like_5: { name: "rw_task_like", desc: "rw_task_like_desc" },
  favorite_5: { name: "rw_task_favorite", desc: "rw_task_favorite_desc" },
  watch_8: { name: "rw_task_watch", desc: "rw_task_watch_desc" },
};

function localDay(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function TaskIcon({ metric }: { metric: DailyTaskItem["metric"] }) {
  if (metric === "like") return <IconHeart size={22} />;
  if (metric === "favorite") return <IconStar size={22} />;
  return <IconPlaySmall size={18} />;
}

export function RewardsPage({
  locale,
  onExplore,
}: {
  locale: Locale;
  onExplore: (target: "home" | "foryou") => void;
}): ReactNode {
  const { status, requireAuth, refresh } = useAuth();
  const [data, setData] = useState<DailyTasksPayload | null>(null);
  const [state, setState] = useState<LoadState>("idle");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (status === "loading") return;
    setState((prev) => (prev === "ready" ? prev : "loading"));
    try {
      // Signed-out visitors still get the cards; only the numbers are personal.
      setData(authToken() ? await getTasks() : await getPublicTasks());
      setState("ready");
    } catch {
      setState("error");
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const claim = (code: string) =>
    requireAuth(() => {
      setBusy(code);
      claimTask(code)
        .then((result) => {
          toast(
            result.replayed
              ? t(locale, "rw_claimed")
              : t(locale, "rw_claim_ok", { amount: result.amount }),
          );
          void load();
          void refresh();
        })
        .catch((error: unknown) => {
          const notDone = error instanceof ApiError && error.status === 409;
          toast(t(locale, notDone ? "rw_not_done" : "rw_claim_fail"));
        })
        .finally(() => setBusy(null));
    });

  const checkin = () =>
    requireAuth(() => {
      setBusy("checkin");
      postCheckin()
        .then((result) => {
          localStorage.setItem(CHECKIN_KEY, localDay());
          toast(
            result.replayed
              ? t(locale, "me_checkin_already")
              : t(locale, "rw_claim_ok", { amount: result.amount }),
          );
          void load();
          void refresh();
        })
        .catch(() => toast(t(locale, "rw_claim_fail")))
        .finally(() => setBusy(null));
    });

  if (status === "loading") return <div className="center">…</div>;

  if (state === "error" || (state === "idle" && !data)) {
    return (
      <div className="center">
        <button type="button" className="retry" onClick={() => void load()}>
          {t(locale, "retry")}
        </button>
      </div>
    );
  }

  if (!data) return <div className="center">…</div>;

  const checked = data.checkin.checked_today;

  return (
    <div className="rw-page">
      <section className="rw-hero">
        <div className="rw-hero-copy">
          <h2>{t(locale, "rw_title")}</h2>
          <p>{t(locale, "rw_sub")}</p>
        </div>
        <div className="rw-balance" data-testid="rw-balance">
          <IconCoin size={15} />
          <b>{data.balance}</b>
          <span>{t(locale, "coins")}</span>
        </div>
      </section>

      <section className={`rw-checkin${checked ? " done" : ""}`}>
        <span className="rw-checkin-icon">
          <IconCalendarCheck size={22} />
        </span>
        <div className="rw-checkin-copy">
          <b>{t(locale, "rw_checkin_streak", { n: data.checkin.streak })}</b>
          <i>
            {checked
              ? t(locale, "rw_checkin_done", { amount: data.checkin.amount })
              : t(locale, "rw_checkin_ready", { amount: data.checkin.amount })}
          </i>
        </div>
        <button
          type="button"
          className={`rw-btn${checked ? " ghost" : " gold"}`}
          disabled={checked || busy === "checkin"}
          onClick={checkin}
          data-testid="rw-checkin"
        >
          {checked ? t(locale, "me_checkin_done") : t(locale, "rw_checkin_btn")}
        </button>
      </section>

      <div className="rw-list">
        {data.items.map((task) => {
          const copy = TASK_COPY[task.code];
          const name = copy ? t(locale, copy.name) : task.name;
          const desc = copy ? t(locale, copy.desc) : task.description;
          const pct = Math.max(0, Math.min(100, Math.round((task.progress / task.target) * 100)));
          const done = task.progress >= task.target;
          const pending = busy === task.code;
          const action = task.claimed
            ? null
            : done
              ? () => claim(task.code)
              : () =>
                  requireAuth(() =>
                    onExplore(task.metric === "complete" ? "foryou" : "home"),
                  );

          return (
            <article
              key={task.code}
              className={`rw-task${task.claimed ? " claimed" : ""}`}
              data-testid={`rw-task-${task.code}`}
            >
              <span className={`rw-task-icon ${task.metric}`}>
                <TaskIcon metric={task.metric} />
              </span>
              <div className="rw-task-body">
                <div className="rw-task-head">
                  <b>{name}</b>
                  <span className="rw-reward">
                    <IconCoin size={12} />+{task.amount}
                  </span>
                </div>
                <p>{desc}</p>
                <div className="rw-bar" aria-hidden="true">
                  <i style={{ width: `${pct}%` }} />
                </div>
                <div className="rw-meta">
                  <span data-testid={`rw-progress-${task.code}`}>
                    {task.progress}/{task.target}
                  </span>
                  <span>{task.claimed ? t(locale, "rw_claimed") : ""}</span>
                </div>
              </div>
              <button
                type="button"
                className={`rw-btn${task.claimed ? " ghost" : done ? " gold" : " line"}`}
                disabled={task.claimed || pending}
                onClick={action ?? undefined}
                data-testid={`rw-action-${task.code}`}
              >
                {task.claimed
                  ? t(locale, "rw_claimed")
                  : done
                    ? t(locale, "rw_claim")
                    : t(locale, "rw_go")}
              </button>
            </article>
          );
        })}
      </div>
    </div>
  );
}
