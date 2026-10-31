"""Reference children for the local protocol (docs/local_protocol.md, version 2).

- heuristic_child: deterministic parsing and rules baseline (no model, no gold).
- lexical_child: lexical-retrieval baseline (EQUIV only; no IMPLIES/EXCLUDES/groups).
- embed_child: embeddings-without-reasoning baseline (cosine gate on cached model hidden states; no new download).
- hf_child: a pinned open-weights causal LM answering typed decisions by constrained choice.
- systemone_child: the same typed decisions answered by any server that implements /v1/systemone, Ollama's or another (torch-free).
- conformance: spawns a child and validates its answers (python -m mentat_bench.children.conformance <argv...>).
"""
