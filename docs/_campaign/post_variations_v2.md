# Campaign — post variations to test (draft for Ron, 2026-07-21)

## What v1 taught us (r/ClaudeAI post 1v22u8b, 25h)
- ~1,600 post views → **0 upvotes** (stuck at score 1), 10 comments (mostly fellow builders).
- Site visitors on launch day = **83 = baseline (~85/day)**. Near-zero clickthrough.
- **0 installer downloads.** 0 installs.
- Verdict: seen but not compelling. The bottleneck is the POST, not the site or download page — people didn't click.

## The core lesson
v1 **asked before it gave** ("I need beta testers"). r/ClaudeAI Rule 6 literally says *"you have to give before you can take."* A recruitment ask from an unknown account with 1 star reads as low-value. Every variation below flips to **give-first**: teach or show something useful; the tool is the payoff, not the request.

Secondary: v1 gated itself ("Max plan", "power users only"), shrinking the audience before the hook landed. Variations widen the top.

---

## Variation A — Show, don't tell (clip-first, r/ClaudeAI)
**Angle:** lead with the 17s clip; almost no text. Let the visual carry it.
**Title:** *5 Claude Code agents working across 5 projects at once — one dashboard I built to keep track*
**Body:** 2–3 sentences max + the clip. "Got tired of losing track across terminal windows, so everything's one tile-per-project grid now. Free, MIT, runs on your own machine. Repo in comments." No "looking for testers."
**Why:** removes the ask; the clip is the give. Video posts travel further and convert clicks better than a wall of text.

## Variation B — The teaching post (give-first, r/ClaudeAI or a blog x-post)
**Angle:** a genuinely useful how-I-work writeup; tool mentioned once at the end.
**Title:** *How I run 5 Claude Code agents across different projects without losing my mind*
**Body:** the actual workflow — one project per context, a memory file per project so agents don't start cold, a backlog for stray ideas, checking in from phone. Concrete tips someone could copy **even without the tool.** Last line: "I bundled all of this into a thing called Clayrune (free/MIT) if you want it prebuilt."
**Why:** maximal give-before-take; survives Rule 6/7 easily; the people who want the shortcut click through warm.

## Variation C — Self-host religion (r/selfhosted, new channel)
**Angle:** their values — your machine, your data, MIT, no cloud.
**Title:** *A self-hosted console for Claude Code agents — your machine, your keys, MIT, bring your own tunnel*
**Body:** lead with local-first + BYO-tunnel remote access (Tailscale/LAN), the grid/memory as features. State the awake caveat plainly (that audience respects honesty).
**Why:** different pool entirely; r/selfhosted rewards exactly what we are and is less builder-saturated than r/ClaudeAI.

## Variation D (cheap, durable) — awesome-claude-code PR
Not a post; a one-line PR adding Clayrune to the `awesome-claude-code` list. Long-tail discovery, near-zero effort, no timing risk. Do regardless of A/B/C.

---

## Recommended sequence
1. **B or A first** (same sub, give-first) — cleanest test of "was it the message?" holding channel constant.
2. **C** a day or two later — tests "was it the channel?"
3. **D** anytime.
Space them out; never same-day, never re-post the same thing to the same sub.

## Open question for Ron
- Which one feels most like you? I'd lead with **B** (it's the most "you" and the highest give), clip-first **A** as the fast second.
- Post from Cannonfidler1 again, or is there value in it coming from a fresh angle?
