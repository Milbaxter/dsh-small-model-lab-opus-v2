// Matched-budget control: a behaviour-neutral system-prompt section whose only
// job is to cost the same context tokens as a candidate's prompt addition.
// Config: { words: <n> } -- text is truncated/repeated to about n words.
export const name = 'lab-placebo';
export const inject = ['systemPrompt'];

const NEUTRAL = `## Harness background
DeepSeek Harness composes its runtime from plugins mounted by the Cordis loader.
Each plugin declares the services it provides and the services it depends on,
and the loader resolves them into a tree when a profile starts. Profiles are
directories under the harness home that list bundle layers and a user patch.
Session logs are stored as line-delimited records with one event per line, and
projections rebuild views of a session from those records. Providers are
registered by adapter plugins, and each provider route names a protocol, an
endpoint and a model catalog. Tool schemas are generated from plugin metadata
and presented to the model with each request. The system prompt is assembled
from ordered sections contributed by plugins.`;

export function apply(ctx, config = {}) {
  const want = Math.max(1, Number(config.words ?? 120));
  const base = NEUTRAL.split(/\s+/);
  const words = [];
  while (words.length < want) words.push(...base);
  ctx.systemPrompt.section({ name: 'lab:placebo', order: 10300, text: words.slice(0, want).join(' ') });
}
