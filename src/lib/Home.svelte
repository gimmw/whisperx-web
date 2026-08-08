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

    // Settings are modal at every viewport size. The defaults (English,
    // diarisation on, automatic speaker count, no prompt) are right for most
    // uploads, so the panel is a rarely-used detour rather than part of the
    // main flow -- giving it permanent screen space costs every user to
    // benefit a few. Keeping one behaviour everywhere also removes the class
    // of bug where the modal and inline layouts disagree.
    let settingsOpen = false
    let sheetEl: HTMLDivElement | null = null
    let settingsBtnEl: HTMLButtonElement | null = null

    function openSettings() {
        settingsOpen = true
    }

    function closeSettings() {
        settingsOpen = false
        // Return focus to the control that opened the panel, otherwise a
        // keyboard user is dropped back at the top of the document.
        settingsBtnEl?.focus()
    }

    function onSheetKeydown(e: KeyboardEvent) {
        if (e.key === 'Escape') {
            e.stopPropagation()
            closeSettings()
            return
        }

        // Focus trap: a modal that lets Tab escape to the content behind it is
        // worse than no modal, since the backdrop hides where focus has gone.
        if (e.key !== 'Tab' || !sheetEl || !settingsOpen) return

        const focusable = Array.from(
            sheetEl.querySelectorAll<HTMLElement>(
                'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
            )
        ).filter(el => !el.hasAttribute('disabled'))
        if (focusable.length === 0) return

        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const active = document.activeElement

        if (e.shiftKey && active === first) {
            e.preventDefault()
            last.focus()
        } else if (!e.shiftKey && active === last) {
            e.preventDefault()
            first.focus()
        }
    }

    // Move focus into the panel when it opens, so the first control is
    // immediately reachable by keyboard.
    $: if (settingsOpen && sheetEl) {
        queueMicrotask(() => sheetEl?.querySelector<HTMLElement>('select, input, button')?.focus())
    }

    // Non-default settings, surfaced on the button so a user who changed
    // something can see it without reopening the panel. Hiding settings makes
    // a stale non-default (say, the wrong language from a previous upload)
    // invisible at the moment it matters, which this guards against.
    $: activeSettings = [
        language !== 'en' ? (languages.find(([c]) => c === language)?.[1] ?? language) : null,
        !enableDiarization ? 'no diarisation' : null,
        enableDiarization && (minSpeakers || maxSpeakers) ? 'speaker range' : null,
        initialPrompt.trim() ? 'prompt' : null,
    ].filter(Boolean) as string[]

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

<div class="page">
    <!-- The file input is the single source of file selection for both the
         drop area and the FAB, so it lives here at the top rather than being
         nested inside either. Visually hidden rather than display:none so it
         stays keyboard-focusable and screen-reader-announced. -->
    <input class="file-input" type="file" id="file" on:change={onFileChange} />

    <!-- A <label for="file"> rather than a plain <div>: it makes the whole
         area open the file picker on click/tap, which is the only way to
         choose a file on touch devices -- they have no drag-and-drop, so this
         was previously the largest element on screen and did nothing. Using a
         label (not a click handler) means keyboard and assistive-tech users
         get the native control association for free. -->
    <label
        class="drop-area"
        class:dragging={isDragging}
        class:has-error={!!error}
        for="file"
        on:dragover={onDragOver}
        on:dragleave={onDragLeave}
        on:drop={onDrop}
    >
        {#if isUploading}
            <span class="drop-text">Uploading…</span>
        {:else if error}
            <span class="drop-text">{error}</span>
        {:else}
            <span class="drop-icon" aria-hidden="true">📥</span>
            <!-- Two labels, one per input modality. Which one is shown is
                 decided in CSS by (hover: none), because the distinction that
                 matters is whether the device can drag, not how wide it is --
                 a narrow desktop window can still drag, a large tablet cannot. -->
            <span class="drop-text pointer-copy">drop file to transcribe</span>
            <span class="drop-text touch-copy">Tap to choose a file</span>
        {/if}
    </label>

    <div class="controls">
        <button
            type="button"
            class="settings-btn"
            class:modified={activeSettings.length > 0}
            bind:this={settingsBtnEl}
            on:click={openSettings}
            aria-haspopup="dialog"
            aria-expanded={settingsOpen}
            aria-controls="settings-panel"
        >
            <Icon icon="tabler:settings" width="22" height="22" />
            <span class="settings-btn-text">
                {#if activeSettings.length > 0}
                    {activeSettings.join(' · ')}
                {:else}
                    Settings
                {/if}
            </span>
        </button>

        <label class="upload-btn" for="file">
            <Icon icon="tabler:upload" width="24" height="24" />
            <span class="sr-only">Choose a file to transcribe</span>
        </label>
    </div>

    <!-- Tapping the backdrop closes the panel, which is the expected gesture.
         It is a <button> rather than a <div> so that behaviour is reachable
         without a pointer; the visible close control in the header serves the
         same purpose, so this one is hidden from assistive tech to avoid
         announcing it twice. -->
    {#if settingsOpen}
        <button
            type="button"
            class="sheet-backdrop"
            on:click={closeSettings}
            tabindex="-1"
            aria-hidden="true"
        ></button>
    {/if}

    <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
    <div
        class="settings-panel"
        class:open={settingsOpen}
        id="settings-panel"
        bind:this={sheetEl}
        role="dialog"
        aria-modal="true"
        aria-label="Transcription settings"
        on:keydown={onSheetKeydown}
    >
        <div class="sheet-header">
            <span class="sheet-title">Settings</span>
            <button type="button" class="sheet-close" on:click={closeSettings} aria-label="Close settings">
                <Icon icon="tabler:x" width="20" height="20" />
            </button>
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
    </div>
</div>

<style lang="sass">
  $c-emp: #86a2ff
  $c-error: #ff8e8e

  // Where the settings modal stops being a full-width bottom sheet and becomes
  // a centred dialog. This is presentation only -- it is a modal either side of
  // the breakpoint, so nothing about its behaviour depends on this value.
  // Chosen to sit above the widest common phone in portrait (~430px) and below
  // small tablets in portrait (768px).
  $bp-sheet: 600px

  // Layout is normal document flow rather than a stack of `position: fixed`
  // boxes. The previous version pinned every element to the viewport, which
  // meant nothing could reflow: on a 375px screen the drop area's fixed 5rem
  // margin left it 215px wide while the prompt input inside asked for 320px,
  // and in landscape the options block was taller than the space between the
  // title and the buttons. Flow layout plus clamp() lets all of that adapt.
  .page
    box-sizing: border-box
    width: 100%
    min-height: 100dvh
    display: flex
    flex-direction: column
    // Generous on desktop, tight on phones. The old fixed 5rem burned 160px
    // (43%) of a 375px viewport on margin alone.
    padding: clamp(0.75rem, 4vw, 5rem)
    // Leave room for the fixed title/github row in App.svelte.
    padding-top: clamp(3.5rem, 12vw, 6rem)
    gap: clamp(0.75rem, 3vw, 1.5rem)

  // Visually hidden but still focusable and announced. display:none would
  // remove it from the accessibility tree and from keyboard tab order.
  .file-input
    position: absolute
    width: 1px
    height: 1px
    padding: 0
    margin: -1px
    overflow: hidden
    clip: rect(0, 0, 0, 0)
    white-space: nowrap
    border: 0

  .sr-only
    position: absolute
    width: 1px
    height: 1px
    padding: 0
    margin: -1px
    overflow: hidden
    clip: rect(0, 0, 0, 0)
    white-space: nowrap
    border: 0

  .drop-area
    flex: 1 1 auto
    // Keeps the area usable in landscape, where viewport height is scarce and
    // a percentage-based height would collapse to almost nothing.
    min-height: clamp(8rem, 30vh, 20rem)

    box-sizing: border-box
    border: 2px dashed $c-emp
    border-radius: 1rem
    background: rgba(white, 0.06)
    backdrop-filter: blur(10px)

    display: flex
    flex-direction: column
    align-items: center
    justify-content: center
    text-align: center
    gap: 0.5rem
    padding: 1rem

    // Was a flat 3em (48px), which overflowed a 215px-wide box. Scales with
    // the viewport instead, with a floor that stays readable.
    font-size: clamp(1.1rem, 5vw, 3em)
    color: $c-emp
    // It is a <label for="file"> now, so make that affordance visible.
    cursor: pointer
    transition: background 0.2s ease, border-color 0.2s ease

    &:hover
      background: rgba(white, 0.1)

    // The file input is visually hidden, so its focus ring would be invisible.
    // Project it onto this label instead, otherwise keyboard users get no
    // indication of focus at all.
    :global(.file-input:focus-visible) + &
      outline: 2px solid $c-emp
      outline-offset: 3px

  .drop-area.dragging
    background: rgba(134, 162, 255, 0.18)
    border-color: white

  .drop-area.has-error
    border-color: $c-error
    color: $c-error

  .drop-icon
    font-size: 1.4em
    line-height: 1

  .drop-text
    // Long backend error messages ("File too large. Maximum upload size is
    // 512 MB.") render at this size inside a narrow box, so allow wrapping
    // rather than letting them overflow.
    max-width: 100%
    overflow-wrap: anywhere
    font-size: 0.85em

  // Which copy shows is a function of input capability, not width: a narrow
  // desktop window can still drag; a 1024px tablet cannot. Default to the
  // drag wording and swap it only where hover/fine pointers are absent.
  .touch-copy
    display: none

  @media (hover: none), (pointer: coarse)
    .pointer-copy
      display: none
    .touch-copy
      display: inline

  .controls
    display: flex
    align-items: center
    // Settings on the left, upload on the right: the primary action keeps the
    // bottom-right position it had before, so the change does not move the
    // control most users reach for.
    justify-content: space-between
    gap: 0.75rem
    flex: 0 0 auto
    min-width: 0

  .settings-btn
    display: inline-flex
    align-items: center
    gap: 0.4rem
    padding: 0.5rem 0.9rem
    border-radius: 999px
    border: 1px solid rgba(white, 0.25)
    background: rgba(white, 0.08)
    color: rgba(white, 0.9)
    font-size: 0.9rem
    font-family: inherit
    cursor: pointer
    // 44px minimum touch target (WCAG 2.5.5 / iOS HIG).
    min-height: 44px
    // The summary text can grow (a long language name plus two other items),
    // so let it shrink and ellipsize rather than push the upload button off
    // the row on a narrow screen.
    min-width: 0
    flex: 0 1 auto

    &:hover
      border-color: $c-emp
      color: white

  // Non-default settings are in effect. Tinted rather than merely labelled,
  // so it reads as "changed" at a glance without needing the text.
  .settings-btn.modified
    border-color: $c-emp
    color: $c-emp
    background: rgba(134, 162, 255, 0.12)

  .settings-btn-text
    overflow: hidden
    text-overflow: ellipsis
    white-space: nowrap

  .upload-btn
    display: inline-flex
    align-items: center
    justify-content: center
    width: 56px
    height: 56px
    min-width: 56px
    border-radius: 50%
    background: $c-emp
    color: white
    box-shadow: 0 3px 5px rgba(black, 0.4)
    cursor: pointer

    :global(.file-input:focus-visible) ~ .controls &
      outline: 2px solid white
      outline-offset: 2px

  .options
    display: flex
    flex-direction: column
    gap: 0.75rem
    color: white
    font-size: 0.9rem

    .option-label,
    .prompt-label
      display: flex
      flex-direction: column
      gap: 0.25rem
      font-size: 0.8rem

    select,
    input[type="text"],
    input[type="number"]
      // width:100% + border-box is what stops the prompt input overflowing its
      // container. The old `width: 20rem; max-width: 90vw` measured against the
      // viewport, not the parent, so it stayed 320px wide inside a 215px box.
      box-sizing: border-box
      width: 100%
      padding: 0.5rem
      border: 1px solid rgba(white, 0.3)
      border-radius: 0.4rem
      background: rgba(white, 0.1)
      color: white
      font-size: 16px // < 16px causes iOS Safari to zoom on focus
      font-family: inherit
      min-height: 44px

      &::placeholder
        color: rgba(white, 0.4)

    select option
      background: #242424
      color: white

    .toggle
      display: flex
      align-items: center
      gap: 0.5rem
      cursor: pointer
      min-height: 44px

      input[type="checkbox"]
        width: 1.15rem
        height: 1.15rem
        cursor: pointer

    .speaker-opts
      display: flex
      gap: 1rem

      label
        flex: 1 1 0
        min-width: 0 // lets the flex children actually shrink
        display: flex
        flex-direction: column
        gap: 0.25rem
        font-size: 0.8rem

  // -----------------------------------------------------------------------
  // Settings panel: modal at every viewport size.
  //
  // Anchored to the bottom on narrow screens (a thumb-reachable sheet) and
  // centred as a dialog once there is room, but it is the same modal in both
  // cases -- open/closed state, focus handling and dismissal do not vary with
  // width, so there is no second layout that can silently disagree with the
  // first.
  // -----------------------------------------------------------------------
  .settings-panel
    position: fixed
    z-index: 200
    box-sizing: border-box

    left: 0
    right: 0
    bottom: 0

    background: #1e1e1e
    border-top: 1px solid rgba(white, 0.15)
    border-top-left-radius: 1rem
    border-top-right-radius: 1rem
    padding: 1rem
    // Clears the iOS home indicator / Android gesture bar.
    padding-bottom: calc(1rem + env(safe-area-inset-bottom))

    // Never taller than the viewport; scroll internally if the speaker fields
    // push it past that.
    max-height: 85dvh
    overflow-y: auto

    transform: translateY(100%)
    transition: transform 0.25s ease, opacity 0.25s ease, visibility 0.25s
    visibility: hidden

  .settings-panel.open
    transform: translateY(0)
    visibility: visible

  // Once there is room, centre it as a conventional dialog rather than
  // stretching a full-width sheet across a wide screen.
  @media (min-width: $bp-sheet)
    .settings-panel
      left: 50%
      right: auto
      top: 50%
      bottom: auto
      // #{} so Sass emits calc() verbatim instead of folding it away: it
      // simplifies min(30rem, calc(100vw - 2rem)) to the invalid
      // "min(30rem, 100vw - 2rem)", which browsers drop entirely, leaving the
      // dialog at its full 30rem on viewports narrower than that.
      width: #{"min(30rem, calc(100vw - 2rem))"}
      border-radius: 1rem
      border: 1px solid rgba(white, 0.15)
      padding: 1.25rem
      max-height: min(85dvh, 40rem)
      box-shadow: 0 10px 40px rgba(black, 0.5)

      // translate(-50%, -50%) centres it; the extra Y offset is the entry
      // animation, so the closed state sits slightly low and fades up.
      transform: translate(-50%, calc(-50% + 0.75rem))
      opacity: 0

    .settings-panel.open
      transform: translate(-50%, -50%)
      opacity: 1

  .sheet-backdrop
    display: block
    position: fixed
    inset: 0
    z-index: 199
    // Reset the <button> defaults, since this is a bare hit target.
    border: none
    padding: 0
    background: rgba(black, 0.5)
    cursor: default

  .sheet-header
    display: flex
    align-items: center
    justify-content: space-between
    margin-bottom: 0.75rem

  .sheet-title
    font-size: 1rem
    font-weight: 600
    color: white

  .sheet-close
    display: inline-flex
    align-items: center
    justify-content: center
    width: 44px
    height: 44px
    border: none
    border-radius: 50%
    background: transparent
    color: rgba(white, 0.7)
    cursor: pointer

    &:hover
      color: white

  // Respect reduced-motion: the slide/fade is decorative.
  @media (prefers-reduced-motion: reduce)
    .settings-panel
      transition: none
    .drop-area
      transition: none
</style>
