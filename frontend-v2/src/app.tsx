import { Navigate, Route, Router, useLocation } from "@solidjs/router";
import type { ParentComponent } from "solid-js";
import { Show } from "solid-js";

import { ThreadSidebar } from "./components/thread-sidebar";
import { ChatPage } from "./pages/chat-page";
import { HomePage } from "./pages/home-page";

const Shell: ParentComponent = (props) => {
  const location = useLocation();

  return (
    <div class="shell">
      <ThreadSidebar currentPath={location.pathname} />
      <main class="workspace">
        <header class="workspace-header">
          <div>
            <h2>DeerFlow Workspace v2</h2>
            <p class="muted">
              Solid-first experiment. Keep the legacy UI at `/` and compare it
              against this new workspace under `/v2/`.
            </p>
          </div>
          <div class="toolbar-row">
            <a class="button-ghost" href="/">
              Open legacy UI
            </a>
            <Show when={location.pathname !== "/chats/new"}>
              <a class="button" href="/v2/chats/new">
                New conversation
              </a>
            </Show>
          </div>
        </header>
        <div class="workspace-body">{props.children}</div>
      </main>
    </div>
  );
};

export const App = () => {
  return (
    <Router base="/v2" root={Shell}>
      <Route path="/" component={HomePage} />
      <Route path="/chats">
        <Route path="/" component={() => <Navigate href="/chats/new" />} />
        <Route path="/new" component={ChatPage} />
        <Route path="/:threadId" component={ChatPage} />
      </Route>
    </Router>
  );
};
