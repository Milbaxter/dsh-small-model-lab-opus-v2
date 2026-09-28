// Stop guard: when a turn is about to end and the last assistant message only
// *describes* work (announces next steps, shows a fix in a code block, or asks
// permission) instead of doing it, steer the agent to continue once or twice.
//
// General mechanism (a bounded Stop hook), not task-specific: small models often
// end a turn right after saying what they will do. The guard never fires when
// the model's last message reports a result without further intent, and it is
// capped per turn so it cannot loop.
import { randomUUID } from 'node:crypto';

export const name = 'lab-stop-guard';

const SOURCE = { kind: 'lab-stop-guard', form: 'notice', summary: 'stop guard' };

// Signals that the model stopped before acting.
const INTENT = /\b(let'?s|let me|i will|i'll|we will|we'll|we need to|we should|next,? (?:i|we)|now (?:i|we)(?:'ll| will)|i am going to|i'm going to)\b/i;
const PERMISSION = /\b(would you like me to|shall i|should i|do you want me to|if you(?:'d)? like,? i can|let me know if you want)\b/i;
const CODE_BLOCK = /```[a-z]*\n[\s\S]{40,}?```/i;
const PROPOSED_FIX = /\b(here'?s the (?:corrected|updated|fixed|modified|new)|corrected (?:version|implementation|code)|you can (?:fix|update|replace|change|run)|replace (?:the|it) with|should be (?:changed|updated|replaced))\b/i;

function text(content) {
  return (content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
}

function reasonFor(t, toolCallsThisTurn) {
  if (PERMISSION.test(t)) return 'you asked for permission instead of acting';
  if (PROPOSED_FIX.test(t) || (CODE_BLOCK.test(t) && /\b(fix|correct|updat|implement|replace|change)/i.test(t))) {
    return 'you showed or described a change instead of applying it to the files';
  }
  if (INTENT.test(t.slice(-600))) return 'you announced next steps but did not carry them out';
  if (toolCallsThisTurn === 0 && t.length < 400 && /\?\s*$/.test(t) === false && /\b(error|fail|issue|problem)\b/i.test(t)) {
    return 'you stopped after describing a problem without using any tools';
  }
  return null;
}

export function apply(ctx, config = {}) {
  const maxNudges = Number(config.maxNudgesPerTurn ?? 2);
  const state = new WeakMap(); // session -> { lastAssistant, toolCalls, nudges }

  const st = (session) => {
    let s = state.get(session);
    if (!s) state.set(session, s = { lastAssistant: '', toolCalls: 0, nudges: 0 });
    return s;
  };

  ctx.on('session/event', (session, event) => {
    const s = st(session);
    if (event.type === 'turn/start') {
      s.toolCalls = 0; s.nudges = 0; s.lastAssistant = '';
    } else if (event.type === 'tool/call') {
      s.toolCalls += 1;
    } else if (event.type === 'assistant/message') {
      const content = event.data?.message?.content || [];
      s.lastAssistant = content.some(c => c.type === 'tool-call') ? '' : text(content);
    }
  });

  ctx.on('agent/turn-stopping', ({ agent }) => {
    const s = st(agent.session);
    if (s.nudges >= maxNudges || !s.lastAssistant) return;
    const reason = reasonFor(s.lastAssistant, s.toolCalls);
    if (!reason) return;
    s.nudges += 1;
    const msg = `[harness] You ended your turn, but the task does not look finished: ${reason}. ` +
      'You have tools and permission to act in this workspace, so do the work now instead of describing it: ' +
      'edit the files and run the commands yourself, then check the result. Stop only when the requested ' +
      'outcome exists and is verified, or when you hit a concrete blocker you cannot resolve (then name it).';
    agent.steer({ id: randomUUID(), role: 'user', content: [{ type: 'text', text: msg }], source: SOURCE });
  });
}
