import { A } from "@solidjs/router";

export const HomePage = () => {
  return (
    <div class="empty-state">
      <section class="empty-card">
        <span class="split-note">Phase 1 target</span>
        <div class="panel-hero" style={{ padding: "0", border: "0", background: "transparent" }}>
          <h1>Rebuild the chat workspace before everything else.</h1>
          <p>
            This first Solid milestone is intentionally narrow: thread list,
            conversation view, message submission, live run awareness, and a
            cleaner shell that can evolve without dragging the old frontend along.
          </p>
        </div>

        <ul>
          <li>Keep the legacy UI alive at `/` for direct comparison.</li>
          <li>Keep DeerFlow APIs unchanged while we replace the client shell.</li>
          <li>Design for later checkpoints, branches, and multi-account boundaries.</li>
        </ul>

        <div class="panel-actions">
          <A class="button" href="/chats/new">
            Open the new workspace
          </A>
          <a class="button-ghost" href="/">
            Review the legacy frontend
          </a>
        </div>
      </section>
    </div>
  );
};
