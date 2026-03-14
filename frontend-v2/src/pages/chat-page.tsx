import { useLocation, useNavigate, useParams } from "@solidjs/router";
import {
  Match,
  Show,
  Switch,
  createEffect,
  createMemo,
  createResource,
  createSignal,
  onCleanup,
} from "solid-js";

import { ChatComposer } from "../components/chat-composer";
import { withAppBasePath } from "../lib/base-path";
import { MessageTimeline } from "../components/message-timeline";
import {
  cancelRun,
  createChatRun,
  loadLatestRun,
  loadModels,
  loadThreadSnapshot,
} from "../lib/api";
import {
  createOptimisticHumanMessage,
  mapMessages,
  messageContainsText,
type RawAgentMessage,
} from "../lib/messages";

const MODEL_STORAGE_KEY = "deerflow.v2.model";
const MODE_STORAGE_KEY = "deerflow.v2.mode";
const PENDING_MESSAGE_STORAGE_KEY = "deerflow.v2.pending-message";

export const ChatPage = () => {
  const params = useParams();
  const navigate = useNavigate();
  const location = useLocation();
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
  const [pollGeneration, setPollGeneration] = createSignal(0);
  const [optimisticMessage, setOptimisticMessage] = createSignal<{
    id: string;
    text: string;
    threadId: string;
  } | null>(null);

  const [models] = createResource(loadModels);
  const [threadState, { refetch }] = createResource(currentThreadId, loadThreadSnapshot);
  const effectiveModel = createMemo(() => {
    const currentModel = selectedModel();
    if (!currentModel) {
      return "";
    }

    const availableModels = models();
    if (!availableModels?.length) {
      return currentModel;
    }

    return availableModels.some((model) => model.name === currentModel) ? currentModel : "";
  });

  createEffect(() => {
    const availableModels = models();
    if (!availableModels?.length) {
      return;
    }

    const currentModel = selectedModel();
    const resolvedModel = effectiveModel();
    if (!currentModel || resolvedModel === currentModel) {
      return;
    }

    setSelectedModel("");
    setSendError(
      `Saved model "${currentModel}" is no longer available. Reverted to the backend default model.`,
    );
  });

  createEffect(() => {
    persistValue(MODEL_STORAGE_KEY, selectedModel());
  });

  createEffect(() => {
    persistValue(MODE_STORAGE_KEY, mode());
  });

  createEffect(() => {
    const threadId = currentThreadId();
    if (!threadId) {
      setOptimisticMessage(null);
      return;
    }

    const currentPending = optimisticMessage();
    if (currentPending?.threadId === threadId) {
      return;
    }

    const routePendingText =
      typeof location.query.pending === "string" ? location.query.pending : undefined;
    if (routePendingText) {
      setOptimisticMessage({
        id: `pending-${threadId}`,
        text: routePendingText,
        threadId,
      });
      return;
    }

    const pending = loadPendingMessage(threadId);
    setOptimisticMessage(pending);
  });

  createEffect(() => {
    const pending = optimisticMessage();
    const threadId = currentThreadId();
    const messages = threadState()?.values.messages;
    if (!pending || !threadId || pending.threadId !== threadId || !messages?.length) {
      return;
    }

    const wasPersisted = messages.some((message) =>
      messageContainsText(message as RawAgentMessage, pending.text),
    );
    if (wasPersisted) {
      clearPendingMessage(threadId);
      if (typeof location.query.pending === "string") {
        window.history.replaceState(window.history.state, "", withAppBasePath(`/chats/${threadId}`));
      }
      setOptimisticMessage(null);
    }
  });

  createEffect(() => {
    const threadId = currentThreadId();
    pollGeneration();
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

        if (latestRun?.status === "error") {
          setSendError(describeRunFailure(latestRun));
        }

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

    onCleanup(() => {
      cancelled = true;
      if (timer) {
        window.clearTimeout(timer);
      }
    });
  });

  const timeline = createMemo(() => {
    const messages = [...(threadState()?.values.messages ?? [])];
    const pending = optimisticMessage();
    if (pending && pending.threadId === currentThreadId()) {
      messages.push(createOptimisticHumanMessage(pending.text, pending.id));
    }
    return mapMessages(messages);
  });
  const threadTitle = createMemo(
    () =>
      threadState()?.values.title ||
      (currentThreadId() ? "Untitled conversation" : "New conversation"),
  );

  const submitMessage = async (message: string) => {
    setIsSending(true);
    setSendError(null);

    try {
      const optimisticId = `optimistic-${Date.now()}`;
      const run = await createChatRun({
        threadId: currentThreadId(),
        message,
        context: {
          mode: mode(),
          modelName: effectiveModel() || undefined,
          threadId: currentThreadId(),
        },
      });

      setOptimisticMessage({
        id: optimisticId,
        text: message,
        threadId: run.thread_id,
      });
      savePendingMessage({
        id: optimisticId,
        text: message,
        threadId: run.thread_id,
      });
      setRunState({ runId: run.run_id, isRunning: true });
      setPollGeneration((value) => value + 1);

      if (!currentThreadId()) {
        await navigate(
          `/chats/${run.thread_id}?pending=${encodeURIComponent(message)}`,
          { replace: true },
        );
      } else {
        await refetch();
      }
      return true;
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Failed to send message.");
      return false;
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
            Model: {effectiveModel() || "backend default"}
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
        <Match when={timeline().length > 0}>
          <MessageTimeline messages={timeline()} isRunning={runState().isRunning} />
        </Match>
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
        modelName={effectiveModel() || undefined}
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

function loadPendingMessage(threadId: string) {
  if (typeof window === "undefined") {
    return null;
  }

  const rawValue = window.sessionStorage.getItem(PENDING_MESSAGE_STORAGE_KEY);
  if (!rawValue) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawValue) as {
      id?: string;
      text?: string;
      threadId?: string;
    };
    if (
      parsed.threadId === threadId &&
      typeof parsed.id === "string" &&
      typeof parsed.text === "string"
    ) {
      return {
        id: parsed.id,
        text: parsed.text,
        threadId: parsed.threadId,
      };
    }
  } catch {
    window.sessionStorage.removeItem(PENDING_MESSAGE_STORAGE_KEY);
  }

  return null;
}

function savePendingMessage(value: { id: string; text: string; threadId: string }) {
  if (typeof window === "undefined") {
    return;
  }

  window.sessionStorage.setItem(PENDING_MESSAGE_STORAGE_KEY, JSON.stringify(value));
}

function clearPendingMessage(threadId: string) {
  if (typeof window === "undefined") {
    return;
  }

  const pending = loadPendingMessage(threadId);
  if (pending) {
    window.sessionStorage.removeItem(PENDING_MESSAGE_STORAGE_KEY);
  }
}

function describeRunFailure(run: unknown) {
  const modelName = extractRunModelName(run);
  if (modelName) {
    return `The latest run failed while using model "${modelName}". Pick an available model or fall back to the backend default, then retry.`;
  }

  return "The latest run failed before a reply was saved. Retry the message or switch to the backend default model.";
}

function extractRunModelName(run: unknown) {
  const record = readRecord(run);
  const kwargs = readRecord(record?.kwargs);
  const config = readRecord(kwargs?.config);
  const configurable = readRecord(config?.configurable);
  const context = readRecord(kwargs?.context);

  const candidate =
    configurable?.model_name ??
    configurable?.modelName ??
    context?.model_name ??
    context?.modelName;

  return typeof candidate === "string" && candidate ? candidate : null;
}

function readRecord(value: unknown) {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}
