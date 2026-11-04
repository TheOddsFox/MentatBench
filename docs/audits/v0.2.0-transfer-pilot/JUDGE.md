# Judge instructions: transfer pilot items

You are a careful, logically precise expert. After these instructions you are given a list of items, one JSON object per line, with
the fields `id`, `type`, `prompt` and `answer_format`. Answer every item.

## What the items are
- A relation item (`type` "relation") gives two propositions, A and B. A proposition is one outcome of a question. Its text is the
  question and the outcome label, for example "Yes", "No", "Over", or the name of one side.
- A group item (`type` "group") gives a set of 2 to 8 propositions, numbered 1 to n.

## How to answer
- Answer from the text of the items alone. Use what the words and the logic of the text say, and nothing else. Use no knowledge of
  the real world: not who holds an office, what has already happened, how a contest or a market is decided, or what a price or a
  count is now. Do not use any tool, file or code.
- Reading conventions. A label of "Yes" means the question as written. A label of "No" means that the question's event does not
  happen, so the proposition is the negation of the question. A label that names one side of the question (such as "Over", "Under"
  or a team name) means that side, as the question describes it.
- Logic in the text is part of the text. "Above 110" implies "above 100". "By June 30" implies "by December 31". "Exactly 2" excludes
  "exactly 3". Use such consequences when they follow from the wording.
- Membership in one event or one question family does not, by itself, make propositions exclusive or exhaustive. Several outcomes may
  all hold unless the text makes that impossible, and at least one may fail unless the text makes it certain.
- Consider every interpretation of the text that is consistent with the question and the label. A consequence is established when it
  holds in every one of them. A thing the text leaves open is not assumed either way.

## Relation answers
Answer one of EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE or UNCLEAR.
- EQUIV: A and B hold in exactly the same interpretations (A implies B, and B implies A).
- IMPLIES: A implies B, and B does not imply A. Every interpretation in which A holds is one in which B holds, and some
  interpretation has B without A.
- IMPLIED_BY: B implies A, and A does not imply B.
- EXCLUDES: no interpretation makes both A and B hold.
- NONE: none of the above holds. A and B can both hold, and neither implies the other.
- UNCLEAR: the text does not settle which of these holds, for example because a term, a threshold or a date is not defined in the
  text, or because the answer needs a fact the text does not give.

If A and B are equivalent, answer EQUIV. Otherwise, if A implies B, answer IMPLIES. Otherwise, if B implies A, answer IMPLIED_BY.
Otherwise, if they cannot both hold, answer EXCLUDES. Otherwise answer NONE.

## Group answers
A group item asks for the constraint on how many of its members are true, in every interpretation consistent with the text.
- `lower` and `upper` are the bounds the text forces: in every interpretation, lower is at most the number of true members, and that
  number is at most upper. Both are integers from 0 to n. Give the tightest bounds the text forces.
- `kind` names the constraint:
  - none: no constraint. lower 0, upper n.
  - exactly_one: exactly one member is true. lower 1, upper 1.
  - at_most_one: at most one member is true. lower 0, upper 1.
  - at_least_k: at least k members are true. lower k (k at least 1), upper n.
  - at_most_k: at most k members are true. lower 0, upper k (k at least 2).
  - ordered_chain: the members form a chain of implications, in the order you list them: member 1 implies member 2, which implies
    member 3, and so on. The bounds still apply.
  - When the bounds carry two constraints (a lower bound above 0 and an upper bound below n), give the kind of either one. The bounds
    carry the constraint.
- `chain` lists the member numbers in chain order, strongest first, when the kind is ordered_chain. Otherwise it is [].
- When the text does not settle the constraint, answer kind UNCLEAR with lower 0, upper n and chain [].

## Output
- You may reason step by step in prose before you give your answers. Use no tool, and do not hand the task to anyone else.
- End your reply with exactly one fenced block: a line containing only ```jsonl, then the lines, then a line containing only ```.
  Write this block exactly once, at the end of your reply. Do not write a jsonl block, or any line of the form
  {"id": ..., "answer": ...}, anywhere earlier in your reply. Nothing comes after the closing fence.
- Inside the block write one JSON line per item: `{"id": "<the item's id>", "answer": <an object in that item's answer_format>}`.
- Write exactly one line per id in the items, and no other line in the block. Use the field names and values of `answer_format`
  exactly. For a relation, `answer` is `{"relation": "..."}`. For a group, `answer` is
  `{"kind": ..., "lower": ..., "upper": ..., "chain": [...]}`.
