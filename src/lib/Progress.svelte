<script lang="ts">
    import { onMount } from 'svelte';
    import {HOST} from "./config";
    import moment from 'moment';

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

    async function checkProgress() {
        const response = await fetch(`${HOST}/progress/${id}`);
        const data = await response.json();

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
            progress = data.status;
            setTimeout(checkProgress, 1000); // Check progress regularly
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
    {:else}
        <p>Progress: {progress}</p>
    {/if}
</main>

<style lang="sass">
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
