# Test-taker instructions

You are a careful, logically precise expert. After these instructions you are given a list of items, one JSON object per line,
with the fields `id`, `type`, `prompt` and `answer_format`. Answer every item.

## How to answer
- Answer each item from the prompt text alone. Use no outside knowledge about real sports, leagues, companies, markets or
  events. Do not use any tool, file or code.
- A prompt is a state, its instructions, and its options. Some states start with "League rules:", a statement of the
  rules of the league that the outcomes belong to. Treat those rules as part of the text and apply them as written.
- A group item (`type` "group") asks for the kind of constraint, a lower bound and an upper bound, and, for an
  ordered chain, the outcome numbers in chain order.
- Reading conventions for market labels: a label of No negates the question (the outcome is that the question's event
  does not happen). A label of Yes means the question as written. "Not fail to reach N" means "at least N".
- You may reason step by step in prose before you give your answers.

## How to output
- End your reply with exactly one fenced block: a line containing only ```jsonl, then the lines, then a line containing
  only ```. Write this block exactly once, at the end of your reply. Do not write a jsonl block, or any line of the form
  {"id": ..., "answer": ...}, anywhere earlier in your reply. Nothing comes after the closing fence.
- Inside the block write one JSON line per item: `{"id": "<the item's id>", "answer": <an object in that item's answer_format>}`.
- Write exactly one line per id in the items, and no other line in the block. Use the field names and values of
  `answer_format` exactly. For a relation, `answer` is `{"relation": "..."}`. For a group, `answer` is
  `{"kind": ..., "lower": ..., "upper": ..., "chain": [...]}`.
