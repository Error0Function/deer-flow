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

export async function createChatRun(input: {
  threadId?: string | null;
  message: string;
  context: ChatContext;
}) {
  const { threadId, message, context } = input;
  return langGraphClient.runs.create(threadId ?? null, "lead_agent", {
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
    context: {
      mode: context.mode,
      model_name: context.modelName,
      thinking_enabled: context.mode !== "balance",
      is_plan_mode: context.mode === "pro",
      subagent_enabled: false,
      thread_id: context.threadId,
    },
    onDisconnect: "continue",
    streamMode: ["updates", "messages", "custom"],
    streamResumable: true,
  });
}

export async function loadLatestRun(threadId: string) {
  const runs = await langGraphClient.runs.list(threadId, {
    limit: 1,
    offset: 0,
    select: ["run_id", "status", "updated_at", "created_at"],
  });
  return runs[0] ?? null;
}

export async function cancelRun(threadId: string, runId: string) {
  await langGraphClient.runs.cancel(threadId, runId, false, "interrupt");
}
