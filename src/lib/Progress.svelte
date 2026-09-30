<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { Link } from 'svelte-routing';
    import Icon from '@iconify/svelte';
    import {HOST} from "./config";
    import moment from 'moment';

    interface Metrics {
        cpu_cores_used: number | null,
        cpu_limit_cores: number | null,
        cpu_util: number | null,
        gpu_util: number | null,
        gpu_mem_util: number | null,
        gpu_mem_used_mb: number | null,
        gpu_mem_total_mb: number | null,
        gpu_temp_c: number | null,
        gpu_name: string | null,
    }

    interface Chunk {
        timestamp: [number, number],
        text: string,
        speaker?: string
    }

    interface SpeakerBlock {
        speaker: string | null,
        startTime: number,
        chunks: Chunk[]
    }

    export let id: string;
    let progress = '';
    let isDone = false;
    let state: 'queued' | 'processing' | 'error' | 'unknown' | '' = '';
    let queuePosition = 0;
    let elapsed = 0;
    let metrics: Metrics | null = null;
    let pollError = '';
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let result: {
        output: {
            text: string
            chunks: Chunk[]
        },
        elapsed: number[]
        elapsedStr: string
    }
    let blocks: SpeakerBlock[] = [];
    let hasSpeakers = false;
    let optTimestamps = localStorage.getItem('optTimestamps') === 'true';

    function groupChunks(chunks: Chunk[]): SpeakerBlock[] {
        const groups: SpeakerBlock[] = [];
        for (const chunk of chunks) {
            const speaker = chunk.speaker ?? null;
            const last = groups[groups.length - 1];
            if (last && last.speaker === speaker) {
                last.chunks.push(chunk);
            } else {
                groups.push({
                    speaker,
                    startTime: chunk.timestamp[0],
                    chunks: [chunk],
                });
            }
        }
        return groups;
    }

    function blockText(block: SpeakerBlock): string {
        return block.chunks.map(c => c.text.trim()).filter(t => t).join(" ");
    }

    onMount(() => {
        checkProgress();
    });

    onDestroy(() => {
        cancelled = true;
        if (timer !== null) clearTimeout(timer);
    });

    function pct(v: number | null | undefined): string {
        return v == null ? '--' : `${Math.round(v * 100)}%`;
    }

    async function checkProgress() {
        if (cancelled) return;

        let data: any;
        try {
            const response = await fetch(`${HOST}/progress/${id}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            data = await response.json();
            pollError = '';
        } catch (e) {
            // Keep polling instead of silently dying on a transient failure
            // (pod restart, rollout, brief network blip).
            pollError = e instanceof Error ? e.message : String(e);
            if (!cancelled) timer = setTimeout(checkProgress, 2000);
            return;
        }

        if (data.done) {
            isDone = true;
            const tmp = (await fetch(`${HOST}/result/${id}.json`).then(res => res.json()));
            if (typeof tmp.elapsed === 'number') {
                tmp.elapsed = [result.elapsed, 0];
            }
            result = tmp;

            for (const chunk of result.output.chunks)
                chunk.speaker = chunk.speaker?.replace("SPEAKER_0", "")

            hasSpeakers = result.output.chunks.some(c => c.speaker != null);
            blocks = groupChunks(result.output.chunks);
            console.log(result)
            await downloadResults();
        } else {
            state = data.state ?? '';
            queuePosition = data.queue_position ?? 0;
            elapsed = data.elapsed ?? 0;
            metrics = data.metrics ?? null;
            progress = data.status ?? '';

            // "unknown" is terminal: the server has no record of this id
            // (deleted, expired, or lost to a restart), and nothing it can do
            // will produce one, so polling would only spin forever. Every
            // other state can still change, so keep polling those.
            if (state === 'unknown') return;

            if (!cancelled) timer = setTimeout(checkProgress, 1000);
        }
    }

    async function downloadResults() {
        let txt = "";

        for (const block of blocks) {
            let blockStart = new Date(block.startTime * 1000).toISOString().substring(11, 19);

            if (hasSpeakers) {
                txt += `\n[Speaker ${block.speaker} - ${blockStart}]\n`;
            } else {
                txt += "\n";
            }

            if (optTimestamps) {
                for (const c of block.chunks) {
                    let start = new Date(c.timestamp[0] * 1000).toISOString().substring(11, 19);
                    txt += `${start}: ${c.text.trim()}\n`;
                }
            } else {
                txt += blockText(block) + "\n";
            }
        }

        download(txt, `${id}.txt`, 'text/plain');
    }

    function download(content: string, fileName: string, contentType: string) {
        const a = document.createElement('a');
        const file = new Blob([content], { type: contentType });
        a.href = URL.createObjectURL(file);
        a.download = fileName;
        a.click();
    }

    function changeTimestamps() {
        localStorage.setItem('optTimestamps', optTimestamps.toString())
        downloadResults()
    }

    // ---------------------------------------------------------------------
    // Delete on request.
    //
    // The transcript is otherwise kept until the operator's retention sweep
    // runs (see k8s/cleanup-cronjob.yaml), which can be days. This gives the
    // user the same outcome immediately.
    //
    // Confirmation is a real dialog rather than window.confirm(): confirm()
    // blocks the event loop, which would freeze the poll timer, and its
    // wording cannot say what is actually being destroyed or that it cannot be
    // undone.
    // ---------------------------------------------------------------------
    let confirmOpen = false;
    let deleting = false;
    let deleteError = '';
    let confirmEl: HTMLDivElement | null = null;
    let deleteBtnEl: HTMLButtonElement | null = null;

    function openConfirm() {
        deleteError = '';
        confirmOpen = true;
    }

    function closeConfirm() {
        // A delete in flight has already left the browser; letting the dialog
        // close would leave the user looking at a transcript that is being
        // removed underneath them.
        if (deleting) return;
        confirmOpen = false;
        // Without this, focus is dropped to the top of the document when the
        // dialog unmounts.
        deleteBtnEl?.focus();
    }

    function onConfirmKeydown(e: KeyboardEvent) {
        if (e.key === 'Escape') {
            e.stopPropagation();
            closeConfirm();
            return;
        }

        // Focus trap, same reasoning as the settings panel in Home.svelte: the
        // backdrop hides where focus has gone if Tab escapes behind it.
        if (e.key !== 'Tab' || !confirmEl) return;

        const focusable = Array.from(
            confirmEl.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
        ).filter(el => !el.hasAttribute('disabled'));
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
        }
    }

    // Focus the cancel button, not delete: the destructive action should never
    // be one stray Enter away.
    $: if (confirmOpen && confirmEl) {
        queueMicrotask(() => confirmEl?.querySelector<HTMLElement>('.cancel-btn')?.focus());
    }

    async function confirmDelete() {
        deleting = true;
        deleteError = '';

        let resp: Response;
        try {
            resp = await fetch(`${HOST}/transcript/${id}`, { method: 'DELETE' });
        } catch (e) {
            console.error('Delete failed', e);
            deleteError = 'Delete failed. Please check your connection and try again.';
            deleting = false;
            return;
        }

        // 404 means it is already gone -- a double submit, or the retention
        // sweep got there first. The user asked for it to not exist, and it
        // does not, so treat that as success rather than an error they can do
        // nothing about.
        if (!resp.ok && resp.status !== 404) {
            let message = 'Could not delete the transcript. Please try again.';
            try {
                const body = await resp.json();
                if (body?.error) message = body.error;
            } catch {
                // Non-JSON body (a proxy error page); keep the default.
            }
            deleteError = message;
            deleting = false;
            return;
        }

        // Stop polling before leaving: the transcript is gone, so the next
        // /progress call would come back "unknown" and flip the page to "no
        // longer available" in the moment before the navigation commits.
        cancelled = true;
        if (timer !== null) clearTimeout(timer);

        // A full load rather than client-side navigation, matching how Home
        // sends the user here, and guaranteeing no deleted transcript is left
        // in component state.
        window.location.href = '/';
    }
</script>

<main>
    <nav class="back-nav">
        <Link to="/">
            <span class="back-link">
                <Icon icon="tabler:arrow-left" width="18" height="18" />
                New transcription
            </span>
        </Link>
    </nav>

    <h1>Transcription Progress</h1>
    {#if isDone && result}
        <p>Transcription complete ({result.elapsed[0].toFixed(1)}s + {result.elapsed[1].toFixed(1)}s). Your file will download shortly.</p>

        <!-- The delete control sits with the download option rather than below
             the transcript: a long transcript would put it several screens
             down, where a user who wants the text gone would have to scroll
             through all of it to get rid of it. -->
        <div class="result-actions">
            <label class="timestamps-toggle">
                <input type="checkbox" bind:checked={optTimestamps} on:change={changeTimestamps} />
                Download with timestamps
            </label>

            <button
                type="button"
                class="delete-btn"
                bind:this={deleteBtnEl}
                on:click={openConfirm}
                aria-haspopup="dialog"
                aria-expanded={confirmOpen}
            >
                <Icon icon="tabler:trash" width="18" height="18" />
                Delete transcript
            </button>
        </div>

        <div class="blocks">
            {#each blocks as block}
                <div class="block">
                    <div class="block-header">
                        {#if hasSpeakers}
                            <span class="speaker s{block.speaker}">{block.speaker}</span>
                        {/if}
                        <span class="time">{moment.utc(block.startTime * 1000).format("HH:mm:ss")}</span>
                    </div>
                    <p class="block-text">{blockText(block)}</p>
                </div>
            {/each}
        </div>
    {:else if state === 'unknown'}
        <p class="status-line">{progress || 'This transcript is no longer available.'}</p>
        <p class="unknown-hint">
            It may have been deleted, or removed by the server's retention policy.
            Transcripts cannot be recovered once they are gone.
        </p>
    {:else if state === 'error'}
        <p class="error-line">{progress || 'Transcription failed.'}</p>
        <p class="error-id">ID: <code>{id}</code></p>
    {:else if state === 'processing'}
        <p class="status-line">Processing &middot; {Math.round(elapsed)}s elapsed</p>

        <div class="metrics">
            <div class="metric">
                <span class="metric-label">CPU</span>
                {#if metrics?.cpu_cores_used != null}
                    <span class="metric-value">{metrics.cpu_cores_used.toFixed(2)} cores</span>
                    {#if metrics.cpu_limit_cores}
                        <span class="metric-sub">
                            of {metrics.cpu_limit_cores.toFixed(2)} ({pct(metrics.cpu_util)})
                        </span>
                    {/if}
                {:else}
                    <span class="metric-value unavailable">unavailable</span>
                {/if}
            </div>

            <div class="metric">
                <span class="metric-label">GPU</span>
                {#if metrics?.gpu_util != null}
                    <span class="metric-value">{pct(metrics.gpu_util)}</span>
                {:else}
                    <span class="metric-value unavailable">unavailable</span>
                {/if}
                {#if metrics?.gpu_mem_used_mb != null && metrics?.gpu_mem_total_mb != null}
                    <span class="metric-sub">
                        {(metrics.gpu_mem_used_mb / 1024).toFixed(1)} /
                        {(metrics.gpu_mem_total_mb / 1024).toFixed(1)} GiB VRAM
                    </span>
                {/if}
            </div>
        </div>

        {#if pollError}
            <p class="poll-error">Connection issue &mdash; retrying ({pollError})</p>
        {/if}
    {:else}
        <p class="status-line">
            {#if state === 'queued'}
                Queued &middot; {queuePosition} ahead of you
            {:else}
                {progress || 'Loading...'}
            {/if}
        </p>
        {#if pollError}
            <p class="poll-error">Connection issue &mdash; retrying ({pollError})</p>
        {/if}
    {/if}

    <!-- Confirmation for the destructive action. Mounted conditionally rather
         than kept hidden, so its controls are never in the tab order of the
         page behind it. -->
    {#if confirmOpen}
        <!-- Backdrop as a <button> so dismissal works without a pointer; the
             Cancel control in the dialog serves the same purpose, so this one
             is hidden from assistive tech to avoid announcing it twice. -->
        <button
            type="button"
            class="confirm-backdrop"
            on:click={closeConfirm}
            tabindex="-1"
            aria-hidden="true"
        ></button>

        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
        <div
            class="confirm-dialog"
            bind:this={confirmEl}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            aria-describedby="confirm-body"
            on:keydown={onConfirmKeydown}
        >
            <h2 class="confirm-title" id="confirm-title">Delete this transcript?</h2>
            <p class="confirm-body" id="confirm-body">
                The transcript is deleted from the server immediately and cannot be
                recovered. This link will stop working for anyone who has it. Any
                copy already downloaded to your device is unaffected.
            </p>

            <!-- Inside the dialog, not on the page behind it: the backdrop
                 covers the page, so an error rendered there is dimmed and
                 partly obscured at the moment the user most needs to read it.
                 Keeping it here also puts it next to the button they would
                 press to retry. -->
            {#if deleteError}
                <p class="confirm-error" role="alert">{deleteError}</p>
            {/if}

            <div class="confirm-actions">
                <button type="button" class="cancel-btn" on:click={closeConfirm} disabled={deleting}>
                    Cancel
                </button>
                <button type="button" class="destructive-btn" on:click={confirmDelete} disabled={deleting}>
                    {deleting ? 'Deleting…' : 'Delete'}
                </button>
            </div>
        </div>
    {/if}
</main>

<style lang="sass">
    .back-nav
      display: flex
      justify-content: flex-start
      margin-bottom: 0.5rem

      .back-link
        display: inline-flex
        align-items: center
        gap: 0.35rem
        font-size: 0.9rem
        color: var(--c-text-muted)
        transition: color 0.2s ease

        &:hover
          color: var(--c-text-strong)

    .status-line
      opacity: 0.85

    // Download option and delete, on one row where there is width for it.
    // Wraps rather than shrinking: "Download with timestamps" and "Delete
    // transcript" both become unreadable if truncated.
    .result-actions
      display: flex
      flex-wrap: wrap
      align-items: center
      justify-content: center
      gap: 0.75rem 1.25rem
      margin: 0.5rem 0 1.25rem

      .timestamps-toggle
        display: inline-flex
        align-items: center
        gap: 0.5rem
        cursor: pointer
        // 44px minimum touch target (WCAG 2.5.5 / iOS HIG), matching the
        // controls in Home.svelte.
        min-height: 44px

        input[type="checkbox"]
          width: 1.15rem
          height: 1.15rem
          cursor: pointer

    // Outlined rather than a solid red fill. This is a secondary action on a
    // page whose purpose is the transcript, so it should be unmistakably
    // destructive without competing with the content for attention; the solid
    // fill is saved for the confirm button, where destruction is the point of
    // the dialog.
    //
    // Colour is not the only signal: the label says "Delete" and the icon is a
    // bin, so the meaning survives for users who cannot distinguish red.
    .delete-btn
      display: inline-flex
      align-items: center
      gap: 0.4rem
      padding: 0.5rem 0.9rem
      min-height: 44px
      border-radius: 999px
      border: 1px solid var(--c-danger)
      background: transparent
      color: var(--c-danger)
      font-size: 0.9rem
      font-family: inherit
      cursor: pointer

      &:hover
        background: var(--c-danger-tint)
        border-color: var(--c-danger-hover)
        color: var(--c-danger-hover)

    .confirm-backdrop
      display: block
      position: fixed
      inset: 0
      z-index: 199
      // Reset the <button> defaults; this is a bare hit target.
      border: none
      padding: 0
      background: var(--c-backdrop)
      cursor: default

    .confirm-dialog
      position: fixed
      z-index: 200
      box-sizing: border-box
      left: 50%
      top: 50%
      // #{} so Sass emits calc() verbatim: it otherwise folds this into the
      // invalid "min(26rem, 100vw - 2rem)", which browsers drop entirely.
      // Same trap as the settings panel in Home.svelte.
      width: #{"min(26rem, calc(100vw - 2rem))"}
      transform: translate(-50%, -50%)
      text-align: left

      background: var(--c-panel-bg)
      border: 1px solid var(--c-panel-border)
      border-radius: 1rem
      padding: 1.25rem
      box-shadow: 0 10px 40px var(--c-shadow-lg)

      .confirm-title
        margin: 0 0 0.5rem
        font-size: 1.1rem
        font-weight: 600
        color: var(--c-text-strong)

      .confirm-body
        margin: 0 0 1.25rem
        font-size: 0.9rem
        line-height: 1.5
        color: var(--c-text-muted)

      .confirm-error
        margin: 0 0 1rem
        font-size: 0.85rem
        line-height: 1.4
        color: var(--c-error)

      .confirm-actions
        display: flex
        justify-content: flex-end
        gap: 0.75rem

        button
          min-height: 44px
          padding: 0.5rem 1.1rem
          border-radius: 0.5rem
          font-size: 0.95rem
          font-family: inherit
          cursor: pointer

          &:disabled
            opacity: 0.6
            cursor: default

        // Cancel is the resting focus target, so it is the plain one.
        .cancel-btn
          border: 1px solid var(--c-border)
          background: var(--c-surface-raised)
          color: var(--c-text)

          &:hover:not(:disabled)
            border-color: var(--c-text-strong)
            color: var(--c-text-strong)

        .destructive-btn
          border: 1px solid transparent
          background: var(--c-danger)
          color: var(--c-on-danger)
          font-weight: 600

          &:hover:not(:disabled)
            background: var(--c-danger-hover)

          &:focus-visible
            // The button is a solid fill, so the ring must contrast with that
            // fill rather than with the page.
            outline: 2px solid var(--c-text-strong)
            outline-offset: 2px

    .metrics
      display: flex
      justify-content: center
      gap: 2.5rem
      margin: 1rem 0

      .metric
        display: flex
        flex-direction: column
        align-items: center
        gap: 0.15rem

        .metric-label
          font-size: 0.75em
          text-transform: uppercase
          letter-spacing: 0.08em
          opacity: 0.5

        .metric-value
          font-family: monospace
          font-size: 1.1em

          &.unavailable
            opacity: 0.4
            font-size: 0.9em

        .metric-sub
          font-family: monospace
          font-size: 0.75em
          opacity: 0.5

    .poll-error
      font-size: 0.85em
      color: var(--c-error)
      opacity: 0.8

    .unknown-hint
      font-size: 0.85em
      color: var(--c-text-muted)

    .error-line
      color: var(--c-error)

    .error-id
      font-size: 0.8em
      opacity: 0.5

      code
        font-family: monospace

    .blocks
      display: flex
      flex-direction: column
      gap: 1.25rem
      text-align: left

      .block
        .block-header
          display: flex
          align-items: center
          gap: 0.75rem
          margin-bottom: 0.25rem

          .speaker
            font-weight: bold
            color: var(--c-speaker)
          .s0
            color: var(--c-speaker-0)
          .s1
            color: var(--c-speaker-1)

          .time
            font-family: monospace
            font-size: 0.85em
            opacity: 0.6

        .block-text
          margin: 0
          line-height: 1.5
</style>
