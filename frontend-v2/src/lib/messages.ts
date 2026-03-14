export type TimelineMessage = {
  id: string;
  role: "human" | "ai" | "system";
  label: string;
  body: string;
  parts: Array<{ type: string; body: string }>;
};

export type RawAgentMessage = Record<string, unknown>;

export function mapMessages(messages: RawAgentMessage[] | undefined): TimelineMessage[] {
  return (messages ?? []).map((message, index) => {
    const role = normalizeRole(String(message.type ?? "system"));
    return {
      id: String(message.id ?? `${role}-${index}`),
      role,
      label: role === "human" ? "User" : role === "ai" ? "Assistant" : "System",
      body: stringifyMessageContent(message.content),
      parts: extractParts(message.content),
    };
  });
}

export function createOptimisticHumanMessage(text: string, id: string): RawAgentMessage {
  return {
    id,
    type: "human",
    content: [
      {
        type: "text",
        text,
      },
    ],
  };
}

export function messageContainsText(
  message: RawAgentMessage | undefined,
  expectedText: string,
) {
  if (!message) {
    return false;
  }

  return stringifyMessageContent(message.content).trim() === expectedText.trim();
}

function normalizeRole(role: string): TimelineMessage["role"] {
  if (role === "human") {
    return "human";
  }
  if (role === "ai") {
    return "ai";
  }
  return "system";
}

function stringifyMessageContent(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") {
          return part;
        }
        if (part && typeof part === "object" && "text" in part) {
          return String(part.text ?? "");
        }
        return "";
      })
      .filter(Boolean)
      .join("\n\n")
      .trim();
  }

  return "";
}

function extractParts(content: unknown) {
  if (!Array.isArray(content)) {
    return [];
  }

  return content.flatMap((part, index) => {
    if (!part || typeof part !== "object") {
      return [];
    }

    const partType =
      "type" in part && typeof part.type === "string" ? part.type : `part-${index}`;
    const partBody =
      "text" in part && typeof part.text === "string"
        ? part.text
        : JSON.stringify(part, null, 2);

    return [{ type: partType, body: partBody }];
  });
}
