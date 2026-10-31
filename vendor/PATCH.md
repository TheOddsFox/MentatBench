varisat 0.2.2 (crates.io), patched:
- model.rs `reconstruct_global_model`: no longer copies the whole assignment on every SAT answer
  (O(nvars)); only done when proofs record models.
- solver.rs: new `Solver::model_value(var)` reads the live assignment in O(1); `model()` is rebuilt on it.
Its own unit tests that read the removed buffer fail by design.
