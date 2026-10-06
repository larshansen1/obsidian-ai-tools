# Spike notes: assistant-ui streaming from FastAPI (issue #117)

As of 2026-10-06. Throwaway code: `web/` (Next.js) and `spikes/assistant_ui/server.py` (FastAPI).
Covers C1, C2, C5, C8 as proof only.

## Result

All three "done when" checks passed in a real browser (Chrome):

| Check | What happened |
| --- | --- |
| A message streams in | Text deltas render word by word; chunks arrive spread over ~1.3 s through the Next.js proxy, not buffered. |
| The tool card renders | `topic_stats` call + result renders as a card via `makeAssistantToolUI`, with a "Loading…" state before the result. |
| Approval blocks until clicked | `fake_write` card shows Approve / Reject. Waited 10 s: still 1 request to `/chat`, `/writes` empty. After Approve: a second request carries the tool result, the server runs the fake write, `/writes` has one entry. |

## How to run

```bash
uv run uvicorn spikes.assistant_ui.server:app --host 127.0.0.1 --port 8100
cd web && npm install && npm run dev   # http://127.0.0.1:3117
```

Type anything for the stats card. Type a message containing "write" or "link" for the approval card.

## Versions pinned

`@assistant-ui/react` 0.15.25, `@assistant-ui/react-data-stream` 0.12.35, `next` 16.3.8, `react` 19.3.0.

## Findings

1. **Use the UI message stream, not the legacy data stream.** `useDataStreamRuntime` picks the protocol
   from a response header. Send `x-vercel-ai-ui-message-stream: v1` and SSE lines
   (`data: {json}\n\n`, ending with `data: [DONE]`). Chunk types used: `start`, `text-start`,
   `text-delta` (`delta`), `text-end`, `tool-input-start`, `tool-input-available` (`input`),
   `tool-output-available` (`output`), `start-step`, `finish-step`, `finish` (`finishReason`).
   Without the header it falls back to the UI message stream and logs a console warning.

2. **No Python library needed.** The wire format is ~30 lines of hand-written SSE in FastAPI.
   The PyPI package `assistant-stream` (0.0.36) exists but was not needed; skip it unless tool
   streaming gets complicated.

3. **Gotcha: do not send `start-step` right after `start`.** The decoder already opens a step on
   `start`. A second `start-step` counts as an extra step, and the local runtime's `maxSteps`
   (default 2) then marks the message `incomplete` instead of resuming after the approval click.
   Symptom: the card shows "Approved" but no follow-up request is sent. Only send `start-step`
   between steps (after a `finish-step`).

4. **Human-in-the-loop needs `unstable_humanToolNames`.** Pass `unstable_humanToolNames: ["fake_write"]`
   to `useDataStreamRuntime`. The backend sends the tool call with no output and finishes with
   `finishReason: "tool-calls"`. The card calls `addResult(...)`; the runtime then re-posts the
   whole thread to the backend, which runs the write. The write only happens on the backend, after
   the click. The `unstable_` prefix means this API can change; pin versions.

5. **Tool results arrive in AI SDK v5 shape.** The re-posted thread has
   `{"role": "tool", "content": [{"type": "tool-result", "toolCallId", "toolName",
   "output": {"type": "json", "value": {...}}}]}`. Read `output.value`, not `result`.

6. **`makeAssistantToolUI` is deprecated in 0.15.** It still works. The docs now point to putting
   `render` on a toolkit entry, or inline overrides on `MessagePrimitive.Parts`
   (https://assistant-ui.com/docs/migrations/toolkit-tools). C5 names `makeAssistantToolUI`;
   consider rewording C5 to "tool UI renderers" before building the real cards.
   `ThreadPrimitive.Messages components={...}` is also deprecated in favour of a children render
   function.

7. **Next.js 16 dev server quirks.**
   - Opening the app on `127.0.0.1` blocks dev chunks unless `allowedDevOrigins: ["127.0.0.1"]`
     is set. Symptom: page renders but never hydrates (Send stays disabled, no error shown).
   - `next dev` writes `AGENTS.md` and `CLAUDE.md` into `web/` unless `agentRules: false`.
   - Port 3000 was taken on this machine (OrbStack, kubectl port-forward); the spike uses 3117.
   - Bind with `-H 127.0.0.1` to keep the localhost-only rule (N1).

8. **Proxy, not CORS.** A Next.js `rewrites()` rule sends `/api/*` to FastAPI on `127.0.0.1:8100`.
   SSE streams through it fine in dev. Same origin means no CORS setup in FastAPI.

## Open for the real build

- Check the proxy still streams under `next build && next start` (only dev was tested).
- Decide whether to use the toolkit `render` API instead of the deprecated `makeAssistantToolUI`.
- The Reject button was not clicked in this spike. It uses the same `addResult` path
  (`{approved: false}`); the backend treats a missing or false `approved` as "do nothing".
