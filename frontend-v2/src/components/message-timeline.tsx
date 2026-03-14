import { For, Show, createMemo, createSignal } from "solid-js";

import type { TimelineMessage } from "../lib/messages";

type MessageTimelineProps = {
  messages: TimelineMessage[];
  isRunning: boolean;
};

export const MessageTimeline = (props: MessageTimelineProps) => {
  const [expanded, setExpanded] = createSignal(false);
  const visibleMessages = createMemo(() => {
    if (expanded() || props.messages.length <= 12) {
      return props.messages;
    }
    return props.messages.slice(-12);
  });

  const hiddenCount = createMemo(() => props.messages.length - visibleMessages().length);

  return (
    <div class="messages">
      <Show when={hiddenCount() > 0}>
        <button class="button-ghost" onClick={() => setExpanded(true)}>
          Show {hiddenCount()} earlier messages
        </button>
      </Show>

      <For each={visibleMessages()}>
        {(message) => (
          <article class="message" data-role={message.role}>
            <header class="message-head">
              <span class="message-role">{message.label}</span>
            </header>
            <Show when={message.body}>
              <div class="message-body">{message.body}</div>
            </Show>
            <Show when={message.parts.length > 1}>
              <div class="message-parts">
                <For each={message.parts}>
                  {(part) => (
                    <section class="message-part">
                      <div class="meta">{part.type}</div>
                      <pre>{part.body}</pre>
                    </section>
                  )}
                </For>
              </div>
            </Show>
          </article>
        )}
      </For>

      <Show when={props.isRunning}>
        <article class="message" data-role="system">
          <header class="message-head">
            <span class="message-role">Run in progress</span>
          </header>
          <div class="message-body">
            DeerFlow is still working on this thread. You can leave this page and
            return later; the run will continue on the backend.
          </div>
        </article>
      </Show>
    </div>
  );
};
