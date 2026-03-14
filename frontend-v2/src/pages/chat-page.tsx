import { useNavigate, useParams } from "@solidjs/router";
import {
  Match,
  Show,
  Switch,
  createEffect,
  createMemo,
  createResource,
  createSignal,
} from "solid-js";

import { ChatComposer } from "../components/chat-composer";
import { MessageTimeline } from "../components/message-timeline";
import {
  cancelRun,
  createChatRun,
  loadLatestRun,
  loadModels,
  loadThreadState,
} from "../lib/api";
import { mapMessages } from "../lib/messages";

const MODEL_STORAGE_KEY = "deerflow.v2.model";
const MODE_STORAGE_KEY = "deerflow.v2.mode";

export const ChatPage = () => {
  const params = useParams();
  const navigate = useNavigate();
  const currentThreadId = createMemo(() =>
    params.threadId && params.threadId !== "new" ? params.threadId : undefined,
  );

  const [selectedModel, setSelectedModel] = createSignal(loadStoredValue(MODEL_STORAGE_KEY));
  const [mode, setMode] = createSignal<"balance" | "pro">(loadStoredMode());
  const [isSending, setIsSending] = createSignal(false);
  const [sendError, setSendError] = createSignal<string | null>(null);
  const [runState, setRunState] = createSignal<{ runId?: string; isRunning: boolean }>({
    isRunning: false,
  });

  const [models] = createResource(loadModels);
  const [threadState, { refetch }] = createResource(currentThreadId, loadThreadState);

  createEffect(() => {
    persistValue(MODEL_STORAGE_KEY, selectedModel());
  });

  createEffect(() => {
    persistValue(MODE_STORAGE_KEY, mode());
  });

  createEffect(() => {
    const threadId = currentThreadId();
    if (!threadId) {
      setRunState({ isRunning: false });
      return;
    }

    let cancelled = false;
    let timer: number | undefined;

    const refresh = async () => {
      try {
        const latestRun = await loadLatestRun(threadId);
        if (cancelled) {
          return;
        }

        const isRunning =
          latestRun?.status === "pending" ||
          latestRun?.status === "running";
        setRunState({ runId: latestRun?.run_id, isRunning });

        await refetch();

        if (!cancelled && isRunning) {
          timer = window.setTimeout(refresh, 1200);
        }
      } catch (error) {
        if (!cancelled) {
          console.error("Failed to refresh run state", error);
          timer = window.setTimeout(refresh, 2000);
        }
      }
    };

    void refresh();

    return () => {
      cancelled = true;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  });

  const timeline = createMemo(() => mapMessages(threadState()?.values.messages));
  const threadTitle = createMemo(
    () =>
      threadState()?.values.title ||
      (currentThreadId() ? "Untitled conversation" : "New conversation"),
  );

  const submitMessage = async (message: string) => {
    setIsSending(true);
    setSendError(null);

    try {
      const run = await createChatRun({
        threadId: currentThreadId(),
        message,
        context: {
          mode: mode(),
          modelName: selectedModel() || undefined,
          threadId: currentThreadId(),
        },
      });

      setRunState({ runId: run.run_id, isRunning: true });

      if (!currentThreadId()) {
        await navigate(`/chats/${run.thread_id}`, { replace: true });
      } else {
        await refetch();
      }
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Failed to send message.");
    } finally {
      setIsSending(false);
    }
  };

  const stopRun = async () => {
    const threadId = currentThreadId();
    const runId = runState().runId;
    if (!threadId || !runId) {
      return;
    }
    await cancelRun(threadId, runId);
    setRunState({ runId, isRunning: false });
    await refetch();
  };

  return (
    <section class="panel panel-content">
      <div class="panel-toolbar">
        <div>
          <h1>{threadTitle()}</h1>
          <p class="muted">
            {currentThreadId()
              ? currentThreadId()
              : "A new thread will be created the moment you send the first message."}
          </p>
        </div>

        <div class="meta-badges">
          <span class="meta-badge">Mode: {mode()}</span>
          <span class="meta-badge">
            Model: {selectedModel() || "backend default"}
          </span>
          <span class="meta-badge">
            Status: {runState().isRunning ? "running" : "idle"}
          </span>
        </div>

        <div class="thread-actions">
          <Show when={runState().isRunning}>
            <button class="button-danger" onClick={() => void stopRun()}>
              Interrupt run
            </button>
          </Show>
          <button class="button-ghost" onClick={() => void refetch()}>
            Refresh thread
          </button>
        </div>

        <Show when={sendError()}>
          <div class="status-line" style={{ color: "var(--danger)" }}>
            {sendError()}
          </div>
        </Show>
      </div>

      <Switch>
        <Match when={threadState.loading && !threadState()}>
          <div class="empty-state">
            <div class="empty-card">Loading thread state...</div>
          </div>
        </Match>
        <Match when={threadState.error}>
          <div class="empty-state">
            <div class="empty-card">
              Failed to load thread:{" "}
              {threadState.error instanceof Error
                ? threadState.error.message
                : String(threadState.error)}
            </div>
          </div>
        </Match>
        <Match when={timeline().length > 0}>
          <MessageTimeline messages={timeline()} isRunning={runState().isRunning} />
        </Match>
        <Match when={!timeline().length}>
          <div class="empty-state">
            <div class="empty-card">
              <span class="split-note">Phase 1 chat flow</span>
              <h1>Start a new conversation.</h1>
              <p class="muted">
                This first version is intentionally compact: thread list on the
                left, one conversation surface on the right, background-run
                awareness, and no heavyweight landing UI.
              </p>
            </div>
          </div>
        </Match>
      </Switch>

      <ChatComposer
        modelName={selectedModel() || undefined}
        mode={mode()}
        models={models() ?? []}
        isSending={isSending()}
        onModeChange={setMode}
        onModelChange={setSelectedModel}
        onSubmit={submitMessage}
      />
    </section>
  );
};

function loadStoredValue(key: string) {
  if (typeof window === "undefined") {
    return "";
  }

  return window.localStorage.getItem(key) ?? "";
}

function loadStoredMode() {
  const value = loadStoredValue(MODE_STORAGE_KEY);
  return value === "pro" ? "pro" : "balance";
}

function persistValue(key: string, value: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, value);
}
