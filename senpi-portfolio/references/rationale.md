# Why the hard rules exist

The live failures behind SKILL.md's hard rules. Read when a user disputes one; the rules themselves
stay in SKILL.md.

## Gutting one sleeve of a multi-wallet strategy

The agent has done exactly this and it is catastrophic:

- "Close ox's \$600 wallet, keep the \$1,400 one" — **gutting one sleeve of a risk-parity core destroys
  the design.** The two sleeves are balanced *against each other*; keeping one is a different, unbalanced
  strategy the user never chose.
- "Keep cougar's short sleeve, repurpose its flat long sleeve" — **closing one sleeve of a long/short
  strategy leaves a NAKED directional position.** A market-neutral book with only its short leg is just
  a short — the exact opposite of neutral.

## Positions-first answers

Positions-first is the failure mode — the agent kept answering "analyze my strategies" with a raw positions dump.

## Caving to "it's closed"

This is the real failure this section prevents: a **live** strategy was re-narrated as closed — with a fabricated close-cascade — because the model deferred to a mistaken "it's closed" instead of re-reading the clearinghouse.
