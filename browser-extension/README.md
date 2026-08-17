# JobMatch AI private alpha overlay

Pinned upstream: `wadekarg/JobMatchAI@977c90cb3100c8287a12085f8a1d52b8051c0977`

This directory is **bootstrap/overlay source only**. It does not run inside Career Engine. The intended runtime remains a private upstream-tracking JobMatch AI fork.

The overlay deliberately keeps the upstream extension and UI intact. It adds only:

1. **OpenCode Go** as an existing OpenAI-compatible AI provider, defaulting to `deepseek-v4-flash`.
2. `https://opencode.ai/*` host permission.
3. A standalone submission detector that reuses upstream `MARK_APPLIED` and `GET_APPLIED_JOBS` messages.

No Career Engine backend, new extension UI, auto-submit, public-SaaS hardening, or ModelRelay integration is included.

## Apply to an upstream checkout

```bash
git clone https://github.com/wadekarg/JobMatchAI.git
cd JobMatchAI
git checkout 977c90cb3100c8287a12085f8a1d52b8051c0977
git apply /path/to/0001-private-alpha-opencode-go-and-submission.patch
npm ci
npm test
```

For the intended private fork, keep the normal two-remote arrangement:

```bash
git remote -v
# origin   -> HameedFarah private fork
# upstream -> https://github.com/wadekarg/JobMatchAI.git
```

Fetch/merge upstream normally and run `git apply --check` when an upstream change touches `aiService.js` or `manifest.json`.

## Chrome load

After applying the patch:

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose **Load unpacked** and select the patched JobMatchAI directory.
4. Open the existing JobMatch AI **AI Settings** page.
5. Select **OpenCode Go**.
6. Paste the OpenCode Go API key.
7. Keep **DeepSeek V4 Flash**.
8. Use the existing **Test Connection** button.

## Submission behavior

The detector reacts only to final-looking labels such as `Submit application`, `Submit`, `Finish and submit`, and `Complete application`. It deliberately ignores `Apply now`, `Next`, and `Continue`.

After a final-looking submit click it:

- records a two-minute pending submission in `chrome.storage.session`;
- watches the current page for a success URL/text state for 15 seconds;
- survives navigation to a thank-you/confirmation page;
- asks for confirmation before marking anything Applied;
- if no success state is detected on the same page, asks `Did this application submit successfully?`;
- calls upstream's existing `MARK_APPLIED` path and keeps its URL deduplication.

It does **not** submit applications and does not implement exit intent yet.

## Validation performed during implementation

- `submissionDetector.js` passed `node --check`.
- final-submit/success regex smoke tests passed.
- the unified patch passed `git apply --check` against an upstream-shaped fixture.

Full upstream `npm test` and a live OpenCode Go connection require a real checkout and the owner's OpenCode Go API key; those were not executable in the current tool runtime.
