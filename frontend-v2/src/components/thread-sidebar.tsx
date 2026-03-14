import { A } from "@solidjs/router";
import { For, Show, createEffect, createSignal } from "solid-js";

import { type AgentThread, searchThreads } from "../lib/api";

type ThreadSidebarProps = {
  currentPath: string;
};

export const ThreadSidebar = (props: ThreadSidebarProps) => {
  const [threads, setThreads] = createSignal<AgentThread[]>([]);
  const [hasMore, setHasMore] = createSignal(true);
  const [isLoading, setIsLoading] = createSignal(true);
  const [isLoadingMore, setIsLoadingMore] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  const loadPage = async (reset = false) => {
    try {
      setError(null);
      if (reset) {
        setIsLoading(true);
      } else {
        setIsLoadingMore(true);
      }

      const offset = reset ? 0 : threads().length;
      const nextThreads = await searchThreads(24, offset);

      setThreads((current) => (reset ? nextThreads : [...current, ...nextThreads]));
      setHasMore(nextThreads.length === 24);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load threads.");
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  };

  createEffect(() => {
    props.currentPath;
    void loadPage(true);
  });

  return (
    <aside class="sidebar">
      <div class="sidebar-header">
        <span class="brand-mark">
          <strong>DF v2</strong>
          <span>Solid workspace experiment</span>
        </span>
        <div class="sidebar-copy">
          <h1>Fast path chat surface.</h1>
          <p>
            Keep the legacy app alive at `/`, and use this shell to validate a
            leaner data flow before we rebuild advanced features.
          </p>
        </div>
        <div class="sidebar-actions">
          <A class="button" href="/chats/new">
            Start in v2
          </A>
          <a class="button-ghost" href="/">
            Compare legacy
          </a>
        </div>
      </div>

      <section class="thread-list">
        <div class="toolbar-row">
          <span class="split-note">Recent threads</span>
          <button class="button-ghost" onClick={() => void loadPage(true)}>
            Refresh
          </button>
        </div>

        <div class="thread-scroll">
          <Show when={!isLoading()} fallback={<SidebarNotice text="Loading threads..." />}>
            <Show when={!error()} fallback={<SidebarNotice text={error() ?? ""} />}>
              <For each={threads()}>
                {(thread) => (
                  <A
                    class="thread-card"
                    href={`/chats/${thread.thread_id}`}
                    data-active={props.currentPath === `/chats/${thread.thread_id}`}
                  >
                    <h3>{thread.values?.title || "Untitled conversation"}</h3>
                    <div class="meta">{thread.thread_id}</div>
                    <time>{formatDate(thread.updated_at)}</time>
                  </A>
                )}
              </For>
            </Show>
          </Show>

          <Show when={hasMore() && !isLoading()}>
            <button
              class="button-ghost"
              disabled={isLoadingMore()}
              onClick={() => void loadPage(false)}
            >
              {isLoadingMore() ? "Loading more..." : "Load more"}
            </button>
          </Show>
        </div>
      </section>
    </aside>
  );
};

const SidebarNotice = (props: { text: string }) => {
  return <div class="thread-card muted">{props.text}</div>;
};

function formatDate(value?: string | null) {
  if (!value) {
    return "No activity yet";
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
