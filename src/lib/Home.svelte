<script lang="ts">
    import { HOST } from './config'
    import Icon from '@iconify/svelte'

    let isDragging = false
    let isUploading = false
    let error = ""

    let enableDiarization = true
    let minSpeakers: number | null = null
    let maxSpeakers: number | null = null
    let initialPrompt = ""
    let language = "en"

    const languages: [string, string][] = [
        ["auto", "Autodetect"],
        ["en", "English"],
        ["zh", "Chinese"],
        ["de", "German"],
        ["es", "Spanish"],
        ["ru", "Russian"],
        ["ko", "Korean"],
        ["fr", "French"],
        ["ja", "Japanese"],
        ["pt", "Portuguese"],
        ["tr", "Turkish"],
        ["pl", "Polish"],
        ["ca", "Catalan"],
        ["nl", "Dutch"],
        ["ar", "Arabic"],
        ["sv", "Swedish"],
        ["it", "Italian"],
        ["id", "Indonesian"],
        ["hi", "Hindi"],
        ["fi", "Finnish"],
        ["vi", "Vietnamese"],
        ["he", "Hebrew"],
        ["uk", "Ukrainian"],
        ["el", "Greek"],
        ["ms", "Malay"],
        ["cs", "Czech"],
        ["ro", "Romanian"],
        ["da", "Danish"],
        ["hu", "Hungarian"],
        ["ta", "Tamil"],
        ["no", "Norwegian"],
        ["th", "Thai"],
        ["ur", "Urdu"],
        ["hr", "Croatian"],
        ["bg", "Bulgarian"],
        ["lt", "Lithuanian"],
        ["la", "Latin"],
        ["mi", "Maori"],
        ["ml", "Malayalam"],
        ["cy", "Welsh"],
        ["sk", "Slovak"],
        ["te", "Telugu"],
        ["fa", "Persian"],
        ["lv", "Latvian"],
        ["bn", "Bengali"],
        ["sr", "Serbian"],
        ["az", "Azerbaijani"],
        ["sl", "Slovenian"],
        ["kn", "Kannada"],
        ["et", "Estonian"],
        ["mk", "Macedonian"],
        ["br", "Breton"],
        ["eu", "Basque"],
        ["is", "Icelandic"],
        ["hy", "Armenian"],
        ["ne", "Nepali"],
        ["mn", "Mongolian"],
        ["bs", "Bosnian"],
        ["kk", "Kazakh"],
        ["sq", "Albanian"],
        ["sw", "Swahili"],
        ["gl", "Galician"],
        ["mr", "Marathi"],
        ["pa", "Punjabi"],
        ["si", "Sinhala"],
        ["km", "Khmer"],
        ["sn", "Shona"],
        ["yo", "Yoruba"],
        ["so", "Somali"],
        ["af", "Afrikaans"],
        ["oc", "Occitan"],
        ["ka", "Georgian"],
        ["be", "Belarusian"],
        ["tg", "Tajik"],
        ["sd", "Sindhi"],
        ["gu", "Gujarati"],
        ["am", "Amharic"],
        ["yi", "Yiddish"],
        ["lo", "Lao"],
        ["uz", "Uzbek"],
        ["fo", "Faroese"],
        ["ht", "Haitian Creole"],
        ["ps", "Pashto"],
        ["tk", "Turkmen"],
        ["nn", "Nynorsk"],
        ["mt", "Maltese"],
        ["sa", "Sanskrit"],
        ["lb", "Luxembourgish"],
        ["my", "Myanmar"],
        ["bo", "Tibetan"],
        ["tl", "Tagalog"],
        ["mg", "Malagasy"],
        ["as", "Assamese"],
        ["tt", "Tatar"],
        ["haw", "Hawaiian"],
        ["ln", "Lingala"],
        ["ha", "Hausa"],
        ["ba", "Bashkir"],
        ["jw", "Javanese"],
        ["su", "Sundanese"],
        ["yue", "Cantonese"],
    ]

    function onDragOver(event: DragEvent) {
        event.preventDefault()
        isDragging = true
        error = ""
    }

    function onDragLeave(event: DragEvent) {
        event.preventDefault()
        isDragging = false
    }

    function onDrop(e: DragEvent) {
        e.preventDefault()
        isDragging = false

        let _files = e.dataTransfer?.files
        if (!_files) return
        let files = Array.from(_files)

        // One file at a time
        if (files.length > 1)
            return error = 'Please upload one file at a time'

        let file = files[0]

        console.log('Uploading', file.name, file.size, file.type)
        upload(file)
    }

    async function upload(file: File) {
        isUploading = true
        error = ""

        // Upload file
        let formData = new FormData()
        formData.append('file', file)
        formData.append('language', language)
        formData.append('diarize', enableDiarization ? 'true' : 'false')
        if (enableDiarization && minSpeakers != null)
            formData.append('min_speakers', String(minSpeakers))
        if (enableDiarization && maxSpeakers != null)
            formData.append('max_speakers', String(maxSpeakers))
        if (initialPrompt.trim())
            formData.append('initial_prompt', initialPrompt.trim())

        let res: any
        try {
            const resp = await fetch(`${HOST}/upload`, {
                method: 'POST',
                body: formData
            })
            res = await resp.json()
        } catch (e) {
            // Network failure, or a response that wasn't JSON (e.g. a proxy
            // error page). Without this the promise rejects unhandled and the
            // UI stays stuck on "Uploading..." forever.
            console.error('Upload failed', e)
            error = 'Upload failed. Please check your connection and try again.'
            isUploading = false
            return
        }

        console.log('Upload result', res)

        // Result should be a UUID
        if (res.error) {
            // Rejections (unsupported type, file too large) are expected, so
            // clear the uploading state to let the user pick another file.
            error = res.error
            isUploading = false
        } else {
            // Redirect to /:uuid
            window.location.href = `/${res.audio_id}`;
        }
    }

    function onFileChange(e: Event) {
        let input = e.target as HTMLInputElement
        let file = input.files?.[0]
        if (!file) return
        upload(file)
    }
</script>

<div>
    <div class="drop-area" on:dragover={onDragOver} on:dragleave={onDragLeave} on:drop={onDrop}>
        {#if isUploading}
            Uploading...
        {:else if error}
            {error}
        {:else}
            📥 drop file to transcribe
        {/if}
    </div>

    <div class="options">
        <label class="option-label">
            Language
            <select bind:value={language}>
                {#each languages as [code, name]}
                    <option value={code}>{name}</option>
                {/each}
            </select>
        </label>

        <label class="toggle">
            <input type="checkbox" bind:checked={enableDiarization} />
            Speaker diarisation
        </label>

        {#if enableDiarization}
            <div class="speaker-opts">
                <!-- min="0" because the backend treats 0 as "no preference",
                     the same as leaving the field blank. Keeping min="1" here
                     would let the browser block a value the API accepts. -->
                <label>
                    Min speakers
                    <input type="number" min="0" max="20" placeholder="auto" bind:value={minSpeakers} />
                </label>
                <label>
                    Max speakers
                    <input type="number" min="0" max="20" placeholder="auto" bind:value={maxSpeakers} />
                </label>
            </div>
        {/if}

        <label class="prompt-label">
            Initial prompt
            <input type="text" placeholder="Optional context to guide transcription" bind:value={initialPrompt} />
        </label>
    </div>

    <div class="upload-btn">
        <input type="file" id="file" on:change={onFileChange} />
        <label for="file"><Icon icon="tabler:upload"/></label>
    </div>
</div>

<style lang="sass">
  body
    font-family: 'Arial', sans-serif

  $c-emp: #86a2ff
  $c-error: #ff8e8e

  .drop-area
    z-index: 100

    border: 2px dashed $c-emp
    border-radius: 1rem
    background: rgba(white, 0.2)

    // Blur
    backdrop-filter: blur(10px)

    position: fixed
    inset: 0
    margin: 5rem

    display: flex
    flex-direction: column
    align-items: center
    justify-content: center
    text-align: center

    font-size: 3em
    color: $c-emp

  .drop-area.error
    border-color: $c-error
    color: $c-error

  .options
    position: fixed
    bottom: 6rem
    left: 50%
    transform: translateX(-50%)
    z-index: 110

    display: flex
    flex-direction: column
    align-items: center
    gap: 0.75rem

    color: white
    font-size: 0.9rem

    .option-label
      display: flex
      flex-direction: column
      align-items: center
      gap: 0.25rem
      font-size: 0.8rem

      select
        padding: 0.3rem 0.5rem
        border: 1px solid rgba(white, 0.3)
        border-radius: 0.4rem
        background: rgba(white, 0.1)
        color: white
        font-size: 0.85rem
        cursor: pointer

        option
          background: #242424
          color: white

    .toggle
      display: flex
      align-items: center
      gap: 0.5rem
      cursor: pointer

      input[type="checkbox"]
        width: 1.1rem
        height: 1.1rem
        cursor: pointer

    .speaker-opts
      display: flex
      gap: 1.5rem

      label
        display: flex
        flex-direction: column
        align-items: center
        gap: 0.25rem
        font-size: 0.8rem

      input[type="number"]
        width: 5rem
        padding: 0.3rem 0.5rem
        border: 1px solid rgba(white, 0.3)
        border-radius: 0.4rem
        background: rgba(white, 0.1)
        color: white
        font-size: 0.85rem
        text-align: center

        &::placeholder
          color: rgba(white, 0.4)

    .prompt-label
      display: flex
      flex-direction: column
      align-items: center
      gap: 0.25rem
      font-size: 0.8rem
      width: 100%

      input[type="text"]
        width: 20rem
        max-width: 90vw
        padding: 0.3rem 0.5rem
        border: 1px solid rgba(white, 0.3)
        border-radius: 0.4rem
        background: rgba(white, 0.1)
        color: white
        font-size: 0.85rem
        text-align: center

        &::placeholder
          color: rgba(white, 0.4)

  .upload-btn
    position: fixed
    bottom: 3rem
    right: 3rem

    z-index: 110

    background: $c-emp
    width: 50px
    height: 50px
    border-radius: 50%

    color: white
    display: flex
    align-items: center
    justify-content: center

    box-shadow: 0 3px 5px rgba(black, 0.4)

    cursor: pointer

    input
      display: none
</style>
