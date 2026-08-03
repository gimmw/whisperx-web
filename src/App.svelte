<script>
  import { Router, Link, Route } from "svelte-routing";
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
</style>
