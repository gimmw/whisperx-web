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
    let state: 'queued' | 'processing' | 'error' | '' = '';
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
        <label>
            <input type="checkbox" bind:checked={optTimestamps} on:change={changeTimestamps} />
            Download with timestamps
        </label>
        
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
        color: rgba(255, 255, 255, 0.6)
        transition: color 0.2s ease

        &:hover
          color: rgba(255, 255, 255, 0.95)

    .status-line
      opacity: 0.85

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
      color: #ff9595
      opacity: 0.8

    .error-line
      color: #ff9595

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
            color: #ff9595
          .s0
            color: #59ffa1
          .s1
            color: #597aff

          .time
            font-family: monospace
            font-size: 0.85em
            opacity: 0.6

        .block-text
          margin: 0
          line-height: 1.5
</style>
