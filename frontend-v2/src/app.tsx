import { Navigate, Route, Router, useLocation } from "@solidjs/router";
import type { ParentComponent } from "solid-js";
import { Show } from "solid-js";

import { ThreadSidebar } from "./components/thread-sidebar";
import { APP_BASE_PATH, withAppBasePath } from "./lib/base-path";
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
              against this new workspace under <code>{`${APP_BASE_PATH}/`}</code>.
              The dev route hot-reloads; the release route stays stable until the next rebuild.
            </p>
          </div>
          <div class="toolbar-row">
            <a class="button-ghost" href="/">
              Open legacy UI
            </a>
            <Show when={location.pathname !== "/chats/new"}>
              <a class="button" href={withAppBasePath("/chats/new")}>
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
    <Router base={APP_BASE_PATH} root={Shell}>
      <Route path="/" component={HomePage} />
      <Route path="/chats">
        <Route path="/" component={() => <Navigate href="/chats/new" />} />
        <Route path="/new" component={ChatPage} />
        <Route path="/:threadId" component={ChatPage} />
      </Route>
    </Router>
  );
};
