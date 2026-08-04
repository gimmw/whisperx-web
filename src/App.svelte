<script>
  import { Router, Link, Route } from "svelte-routing";
  import Icon from "@iconify/svelte";
  import Home from "./lib/Home.svelte";
  import Progress from "./lib/Progress.svelte";
  import { TITLE } from "./lib/config";

  export let url = "";
</script>

<Router {url}>
  <div>
    {#if TITLE}
      <h1 class="site-title">{TITLE}</h1>
    {/if}
    <a class="github-link" href="https://github.com/gimmw/whisperx-web" target="_blank" rel="noopener noreferrer" aria-label="GitHub repository">
      <Icon icon="mdi:github" width="28" height="28" />
    </a>
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
    color: rgba(255, 255, 255, 0.87);
    pointer-events: none;
  }

  .github-link {
    position: fixed;
    top: 1.5rem;
    right: 1.5rem;
    z-index: 120;
    color: rgba(255, 255, 255, 0.6);
    transition: color 0.2s ease;
  }

  .github-link:hover {
    color: rgba(255, 255, 255, 0.95);
  }
</style>
