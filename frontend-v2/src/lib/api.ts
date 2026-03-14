import { Client, type Thread, type ThreadState } from "@langchain/langgraph-sdk";

export type AgentThreadState = {
  title?: string;
  messages?: Array<Record<string, unknown>>;
  artifacts?: string[];
  todos?: Array<Record<string, unknown>>;
};

export type AgentThread = Thread<AgentThreadState>;
export type AgentThreadSnapshot = ThreadState<AgentThreadState>;

export type ModelDescriptor = {
  name: string;
  provider?: string;
  label?: string;
};

export type ChatContext = {
  mode: "balance" | "pro";
  modelName?: string;
  threadId?: string;
};

const langGraphClient = new Client<AgentThreadState>({
  apiUrl: getLangGraphBaseUrl(),
});

export function getBackendBaseUrl() {
  return import.meta.env.VITE_BACKEND_BASE_URL || "";
}

export function getLangGraphBaseUrl() {
  return import.meta.env.VITE_LANGGRAPH_BASE_URL || `${window.location.origin}/api/langgraph`;
}

export async function loadModels() {
  const res = await fetch(`${getBackendBaseUrl()}/api/models`);
  if (!res.ok) {
    throw new Error(`Failed to load models (${res.status})`);
  }
  const payload = (await res.json()) as { models?: ModelDescriptor[] };
  return payload.models ?? [];
}

export async function searchThreads(limit: number, offset = 0) {
  return langGraphClient.threads.search<AgentThreadState>({
    limit,
    offset,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "status", "values"],
  }) as Promise<AgentThread[]>;
}

export async function loadThreadState(threadId: string) {
  return langGraphClient.threads.getState<AgentThreadState>(threadId, undefined, {
    subgraphs: true,
  });
}

export async function loadThreadSnapshot(threadId: string) {
  const state = await loadThreadState(threadId);
  if (hasThreadValues(state)) {
    return state;
  }

  const history = await langGraphClient.threads.getHistory<AgentThreadState>(threadId, {
    limit: 1,
  });
  return history[0] ?? state;
}

export async function createChatRun(input: {
  threadId?: string | null;
  message: string;
  context: ChatContext;
}) {
  const { message, context } = input;
  const threadId = input.threadId ?? (await langGraphClient.threads.create()).thread_id;
  const runContext: Record<string, unknown> = {
    mode: context.mode,
    thinking_enabled: context.mode !== "balance",
    is_plan_mode: context.mode === "pro",
    subagent_enabled: false,
    thread_id: threadId,
  };

  if (context.modelName) {
    runContext.model_name = context.modelName;
  }

  return langGraphClient.runs.create(threadId, "lead_agent", {
    input: {
      messages: [
        {
          type: "human",
          content: [
            {
              type: "text",
              text: message,
            },
          ],
        },
      ],
    },
    config: {
      recursion_limit: 1000,
    },
    context: runContext,
    onDisconnect: "continue",
    streamMode: ["updates", "messages", "custom"],
    streamResumable: true,
  });
}

export async function loadLatestRun(threadId: string) {
  const runs = await langGraphClient.runs.list(threadId, {
    limit: 1,
    offset: 0,
  });
  return runs[0] ?? null;
}

export async function cancelRun(threadId: string, runId: string) {
  await langGraphClient.runs.cancel(threadId, runId, false, "interrupt");
}

function hasThreadValues(state: AgentThreadSnapshot | null | undefined) {
  if (!state || !state.values || typeof state.values !== "object") {
    return false;
  }

  return Object.keys(state.values).length > 0;
}
