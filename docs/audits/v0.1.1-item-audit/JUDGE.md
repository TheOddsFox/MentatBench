# Judge instructions: Mentat-Hard v3 answerability sample

You are a careful, logically precise expert. You are given `sample.jsonl`: one JSON object per item, with the fields
`id`, `type`, `prompt` and `answer_format`. Answer every item.

## How to answer
- Answer each item from the prompt text alone. Use no outside knowledge about real sports, leagues, companies, markets or
  events. Do not open or use any other file, and do not run code.
- A prompt is a state, its instructions, and its options. Some states start with "League rules:", a statement of the
  rules of the league that the outcomes belong to. Treat those rules as part of the text and apply them as written.
- A group item (`type` "group") asks for the kind of constraint, a lower bound and an upper bound, and, for an
  ordered chain, the outcome numbers in chain order.
- Reading conventions for market labels: a label of No negates the question (the outcome is that the question's event
  does not happen). A label of Yes means the question as written. "Not fail to reach N" means "at least N".

## How to output
- For each item, write one JSON line: `{"id": "<the item's id>", "answer": <an object in that item's answer_format>}`.
- Write exactly one line per id in `sample.jsonl`, and nothing else: no prose, no headers, no code fences.
- Use the field names and values of `answer_format` exactly. For a relation, `answer` is `{"relation": "..."}`. For a
  group, `{"kind": ..., "lower": ..., "upper": ..., "chain": [...]}`.
