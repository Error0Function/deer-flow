import { For, createSignal } from "solid-js";

type ChatComposerProps = {
  modelName?: string;
  mode: "balance" | "pro";
  models: Array<{ name: string; provider?: string }>;
  isSending: boolean;
  onModeChange: (mode: "balance" | "pro") => void;
  onModelChange: (name: string) => void;
  onSubmit: (message: string) => Promise<void>;
};

export const ChatComposer = (props: ChatComposerProps) => {
  const [draft, setDraft] = createSignal("");

  const submit = async () => {
    const nextMessage = draft().trim();
    if (!nextMessage) {
      return;
    }

    await props.onSubmit(nextMessage);
    setDraft("");
  };

  return (
    <div class="composer">
      <textarea
        value={draft()}
        onInput={(event) => setDraft(event.currentTarget.value)}
        placeholder="Ask DeerFlow to inspect code, run a sandbox action, or continue a thread."
      />

      <div class="composer-actions">
        <select
          class="select"
          value={props.modelName ?? ""}
          onChange={(event) => props.onModelChange(event.currentTarget.value)}
        >
          <option value="">Use backend default model</option>
          <For each={props.models}>
            {(model) => (
              <option value={model.name}>
                {model.provider ? `${model.provider} / ${model.name}` : model.name}
              </option>
            )}
          </For>
        </select>

        <select
          class="select"
          value={props.mode}
          onChange={(event) =>
            props.onModeChange(event.currentTarget.value === "pro" ? "pro" : "balance")
          }
        >
          <option value="balance">Balance mode</option>
          <option value="pro">Pro mode</option>
        </select>

        <button class="button" disabled={props.isSending} onClick={() => void submit()}>
          {props.isSending ? "Sending..." : "Send"}
        </button>
      </div>
    </div>
  );
};
