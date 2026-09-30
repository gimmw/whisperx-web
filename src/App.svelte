<script>
  import { Router, Link, Route } from "svelte-routing";
  import Icon from "@iconify/svelte";
  import Home from "./lib/Home.svelte";
  import Progress from "./lib/Progress.svelte";
  import { TITLE, HELP_URL } from "./lib/config";

  export let url = "";
</script>

<Router {url}>
  <div>
    {#if TITLE}
      <h1 class="site-title">{TITLE}</h1>
    {/if}
    <div class="header-links">
      {#if HELP_URL}
        <a class="header-link" href={HELP_URL} target="_blank" rel="noopener noreferrer" aria-label="Help" title="Help">
          <Icon icon="mdi:help-circle-outline" width="28" height="28" />
        </a>
      {/if}
      <a class="header-link" href="https://github.com/gimmw/whisperx-web" target="_blank" rel="noopener noreferrer" aria-label="GitHub repository" title="source code repository on GitHub">
        <Icon icon="mdi:github" width="28" height="28" />
      </a>
    </div>
    <Route path="/:id" component={Progress} />
    <Route path="/"><Home /></Route>
  </div>
</Router>

<style>
  .site-title {
    position: fixed;
    top: 1.5rem;
    left: 50%;
    transform: translateX(-50%);
    z-index: 120;
    margin: 0;
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--c-text);
    pointer-events: none;
  }

  .header-links {
    position: fixed;
    top: 1.5rem;
    right: 1.5rem;
    z-index: 120;
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .header-link {
    display: inline-flex;
    color: var(--c-text-muted);
    transition: color 0.2s ease;
  }

  .header-link:hover {
    color: var(--c-text-strong);
  }
</style>
