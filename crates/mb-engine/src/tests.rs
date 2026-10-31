//! Unit tests for query semantics. Submissions are built from JSON and validated first.

use crate::check::{certify, check};
use crate::engine::Engine;
use crate::world::{leaked_engines, World};
use mb_ir::{validate, AnswerIn, AnswerOut, Binding, Claim, Expr, Kind, Provenance, Query, QueryBody, Status, Submission, Theory};
use serde_json::{json, Value};
use std::collections::HashMap;

fn sub(interps: &[&str], aliases: Value, constraints: Value, bindings: Vec<Value>) -> Submission {
    let theory: Theory = serde_json::from_value(json!({
        "interpretations": interps,
        "aliases": aliases,
        "constraints": constraints,
    }))
    .unwrap();
    let bindings: Vec<Binding> = bindings
        .into_iter()
        .map(|b| serde_json::from_value(b).unwrap())
        .collect();
    let provenance = vec![Provenance {
        reference: "R1".into(),
        kind: Kind::Source,
        record: "m1".into(),
        field: "question".into(),
    }];
    let s = Submission {
        bindings,
        theory,
        provenance,
        bytes: 0,
    };
    let errs = validate(&s, None);
    assert!(errs.is_empty(), "submission invalid: {errs:?}");
    s
}

fn unresolved(id: &str) -> Value {
    json!({"id": id, "status": "unresolved", "class": null, "missing": ["no_date"]})
}

fn bound(id: &str, class: &str) -> Value {
    json!({"id": id, "status": "bound", "class": class, "evidence": ["R1"]})
}

fn var(id: &str) -> Value {
    json!({"op": "var", "id": id})
}

fn not(e: Value) -> Value {
    json!({"op": "not", "a": e})
}

/// Constraint with provenance and all interpretations.
fn cons(id: &str, expr: Value) -> Value {
    json!({"id": id, "expr": expr, "interps": null, "provenance": ["R1"]})
}

fn run(s: &Submission, q: Value) -> Value {
    let q: Query = serde_json::from_value(q).unwrap();
    let mut e = World::new(s);
    let w = serde_json::to_value(e.answer(&q)).unwrap();
    // The single-solver engine and the component-partitioned world agree (witnesses may differ).
    let mut m = Engine::new(s).unwrap();
    let mono = serde_json::to_value(m.answer(&q)).unwrap();
    for key in ["witness", "counterexample"] {
        assert_eq!(w[key].is_null(), mono[key].is_null(), "{key}: {w} vs {mono}");
    }
    let strip = |mut v: Value| {
        for key in ["witness", "counterexample", "core"] {
            if let Some(o) = v.as_object_mut() {
                o.remove(key);
            }
        }
        v
    };
    assert_eq!(strip(w.clone()), strip(mono), "world vs engine");
    w
}

fn feas(s: &Submission, assume: Vec<Value>) -> Value {
    run(s, json!({"qid": "f", "family": "feasibility", "assume": assume}))
}

fn decision(v: &Value) -> &str {
    v["decision"].as_str().unwrap_or("")
}

fn labels(v: &Value, label: &str) -> Vec<String> {
    v["labels"][label]
        .as_array()
        .unwrap_or_else(|| panic!("no labels in {v}"))
        .iter()
        .map(|x| x.as_str().unwrap().to_string())
        .collect()
}

#[test]
fn pairs_feasible_but_jointly_unsat() {
    // exactly-one over a, b, c.
    let s = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "exactly", "k": 1, "args": [var("a"), var("b"), var("c")]}))]),
        vec![unresolved("a"), unresolved("b"), unresolved("c")],
    );
    for pair in [
        vec![not(var("a")), not(var("b"))],
        vec![not(var("b")), not(var("c"))],
        vec![not(var("a")), not(var("c"))],
    ] {
        assert_eq!(decision(&feas(&s, pair)), "SAT");
    }
    let all_neg = vec![not(var("a")), not(var("b")), not(var("c"))];
    let v = feas(&s, all_neg);
    assert_eq!(decision(&v), "UNSAT");
    assert!(v["core"].as_array().is_some_and(|c| !c.is_empty()));
    assert_eq!(decision(&feas(&s, vec![var("a"), var("b"), var("c")])), "UNSAT");
    // Witness covers the query support (a, b); it must leave room for c under exactly-one.
    let w = feas(&s, vec![not(var("a")), not(var("b"))]);
    assert_eq!(w["witness"], json!({"a": false, "b": false}));
}

#[test]
fn unresolved_atom_keeps_boolean_identity() {
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("P")]);
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("P")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["P"]);
    assert!(labels(&v, "UNRESOLVED").is_empty());
    assert_eq!(decision(&feas(&s, vec![var("P"), not(var("P"))])), "UNSAT");
    assert_eq!(decision(&feas(&s, vec![var("P")])), "SAT");
}

#[test]
fn cardinality_atmost_atleast_exactly() {
    let ids = ["a", "b", "c", "d"];
    let args: Vec<Value> = ids.iter().map(|i| var(i)).collect();
    let bind: Vec<Value> = ids.iter().map(|i| unresolved(i)).collect();

    let at_most = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "atmost", "k": 2, "args": args}))]),
        bind.clone(),
    );
    assert_eq!(decision(&feas(&at_most, vec![var("a"), var("b")])), "SAT");
    assert_eq!(decision(&feas(&at_most, vec![var("a"), var("b"), var("c")])), "UNSAT");

    let at_least = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "atleast", "k": 3, "args": args}))]),
        bind.clone(),
    );
    assert_eq!(decision(&feas(&at_least, vec![not(var("a")), not(var("b"))])), "UNSAT");
    assert_eq!(decision(&feas(&at_least, vec![not(var("a"))])), "SAT");

    let exactly = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "exactly", "k": 2, "args": args}))]),
        bind,
    );
    assert_eq!(decision(&feas(&exactly, vec![var("a"), var("b"), var("c")])), "UNSAT");
    assert_eq!(
        decision(&feas(&exactly, vec![var("a"), not(var("b")), not(var("c")), not(var("d"))])),
        "UNSAT"
    );
    assert_eq!(
        decision(&feas(&exactly, vec![var("a"), var("b"), not(var("c")), not(var("d"))])),
        "SAT"
    );
}

#[test]
fn wsum_bounds() {
    // 2a + 3b - c <= 3
    let terms = json!([
        {"w": 2, "e": var("a")},
        {"w": 3, "e": var("b")},
        {"w": -1, "e": var("c")}
    ]);
    let bind = vec![unresolved("a"), unresolved("b"), unresolved("c")];
    let le = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "wsum", "cmp": "le", "k": 3, "terms": terms}))]),
        bind.clone(),
    );
    assert_eq!(decision(&feas(&le, vec![var("a"), var("b")])), "UNSAT");
    assert_eq!(decision(&feas(&le, vec![var("a"), not(var("b")), var("c")])), "SAT");
    assert_eq!(decision(&feas(&le, vec![var("a"), var("b"), not(var("c"))])), "UNSAT");

    let ge = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "wsum", "cmp": "ge", "k": 4, "terms": terms}))]),
        bind.clone(),
    );
    assert_eq!(decision(&feas(&ge, vec![not(var("a")), var("b"), not(var("c"))])), "UNSAT");
    assert_eq!(decision(&feas(&ge, vec![var("a"), var("b"), not(var("c"))])), "SAT");

    let eq = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "wsum", "cmp": "eq", "k": 2, "terms": terms}))]),
        bind,
    );
    assert_eq!(decision(&feas(&eq, vec![var("a"), not(var("b")), not(var("c"))])), "SAT");
    assert_eq!(decision(&feas(&eq, vec![not(var("a")), not(var("b")), var("c")])), "UNSAT");
}

#[test]
fn implication_cycles_are_allowed() {
    let s = sub(
        &["base"],
        json!({}),
        json!([
            cons("C1", json!({"op": "implies", "a": var("a"), "b": var("b")})),
            cons("C2", json!({"op": "implies", "a": var("b"), "b": var("a")}))
        ]),
        vec![unresolved("a"), unresolved("b")],
    );
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("a")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["a", "b"]);
}

#[test]
fn multi_interpretation_disagreement_is_unresolved() {
    let s = sub(
        &["i1", "i2"],
        json!({}),
        json!([
            {"id": "C1", "expr": var("a"), "interps": ["i1"], "provenance": ["R1"]},
            {"id": "C2", "expr": not(var("a")), "interps": ["i2"], "provenance": ["R1"]}
        ]),
        vec![unresolved("a")],
    );
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": []}));
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["a"]);
    assert!(labels(&v, "ENTAILED").is_empty() && labels(&v, "REFUTED").is_empty());
    // Only i1 survives H = {a}, so the disagreement disappears.
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("a")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["a"]);
}

#[test]
fn entailed_beats_unresolved_and_hyp_inconsistent() {
    let s = sub(
        &["base"],
        json!({"A": var("y")}),
        json!([]),
        vec![bound("x", "A"), unresolved("y")],
    );
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": []}));
    assert_eq!(labels(&v, "CONTINGENT"), vec!["x"]);
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["y"]);
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("y")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["x", "y"]);
    assert!(labels(&v, "UNRESOLVED").is_empty());
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("y"), not(var("y"))]}));
    assert_eq!(v["error"], json!("hyp_inconsistent"));
    assert!(v["labels"].is_null());
}

#[test]
fn selection_relations() {
    // p1 => p2 through the aliases u and v, with u => v in the theory.
    let s = sub(
        &["base"],
        json!({"A1": var("u"), "A2": var("v")}),
        json!([cons("C1", json!({"op": "implies", "a": var("u"), "b": var("v")}))]),
        vec![
            bound("p1", "A1"),
            bound("p2", "A2"),
            unresolved("u"),
            unresolved("v"),
        ],
    );
    let sel = |rel: &str| {
        run(
            &s,
            json!({"qid": "s", "family": "selection", "relation": rel, "target": var("p2")}),
        )["ids"]
            .clone()
    };
    assert_eq!(sel("implies"), json!(["p1", "p2"]));
    assert_eq!(sel("implied_by"), json!(["p2"]));
    assert_eq!(sel("excludes"), json!([]));
    assert_eq!(sel("equiv"), json!(["p2"]));
}

#[test]
fn partition_claims() {
    let bind = vec![unresolved("a"), unresolved("b")];
    let free = sub(&["base"], json!({}), json!([]), bind.clone());
    let v = run(
        &free,
        json!({"qid": "p", "family": "partition", "members": [var("a"), var("b")], "claim": "exclusive", "target": null}),
    );
    assert_eq!(v["decision"], json!("false"));
    assert_eq!(v["counterexample"]["a"], json!(true));
    assert_eq!(v["counterexample"]["b"], json!(true));

    let atmost1 = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "atmost", "k": 1, "args": [var("a"), var("b")]}))]),
        bind.clone(),
    );
    let v = run(
        &atmost1,
        json!({"qid": "p", "family": "partition", "members": [var("a"), var("b")], "claim": "exclusive", "target": null}),
    );
    assert_eq!(v["decision"], json!("true"));

    let v = run(
        &free,
        json!({"qid": "p", "family": "partition", "members": [var("a"), var("b")], "claim": "exhaustive", "target": null}),
    );
    assert_eq!(v["decision"], json!("false"));
    assert_eq!(v["counterexample"]["a"], json!(false));
    assert_eq!(v["counterexample"]["b"], json!(false));
}

#[test]
fn check_certifies_engine_answers_and_rejects_tampering() {
    let s = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "exactly", "k": 1, "args": [var("a"), var("b"), var("c")]}))]),
        vec![unresolved("a"), unresolved("b"), unresolved("c")],
    );
    let raw = vec![
        json!({"qid": "f1", "family": "feasibility", "assume": [not(var("a")), not(var("b"))]}),
        json!({"qid": "f2", "family": "feasibility", "assume": [var("a"), var("b"), var("c")]}),
        json!({"qid": "f3", "family": "feasibility", "assume": [var("a")]}),
        json!({"qid": "p1", "family": "partition", "members": [var("a"), var("b")], "claim": "exclusive", "target": null}),
        json!({"qid": "p2", "family": "partition", "members": [var("a"), var("b"), var("c")], "claim": "exhaustive", "target": null}),
    ];
    let queries: Vec<Query> = raw.iter().map(|q| serde_json::from_value(q.clone()).unwrap()).collect();
    let mut e = World::new(&s);
    let answers: Vec<AnswerIn> = queries
        .iter()
        .map(|q| serde_json::from_value(serde_json::to_value(e.answer(q)).unwrap()).unwrap())
        .collect();
    let by_qid: HashMap<String, AnswerIn> = answers.into_iter().map(|a| (a.qid.clone(), a)).collect();
    let verdicts = check(&mut e, &queries, &by_qid);
    for v in &verdicts {
        assert!(v.cert_valid, "{v:?}");
    }
    // A witness that breaks exactly-one must not certify.
    let mut bad = by_qid["f1"].clone();
    bad.witness.as_mut().unwrap().insert("c".into(), false);
    let mut tampered = by_qid.clone();
    tampered.insert("f1".into(), bad);
    let verdicts = check(&mut e, &queries, &tampered);
    assert!(!verdicts[0].cert_valid, "{:?}", verdicts[0]);
}

#[test]
fn feasibility_witness_is_per_interpretation_completion() {
    // i1 needs a, i2 needs not-a. Off-support atoms may complete differently per interpretation.
    let s = sub(
        &["i1", "i2"],
        json!({}),
        json!([
            {"id": "C1", "expr": var("a"), "interps": ["i1"], "provenance": ["R1"]},
            {"id": "C2", "expr": not(var("a")), "interps": ["i2"], "provenance": ["R1"]}
        ]),
        vec![unresolved("a"), unresolved("b")],
    );
    let v = feas(&s, vec![var("b")]);
    assert_eq!(decision(&v), "SAT");
    assert_eq!(v["witness"], json!({"b": true}));
    // a is on the support, so both interpretations would need a shared value of a.
    assert_eq!(decision(&feas(&s, vec![var("a")])), "UNRESOLVED");
    assert_eq!(decision(&feas(&s, vec![var("a"), var("b")])), "UNRESOLVED");
}

#[test]
fn inconsistent_interpretation_is_detected_not_dropped() {
    // base forces x; alt forces not-x. The submission is invalid, not "x entailed".
    let s = sub(
        &["base", "alt"],
        json!({}),
        json!([
            cons("C1", var("x")),
            {"id": "C2", "expr": not(var("x")), "interps": ["alt"], "provenance": ["R1"]}
        ]),
        vec![unresolved("x")],
    );
    let mut e = World::new(&s);
    assert_eq!(e.inconsistent_interpretation(false).unwrap(), Some("alt".to_string()));
    // Consistent submissions report no interpretation.
    let ok = sub(&["base"], json!({}), json!([cons("C1", var("x"))]), vec![unresolved("x")]);
    assert_eq!(World::new(&ok).inconsistent_interpretation(true).unwrap(), None);
}

#[test]
fn alias_refs_inside_queries_are_rejected() {
    let s = sub(
        &["base"],
        json!({"A": var("x")}),
        json!([]),
        vec![bound("y", "A"), unresolved("x")],
    );
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [{"op": "ref", "name": "A"}]}));
    assert_eq!(v, json!({"qid": "c", "error": "ref_in_query"}));
    let v = run(&s, json!({"qid": "f", "family": "feasibility", "assume": [{"op": "ref", "name": "A"}]}));
    assert_eq!(v["error"], json!("ref_in_query"));
}

#[test]
fn check_reasons_are_coarse() {
    let s = sub(
        &["base"],
        json!({}),
        json!([cons("C1", json!({"op": "exactly", "k": 1, "args": [var("a"), var("b")]}))]),
        vec![unresolved("a"), unresolved("b")],
    );
    let q: Query = serde_json::from_value(json!({"qid": "f", "family": "feasibility", "assume": [var("a"), var("b")]})).unwrap();
    let mut e = World::new(&s);
    // Forged UNSAT claim whose core is satisfiable: must fail with a coarse reason.
    let forged: AnswerIn = serde_json::from_value(json!({"qid": "f", "decision": "UNSAT", "core": [0]})).unwrap();
    let forged = HashMap::from([("f".to_string(), forged)]);
    let v = check(&mut e, std::slice::from_ref(&q), &forged);
    assert!(!v[0].cert_valid);
    assert_eq!(v[0].reason, "core_not_unsat");
    // Forged witness: reason is coarse and names no interpretation or ID.
    let forged: AnswerIn = serde_json::from_value(json!({"qid": "f", "decision": "SAT", "witness": {"zz": true}})).unwrap();
    let forged = HashMap::from([("f".to_string(), forged)]);
    let v = check(&mut e, std::slice::from_ref(&q), &forged);
    assert_eq!(v[0].reason, "witness_invalid");
    // A query that uses a ref is rejected by check as well as by eval.
    let rq: Query = serde_json::from_value(json!({"qid": "r", "family": "feasibility", "assume": [{"op": "ref", "name": "A"}]})).unwrap();
    let v = check(&mut e, &[rq], &HashMap::new());
    assert_eq!(v[0].reason, "query_invalid");
}

#[test]
fn deep_alias_chain_does_not_overflow() {
    // 200k aliases in one chain: validation, encoding and query evaluation are all iterative
    // in alias depth, so this must not blow the (default 2 MiB) test-thread stack.
    const N: usize = 200_000;
    let mut aliases = serde_json::Map::new();
    aliases.insert("A0".into(), var("x"));
    for i in 1..N {
        aliases.insert(format!("A{i}"), json!({"op": "ref", "name": format!("A{}", i - 1)}));
    }
    let s = sub(
        &["base"],
        Value::Object(aliases),
        json!([]),
        vec![unresolved("x"), bound("y", &format!("A{}", N - 1))],
    );
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": []}));
    assert_eq!(labels(&v, "CONTINGENT"), vec!["y"]);
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["x"]);
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("y")]}));
    // y is bound to A = x, so forcing y forces x as well.
    assert_eq!(labels(&v, "ENTAILED"), vec!["x", "y"]);
    assert!(labels(&v, "UNRESOLVED").is_empty());
}

#[test]
fn independent_components_select_and_close_like_one_solver() {
    // {a => b}, {c => d}, {z is false}, {k is true}, {u free}: z and k relate to the b target only
    // through their constants, which is the cross-component path.
    let s = sub(
        &["base"],
        json!({}),
        s_constraints(),
        ["a", "b", "c", "d", "z", "k", "u"].iter().map(|i| unresolved(i)).collect(),
    );
    let sel = |rel: &str| {
        let v = run(&s, json!({"qid": "s", "family": "selection", "relation": rel, "target": var("b")}));
        let mut ids: Vec<String> = serde_json::from_value(v["ids"].clone()).unwrap();
        ids.sort();
        ids
    };
    // Unresolved atoms never qualify for selection; the differential assertion in `run` is the point.
    assert!(sel("implies").is_empty());
    // Bound variant: z (always false) and k (always true) qualify across components.
    let aliases: Value = ["a", "b", "c", "d", "z", "k", "u"].iter().map(|i| (i.to_uppercase(), var(i))).collect::<serde_json::Map<_, _>>().into();
    let bs = sub(
        &["base"],
        aliases,
        s_constraints(),
        ["a", "b", "c", "d", "z", "k", "u"].iter().map(|i| bound(i, &i.to_uppercase())).collect(),
    );
    let bsel = |rel: &str| {
        let v = run(&bs, json!({"qid": "s", "family": "selection", "relation": rel, "target": var("b")}));
        let mut ids: Vec<String> = serde_json::from_value(v["ids"].clone()).unwrap();
        ids.sort();
        ids
    };
    assert_eq!(bsel("implies"), vec!["a", "b", "z"]);
    assert_eq!(bsel("implied_by"), vec!["b", "k"]);
    assert_eq!(bsel("excludes"), vec!["z"]);
    assert_eq!(bsel("equiv"), vec!["b"]);
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("a")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["a", "b", "k"]);
    assert_eq!(labels(&v, "REFUTED"), vec!["z"]);
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["c", "d", "u"]);
}

#[test]
fn closure_hypothesis_groups_answer_like_one_engine() {
    // {a => b}, {c => d}, {z false}, {k true}, {u free}: `run` checks every answer against one engine.
    let s = sub(
        &["base"],
        json!({}),
        s_constraints(),
        ["a", "b", "c", "d", "z", "k", "u"].iter().map(|i| unresolved(i)).collect(),
    );
    // One item per group ({a,b} and {c,d}).
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("a"), var("c")]}));
    assert_eq!(labels(&v, "ENTAILED"), vec!["a", "b", "c", "d", "k"]);
    assert_eq!(labels(&v, "REFUTED"), vec!["z"]);
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["u"]);
    // One item joining both groups; a constant item is a group of its own.
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [{"op": "or", "args": [var("a"), var("c")]}, {"op": "true"}]}));
    assert!(v["error"].is_null(), "{v}");
    // An inconsistent group makes the whole hypothesis inconsistent.
    let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("c"), var("a"), not(var("b"))]}));
    assert_eq!(v["error"], json!("hyp_inconsistent"));
}

/// Two interpretations: C1 `a => b` holds in i1 only, C2 `not c` in i2 only. Bindings a, b, c, d are unresolved.
fn two_interp_sub(extra_cons: Vec<Value>, extra_bindings: Vec<Value>) -> Submission {
    let mut cs = vec![
        json!({"id": "C1", "expr": {"op": "implies", "a": var("a"), "b": var("b")}, "interps": ["i1"], "provenance": ["R1"]}),
        json!({"id": "C2", "expr": not(var("c")), "interps": ["i2"], "provenance": ["R1"]}),
    ];
    cs.extend(extra_cons);
    let mut bs: Vec<Value> = ["a", "b", "c", "d"].iter().map(|i| unresolved(i)).collect();
    bs.extend(extra_bindings);
    sub(&["i1", "i2"], json!({}), Value::Array(cs), bs)
}

fn closure_q(hyp: Value) -> Value {
    json!({"qid": "c", "family": "closure", "hyp": hyp})
}

/// Asserts the labels of a closure answer: entailed and unresolved as given, nothing refuted or contingent.
fn assert_labels(v: &Value, entailed: &[&str], unresolved: &[&str]) {
    assert_eq!(labels(v, "ENTAILED"), entailed, "{v}");
    assert_eq!(labels(v, "UNRESOLVED"), unresolved, "{v}");
    assert!(labels(v, "REFUTED").is_empty() && labels(v, "CONTINGENT").is_empty(), "{v}");
}

#[test]
fn cross_group_interpretation_survival_is_per_interpretation() {
    // Hand-derived: hyp [a] keeps i1 and i2 (both force a); b differs between them, c is refuted by i2 only.
    let s = two_interp_sub(vec![], vec![]);
    let v = run(&s, closure_q(json!([var("a")])));
    assert_labels(&v, &["a"], &["b", "c", "d"]);
    // hyp [c] kills i2 (not c), so i1 alone answers: a is free in i1.
    let v = run(&s, closure_q(json!([var("c")])));
    assert_labels(&v, &["c"], &["a", "b", "d"]);
    // hyp [a, c] is two groups; i2 dies, i1 alone entails b.
    let v = run(&s, closure_q(json!([var("a"), var("c")])));
    assert_labels(&v, &["a", "b", "c"], &["d"]);
    // With `not a` in i1 too: [a, c] kills both interpretations; [a] answers from i2 only.
    let s3 = two_interp_sub(vec![json!({"id": "C3", "expr": not(var("a")), "interps": ["i1"], "provenance": ["R1"]})], vec![]);
    let v = run(&s3, closure_q(json!([var("a"), var("c")])));
    assert_eq!(v["error"], json!("hyp_inconsistent"));
    let v = run(&s3, closure_q(json!([var("a")])));
    assert_eq!(labels(&v, "ENTAILED"), vec!["a"]);
    assert_eq!(labels(&v, "REFUTED"), vec!["c"]);
    assert_eq!(labels(&v, "UNRESOLVED"), vec!["b", "d"]);
}

#[test]
fn global_constraints_leave_group_answers_unchanged() {
    // Globals mention no outcome and hold in every surviving interpretation (base proves it), so the group engines,
    // which omit them, must answer as if they were there.
    let plain = two_interp_sub(vec![], vec![]);
    let with_globals = two_interp_sub(
        vec![
            json!({"id": "G1", "expr": {"op": "true"}, "interps": ["i1"], "provenance": ["R1"]}),
            json!({"id": "G2", "expr": {"op": "or", "args": [{"op": "true"}, {"op": "false"}]}, "interps": null, "provenance": ["R1"]}),
        ],
        vec![],
    );
    for hyp in [json!([var("a")]), json!([var("c")]), json!([var("a"), var("c")]), json!([var("b"), not(var("d"))])] {
        let want = run(&plain, closure_q(hyp.clone()));
        assert_eq!(run(&with_globals, closure_q(hyp)), want);
    }
    let v = run(&with_globals, closure_q(json!([var("a")])));
    assert_labels(&v, &["a"], &["b", "c", "d"]);
}

#[test]
fn base_classes_of_untouched_ids_merge_across_survivors() {
    // e is entailed in both interpretations by base, f only in i1: the merge of untouched IDs must agree per
    // interpretation, so f is unresolved whenever both survive and entailed when i1 alone survives.
    let extra_cons = vec![
        json!({"id": "C4", "expr": var("e"), "interps": null, "provenance": ["R1"]}),
        json!({"id": "C5", "expr": var("f"), "interps": ["i1"], "provenance": ["R1"]}),
    ];
    let s = two_interp_sub(extra_cons, vec![unresolved("e"), unresolved("f")]);
    let v = run(&s, closure_q(json!([var("a")])));
    assert_labels(&v, &["a", "e"], &["b", "c", "d", "f"]);
    let v = run(&s, closure_q(json!([var("c")])));
    assert_labels(&v, &["c", "e", "f"], &["a", "b", "d"]);
}

/// Random submission over two interpretations. Unresolved bindings x{i} and u0, u1; some bound bindings share a class
/// alias (K0 = u0, K1 = u1); constraints mix literals, implications, alias references (`ref`) and gating to a random
/// interpretation subset (or all). None when invalid.
fn random_two_interp_sub(rng: &mut Lcg) -> Option<Submission> {
    let nb = 3 + rng.below(5) as usize;
    let mut aliases = serde_json::Map::new();
    aliases.insert("K0".into(), var("u0"));
    aliases.insert("K1".into(), var("u1"));
    let mut bindings = vec![unresolved("u0"), unresolved("u1")];
    let mut ids = vec!["u0".to_string(), "u1".to_string()];
    for i in 0..nb {
        ids.push(format!("x{i}"));
        if rng.below(2) == 0 {
            let class = format!("K{}", rng.below(2));
            bindings.push(json!({"id": format!("x{i}"), "status": "bound", "class": class, "evidence": ["R1"]}));
        } else {
            bindings.push(unresolved(&format!("x{i}")));
        }
    }
    let lit = |rng: &mut Lcg, ids: &[String]| {
        let x = var(&ids[rng.below(ids.len() as u64) as usize]);
        if rng.below(2) == 0 { x } else { not(x) }
    };
    let mut constraints = Vec::new();
    for j in 0..1 + rng.below(6) {
        let expr = match rng.below(5) {
            0 => lit(rng, &ids),
            1 => json!({"op": "implies", "a": lit(rng, &ids), "b": lit(rng, &ids)}),
            2 => json!({"op": "or", "args": [lit(rng, &ids), lit(rng, &ids)]}),
            3 => json!({"op": "ref", "name": format!("K{}", rng.below(2))}),
            _ => json!({"op": "implies", "a": lit(rng, &ids), "b": {"op": "ref", "name": format!("K{}", rng.below(2))}}),
        };
        let interps = match rng.below(4) {
            0 => json!(["i1"]),
            1 => json!(["i2"]),
            2 => json!(["i1", "i2"]),
            _ => Value::Null,
        };
        constraints.push(json!({"id": format!("C{j}"), "expr": expr, "interps": interps, "provenance": ["R1"]}));
    }
    if rng.below(3) == 0 {
        constraints.push(json!({"id": "G", "expr": {"op": "or", "args": [{"op": "true"}, {"op": "false"}]}, "interps": ["i2"], "provenance": ["R1"]}));
    }
    let theory: Theory =
        serde_json::from_value(json!({"interpretations": ["i1", "i2"], "aliases": aliases, "constraints": constraints})).unwrap();
    let bindings: Vec<Binding> = bindings.into_iter().map(|b| serde_json::from_value(b).unwrap()).collect();
    let provenance = vec![Provenance { reference: "R1".into(), kind: Kind::Source, record: "m1".into(), field: "question".into() }];
    let s = Submission { bindings, theory, provenance, bytes: 0 };
    validate(&s, None).is_empty().then_some(s)
}

#[test]
fn random_two_interpretation_closures_match_one_engine() {
    use crate::world::EARLY_RETURNS;
    EARLY_RETURNS.with(|c| c.set(0));
    // Coverage counters, each asserted nonzero: multi-group hypotheses, hypotheses that drop an interpretation,
    // two survivors with untouched IDs, alias references in constraints, shared class aliases, constant hypothesis
    // items (true and false), universe subsets, and early returns from pass one.
    let (mut compared, mut seeds, mut multi, mut dropped, mut untouched_two) = (0, 0, 0, 0, 0);
    let (mut with_ref, mut shared, mut const_false, mut const_true, mut subset) = (0, 0, 0, 0, 0);
    for seed in 0..120u64 {
        let mut rng = Lcg(seed.wrapping_mul(7919).wrapping_add(11));
        let Some(s) = random_two_interp_sub(&mut rng) else { continue };
        if World::new(&s).inconsistent_interpretation(false).unwrap().is_some() {
            continue;
        }
        seeds += 1;
        with_ref += usize::from(s.theory.constraints.iter().any(|c| c.expr.contains_ref()));
        shared += usize::from(["K0", "K1"].iter().any(|k| s.bindings.iter().filter(|b| b.class.as_deref() == Some(k)).count() >= 2));
        let all_ids: Vec<String> = s.bindings.iter().map(|b| b.id.clone()).collect();
        for _ in 0..3 {
            // 1 to 4 items: a literal, a disjunction of two literals, or a constant true or false item.
            let n_items = 1 + rng.below(4);
            let lit = |rng: &mut Lcg| {
                let x = var(&all_ids[rng.below(all_ids.len() as u64) as usize]);
                if rng.below(2) == 0 { x } else { not(x) }
            };
            let hyp: Vec<Value> = (0..n_items)
                .map(|_| match rng.below(12) {
                    0 => json!({"op": "false"}),
                    1 => json!({"op": "true"}),
                    2..=4 => json!({"op": "or", "args": [lit(&mut rng), lit(&mut rng)]}),
                    _ => lit(&mut rng),
                })
                .collect();
            const_false += usize::from(hyp.iter().any(|h| h == &json!({"op": "false"})));
            const_true += usize::from(hyp.iter().any(|h| h == &json!({"op": "true"})));
            // Universe: none, or a random permutation truncated to a random non-empty prefix (a subset).
            let universe = if rng.below(2) == 0 {
                None
            } else {
                let mut u = all_ids.clone();
                for i in (1..u.len()).rev() {
                    u.swap(i, rng.below(i as u64 + 1) as usize);
                }
                u.truncate(1 + rng.below(u.len() as u64) as usize);
                subset += usize::from(u.len() < all_ids.len());
                Some(u)
            };
            let mut q_json = json!({"qid": "c", "family": "closure", "hyp": hyp});
            if let Some(u) = &universe {
                q_json["universe"] = json!(u);
            }
            let q: Query = serde_json::from_value(q_json.clone()).unwrap();
            let QueryBody::Closure { hyp: exprs } = &q.body else { unreachable!() };
            let w = World::new(&s);
            let groups = w.hyp_groups(exprs).expect("known IDs").len();
            let mut e = Engine::new(&s).unwrap();
            let hl = e.lits(exprs).unwrap();
            let alive = (0..2).filter(|&i| {
                let mut a = e.interp_assumptions(i);
                a.extend_from_slice(&hl);
                e.sat(&a).unwrap()
            }).count();
            multi += usize::from(groups >= 2);
            dropped += usize::from(alive == 1);
            untouched_two += usize::from(alive == 2 && w.untouched_count(exprs) > 0);
            run(&s, q_json);
            compared += 1;
        }
    }
    let early = EARLY_RETURNS.with(|c| c.get());
    assert!(seeds >= 40, "only {seeds} valid seeds");
    assert!(compared >= 100, "only {compared} closures compared");
    let counters = [
        ("multi", multi), ("dropped", dropped), ("untouched_two", untouched_two), ("with_ref", with_ref),
        ("shared", shared), ("const_false", const_false), ("const_true", const_true), ("subset", subset), ("early", early),
    ];
    for (name, n) in counters {
        assert!(n > 0, "coverage counter {name} is zero: {counters:?}");
    }
}

/// A global `false` gated to i1 makes i1's base theory unsatisfiable. World must answer the base error, not labels, for
/// the first query and for every later query on the same World (eval answers all queries with one World, and a base
/// stored despite a bad interpretation would answer the later ones with labels). This asserts World's own answers, not
/// `run`. Without bindings only the None component engine sees the global, so the second case also catches a skipped
/// None component.
#[test]
fn global_false_gated_to_one_interpretation_is_inconsistent_base() {
    let g = json!({"id": "G", "expr": {"op": "false"}, "interps": ["i1"], "provenance": ["R1"]});
    let s = two_interp_sub(vec![g.clone()], vec![]);
    let mut w = World::new(&s);
    for hyp in [json!([var("a")]), json!([var("c")]), json!([])] {
        let q: Query = serde_json::from_value(closure_q(hyp)).unwrap();
        let v = serde_json::to_value(w.answer(&q)).unwrap();
        assert_eq!(v["error"], json!("inconsistent_base"), "{v}");
        assert!(v["labels"].is_null(), "{v}");
    }
    let s0 = sub(&["i1", "i2"], json!({}), json!([g]), vec![]);
    let mut w0 = World::new(&s0);
    for _ in 0..2 {
        let q0: Query = serde_json::from_value(closure_q(json!([]))).unwrap();
        let v0 = serde_json::to_value(w0.answer(&q0)).unwrap();
        assert_eq!(v0["error"], json!("inconsistent_base"), "{v0}");
        assert!(v0["labels"].is_null(), "{v0}");
    }
}

#[test]
fn zero_query_timeout_answers_timeout_error() {
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("a")]);
    let q: Query = serde_json::from_value(json!({"qid": "f", "family": "feasibility", "assume": [var("a")]})).unwrap();
    let mut w = World::new(&s);
    w.set_query_timeout(Some(std::time::Duration::ZERO));
    let v = serde_json::to_value(w.answer(&q)).unwrap();
    assert_eq!(v, json!({"qid": "f", "error": "timeout"}));
    w.set_query_timeout(None);
    assert_eq!(serde_json::to_value(w.answer(&q)).unwrap()["decision"], json!("SAT"));
}

fn s_constraints() -> Value {
    json!([
        cons("C1", json!({"op": "implies", "a": var("a"), "b": var("b")})),
        cons("C2", json!({"op": "implies", "a": var("c"), "b": var("d")})),
        cons("C3", not(var("z"))),
        cons("C4", var("k")),
    ])
}

#[test]
fn cached_cover_engine_is_reused_and_answers_match() {
    // Query 1 touches {a,b} + {c,d}; query 2 touches only {a,b}: served by the cached superset engine.
    let s = sub(&["base"], json!({}), s_constraints(),
        ["a", "b", "c", "d", "z", "k", "u"].iter().map(|i| unresolved(i)).collect());
    let mut w = World::new(&s);
    let q1: Query = serde_json::from_value(json!({"qid": "1", "family": "feasibility", "assume": [var("a"), var("c")]})).unwrap();
    let q2: Query = serde_json::from_value(json!({"qid": "2", "family": "feasibility", "assume": [var("a"), not(var("b"))]})).unwrap();
    assert_eq!(serde_json::to_value(w.answer(&q1)).unwrap()["decision"], json!("SAT"));
    let v = serde_json::to_value(w.answer(&q2)).unwrap();
    assert_eq!(v["decision"], json!("UNSAT"));
    assert_eq!(w.cached_engines(), 1);
}

/// Verifies one engine answer with `check` (the answer goes through the wire format).
fn verdict(s: &Submission, q: Value, answer: Value) -> (bool, String) {
    let q: Query = serde_json::from_value(q).unwrap();
    let a: AnswerIn = serde_json::from_value(answer).unwrap();
    let mut w = World::new(s);
    let qid = q.qid.clone();
    let v = check(&mut w, std::slice::from_ref(&q), &HashMap::from([(qid, a)]));
    (v[0].cert_valid, v[0].reason.clone())
}

/// Two interpretations that disagree on `x`, `y`, `z` such that no single joint assignment
/// satisfies both: i0 = x & y & !z, i1 = !x & y & z.
fn no_joint_sub() -> Submission {
    sub(
        &["i0", "i1"],
        json!({}),
        json!([
            {"id": "C1", "expr": {"op": "and", "args": [var("x"), var("y"), not(var("z"))]}, "interps": ["i0"], "provenance": ["R1"]},
            {"id": "C2", "expr": {"op": "and", "args": [not(var("x")), var("y"), var("z")]}, "interps": ["i1"], "provenance": ["R1"]}
        ]),
        vec![unresolved("x"), unresolved("y"), unresolved("z")],
    )
}

#[test]
fn no_joint_model_gives_maximal_partial_witness_and_counterexample() {
    // Every interpretation is satisfiable, but no single assignment works in both: D-A says SAT
    // with the maximal partial witness, and "false" with the maximal partial counterexample.
    let s = no_joint_sub();
    // x is fixed by neither polarity; y is fixed to true (both interpretations need it).
    let v = run(&s, json!({"qid": "f", "family": "feasibility", "assume": [{"op": "or", "args": [var("x"), not(var("x"))]}]}));
    assert_eq!(decision(&v), "SAT");
    assert_eq!(v["witness"], json!({}));
    let v = run(&s, json!({"qid": "p", "family": "partition", "members": [var("x"), var("y"), var("z")], "claim": "exclusive", "target": null}));
    assert_eq!(decision(&v), "false");
    assert_eq!(v["counterexample"], json!({"y": true}));
    // Support IDs that a joint model can fix are kept (the witness is over the support only).
    let v = run(&s, json!({"qid": "f", "family": "feasibility", "assume": [var("y")]}));
    assert_eq!(decision(&v), "SAT");
    assert_eq!(v["witness"], json!({"y": true}));
}

#[test]
fn check_certifies_maximal_partial_witnesses_only() {
    let s = no_joint_sub();
    let assume = json!([var("y")]);
    let q = json!({"qid": "f", "family": "feasibility", "assume": assume});
    // {} is not maximal: y can be fixed to true in both interpretations.
    assert_eq!(verdict(&s, q.clone(), json!({"qid": "f", "decision": "SAT", "witness": {}})).1, "witness_not_maximal");
    assert_eq!(verdict(&s, q.clone(), json!({"qid": "f", "decision": "SAT", "witness": {"y": true}})), (true, "ok".into()));
    assert_eq!(verdict(&s, q, json!({"qid": "f", "decision": "SAT", "witness": {"y": false}})).1, "witness_invalid");
    // Partition counterexample {} is not maximal either.
    let p = json!({"qid": "p", "family": "partition", "members": [var("x"), var("y"), var("z")], "claim": "exclusive", "target": null});
    assert_eq!(verdict(&s, p.clone(), json!({"qid": "p", "decision": "false", "counterexample": {}})).1, "counterexample_not_maximal");
    assert_eq!(verdict(&s, p, json!({"qid": "p", "decision": "false", "counterexample": {"y": true}})), (true, "ok".into()));
}

#[test]
fn unresolved_decisions_must_name_gold_missing_codes() {
    // i1 needs x, i2 needs !x: UNRESOLVED over {x}, whose gold missing code is no_date.
    let s = sub(
        &["i1", "i2"],
        json!({}),
        json!([
            {"id": "C1", "expr": var("x"), "interps": ["i1"], "provenance": ["R1"]},
            {"id": "C2", "expr": not(var("x")), "interps": ["i2"], "provenance": ["R1"]}
        ]),
        vec![unresolved("x")],
    );
    let q = json!({"qid": "f", "family": "feasibility", "assume": [var("x")]});
    let v = run(&s, q.clone());
    assert_eq!(decision(&v), "UNRESOLVED");
    assert_eq!(v["missing"], json!(["no_date"]));
    let ans = |missing: Value| json!({"qid": "f", "decision": "UNRESOLVED", "missing": missing});
    assert_eq!(verdict(&s, q.clone(), ans(json!(["no_date"]))), (true, "ok".into()));
    assert_eq!(verdict(&s, q.clone(), ans(json!([]))).1, "missing_invalid");
    assert_eq!(verdict(&s, q, ans(json!(["no_source"]))).1, "missing_invalid");
}

#[test]
fn check_rejects_query_level_overflow_and_reports_closure_errors_as_uncertified() {
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("a"), unresolved("b")]);
    let big = json!([{"w": 1i64 << 62, "e": var("a")}, {"w": 1i64 << 62, "e": var("b")}]);
    let q = json!({"qid": "f", "family": "feasibility", "assume": [{"op": "wsum", "cmp": "le", "k": 0, "terms": big}]});
    // The engine answers a query-level error; check treats an unanswerable query as invalid.
    let mut w = World::new(&s);
    let qq: Query = serde_json::from_value(q.clone()).unwrap();
    let a = serde_json::to_value(w.answer(&qq)).unwrap();
    assert_eq!(a, json!({"qid": "f", "error": "weight_overflow"}));
    assert_eq!(verdict(&s, q, json!({"qid": "f", "decision": "SAT", "witness": {}})).1, "query_invalid");
    let c = json!({"qid": "c", "family": "closure", "hyp": []});
    assert_eq!(verdict(&s, c, json!({"qid": "c", "labels": null, "error": "timeout"})).1, "family_not_certified");
}

#[test]
fn check_applies_query_timeout() {
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("a")]);
    let q: Query = serde_json::from_value(json!({"qid": "f", "family": "feasibility", "assume": [var("a")]})).unwrap();
    let mut w = World::new(&s);
    w.set_query_timeout(Some(std::time::Duration::ZERO));
    let a: AnswerIn = serde_json::from_value(json!({"qid": "f", "decision": "SAT", "witness": {"a": true}})).unwrap();
    let v = check(&mut w, std::slice::from_ref(&q), &HashMap::from([("f".to_string(), a)]));
    assert_eq!(v[0].reason, "timeout");
    assert!(!v[0].cert_valid);
}

#[test]
fn deep_expressions_are_answered_without_overflowing_the_stack() {
    // A 2k-level negation chain over `x`, evaluated on a thread with room for the release-sized
    // recursion of the encoder (the test thread's default stack is too small in debug builds).
    std::thread::Builder::new()
        .stack_size(1 << 30)
        .spawn(|| {
            const N: usize = 2_000;
            let mut e = var("x");
            for _ in 0..N {
                e = json!({"op": "not", "a": e});
            }
            let s = sub(&["base"], json!({"A": e}), json!([]), vec![unresolved("x"), bound("y", "A")]);
            let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": []}));
            assert_eq!(labels(&v, "CONTINGENT"), vec!["y"]);
            assert_eq!(labels(&v, "UNRESOLVED"), vec!["x"]);
            let v = run(&s, json!({"qid": "c", "family": "closure", "hyp": [var("y")]}));
            // y <-> A and A = x (N is even): forcing y forces x.
            assert_eq!(labels(&v, "ENTAILED"), vec!["x", "y"]);
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
fn certificate_keys_outside_the_query_support_are_rejected() {
    // e is unresolved and only its meaning differs between the interpretations: e -> !y and e -> y.
    let s = sub(
        &["i0", "i1"],
        json!({}),
        json!([
            {"id": "C1", "expr": {"op": "implies", "a": var("e"), "b": not(var("y"))}, "interps": ["i0"], "provenance": ["R1"]},
            {"id": "C2", "expr": {"op": "implies", "a": var("e"), "b": var("y")}, "interps": ["i1"], "provenance": ["R1"]}
        ]),
        vec![unresolved("e"), unresolved("y")],
    );
    let q = json!({"qid": "f", "family": "feasibility", "assume": [{"op": "or", "args": [var("y"), not(var("y"))]}]});
    let v = run(&s, q.clone());
    assert_eq!(decision(&v), "SAT");
    // Fixing e (outside the support) is not a certificate, whatever it extends to.
    assert_eq!(verdict(&s, q.clone(), json!({"qid": "f", "decision": "SAT", "witness": {"e": true}})).1, "witness_invalid");
    assert_eq!(verdict(&s, q.clone(), json!({"qid": "f", "decision": "SAT", "witness": {"y": true}})), (true, "ok".into()));
    // The same holds for a partition counterexample.
    let p = json!({"qid": "p", "family": "partition", "members": [var("y")], "claim": "exhaustive", "target": null});
    let pv = run(&s, p.clone());
    assert_eq!(decision(&pv), "false");
    assert_eq!(
        verdict(&s, p, json!({"qid": "p", "decision": "false", "counterexample": {"e": false, "y": false}})).1,
        "counterexample_invalid"
    );
}

#[test]
fn null_or_malformed_missing_claims_are_missing_invalid() {
    let s = sub(
        &["i1", "i2"],
        json!({}),
        json!([
            {"id": "C1", "expr": var("x"), "interps": ["i1"], "provenance": ["R1"]},
            {"id": "C2", "expr": not(var("x")), "interps": ["i2"], "provenance": ["R1"]}
        ]),
        vec![unresolved("x")],
    );
    let q = json!({"qid": "f", "family": "feasibility", "assume": [var("x")]});
    let ans = |missing: Value| json!({"qid": "f", "decision": "UNRESOLVED", "missing": missing});
    // null means no claim; the gold support has no_date, so that is not a match.
    assert_eq!(verdict(&s, q.clone(), ans(Value::Null)).1, "missing_invalid");
    assert_eq!(verdict(&s, q.clone(), ans(json!(["no_date", "no_date"]))).1, "missing_invalid");
    assert_eq!(verdict(&s, q.clone(), ans(json!([""]))).1, "missing_invalid");
    let nine: Vec<String> = (0..8).map(|i| format!("c{i}")).chain(["no_date".to_string()]).collect();
    assert_eq!(verdict(&s, q.clone(), ans(json!(nine))).1, "missing_invalid");
    assert_eq!(verdict(&s, q, ans(json!(["no_date"]))), (true, "ok".into()));
}

#[test]
fn engine_cache_is_bounded_and_answers_match_the_monolith() {
    // A hub of 300 chained IDs and 80 single-ID partners: every partner set is a different engine.
    let hub = 300;
    let mut bindings: Vec<Value> = (0..hub).map(|i| unresolved(&format!("h{i}"))).collect();
    bindings.extend((0..80).map(|j| unresolved(&format!("s{j}"))));
    let mut constraints: Vec<Value> = (0..hub - 1)
        .map(|i| {
            cons(
                &format!("H{i}"),
                json!({"op": "implies", "a": var(&format!("h{i}")), "b": var(&format!("h{}", i + 1))}),
            )
        })
        .collect();
    constraints.extend((0..80).map(|j| {
        cons(
            &format!("S{j}"),
            json!({"op": "or", "args": [var(&format!("s{j}")), not(var(&format!("s{j}")))]}),
        )
    }));
    let s = sub(&["base"], json!({}), Value::Array(constraints), bindings);
    let mut w = World::new(&s);
    let mut m = Engine::new(&s).unwrap();
    for j in 0..80 {
        let q: Query = serde_json::from_value(json!({
            "qid": format!("q{j}"), "family": "feasibility", "assume": [var("h0"), var(&format!("s{j}"))]
        }))
        .unwrap();
        let a = serde_json::to_value(w.answer(&q)).unwrap();
        let b = serde_json::to_value(m.answer(&q)).unwrap();
        assert_eq!(a["decision"], b["decision"], "partner {j}");
        assert_eq!(a["decision"], json!("SAT"));
    }
    assert!(w.cached_engines() <= 32, "cache holds {} engines", w.cached_engines());
}

#[test]
fn closure_base_classification_runs_under_the_query_deadline() {
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("a")]);
    let q: Query = serde_json::from_value(json!({"qid": "c", "family": "closure", "hyp": []})).unwrap();
    let mut w = World::new(&s);
    w.set_query_timeout(Some(std::time::Duration::ZERO));
    assert_eq!(serde_json::to_value(w.answer(&q)).unwrap(), json!({"qid": "c", "error": "timeout"}));
}

/// Submission with one provenance record per ref (no validation: scoping is tested on raw shapes too).
fn sub_refs(refs: &[&str], aliases: Value, constraints: Value, bindings: Vec<Value>) -> Submission {
    let theory: Theory = serde_json::from_value(json!({
        "interpretations": ["base"],
        "aliases": aliases,
        "constraints": constraints,
    }))
    .unwrap();
    let bindings: Vec<Binding> = bindings.into_iter().map(|b| serde_json::from_value(b).unwrap()).collect();
    let provenance = refs
        .iter()
        .map(|r| Provenance {
            reference: r.to_string(),
            kind: if r.starts_with("ax:") { Kind::Axiom } else { Kind::Source },
            record: "m1".into(),
            field: "question".into(),
        })
        .collect();
    Submission { bindings, theory, provenance, bytes: 0 }
}

fn bound_ev(id: &str, class: &str, refs: &[&str]) -> Value {
    json!({"id": id, "status": "bound", "class": class, "evidence": refs})
}

fn unres_ev(id: &str, refs: &[&str]) -> Value {
    json!({"id": id, "status": "unresolved", "class": null, "missing": ["no_date"], "evidence": refs})
}

/// Constraint citing `refs` for all interpretations.
fn cons_ev(id: &str, expr: Value, refs: &[&str]) -> Value {
    json!({"id": id, "expr": expr, "interps": null, "provenance": refs})
}

fn alias_ref(name: &str) -> Value {
    json!({"op": "ref", "name": name})
}

/// Query JSON with an evidence scope.
fn with_ev(mut q: Value, refs: &[&str]) -> Value {
    q["evidence"] = json!(refs);
    q
}

fn answer_json(s: &Submission, q: Value) -> Value {
    let q: Query = serde_json::from_value(q).unwrap();
    serde_json::to_value(World::new(s).answer(&q)).unwrap()
}

#[test]
fn complete_evidence_scope_answers_like_no_scope() {
    // x = A (R1), y = B (R2), z unresolved (R1); the axiom AX links A to B; C2 forbids z.
    let s = sub_refs(
        &["R1", "R2", "AX"],
        json!({"A": var("x"), "B": var("y")}),
        json!([
            cons_ev("C1", json!({"op": "implies", "a": alias_ref("A"), "b": alias_ref("B")}), &["AX"]),
            cons_ev("C2", not(var("z")), &["R1"]),
        ]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"]), unres_ev("z", &["R1"])],
    );
    let all = ["R1", "R2", "AX"];
    let queries = [
        json!({"qid": "c", "family": "closure", "hyp": [var("x")]}),
        json!({"qid": "s", "family": "selection", "relation": "implies", "target": var("y")}),
        json!({"qid": "f", "family": "feasibility", "assume": [not(var("y")), var("x")]}),
        json!({"qid": "f2", "family": "feasibility", "assume": [var("x"), var("y")]}),
        json!({"qid": "p", "family": "partition", "members": [var("x"), var("y")], "claim": "exclusive", "target": null}),
    ];
    for q in queries {
        let plain = answer_json(&s, q.clone());
        let scoped = answer_json(&s, with_ev(q.clone(), &all));
        assert_eq!(plain, scoped, "query {q}");
        // The monolithic scoped path agrees with the component-partitioned one.
        let parsed: Query = serde_json::from_value(with_ev(q.clone(), &all)).unwrap();
        let mut mono = World::new(&s);
        mono.set_monolith(true);
        assert_eq!(serde_json::to_value(mono.answer(&parsed)).unwrap(), scoped, "monolith, query {q}");
    }
}

#[test]
fn dropping_an_axiom_ref_loses_entailments() {
    // A (x, R1) implies B (y, R2) only through the axiom AX.
    let full = sub_refs(
        &["R1", "R2", "AX"],
        json!({"A": var("x"), "B": var("y")}),
        json!([cons_ev("C1", json!({"op": "implies", "a": alias_ref("A"), "b": alias_ref("B")}), &["AX"])]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"])],
    );
    let hyp = json!({"qid": "c", "family": "closure", "hyp": [var("x")]});
    let labels = |v: &Value, l: &str| -> Vec<String> {
        v["labels"][l].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
    };
    let with_axiom = answer_json(&full, with_ev(hyp.clone(), &["R1", "R2", "AX"]));
    assert_eq!(labels(&with_axiom, "ENTAILED"), vec!["x", "y"]);
    // Without the axiom the implication is outside the scope: y is merely contingent.
    let no_axiom = answer_json(&full, with_ev(hyp.clone(), &["R1", "R2"]));
    assert_eq!(labels(&no_axiom, "ENTAILED"), vec!["x"]);
    assert_eq!(labels(&no_axiom, "CONTINGENT"), vec!["y"]);
    // The same answer as the weaker submission with the constraint removed.
    let weaker = sub_refs(
        &["R1", "R2", "AX"],
        json!({"A": var("x"), "B": var("y")}),
        json!([]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"])],
    );
    assert_eq!(no_axiom, answer_json(&weaker, hyp));
    // An out-of-scope binding leaves the default universe: only x remains.
    let only_r1 = answer_json(&full, with_ev(json!({"qid": "c", "family": "closure", "hyp": [var("x")]}), &["R1", "AX"]));
    assert_eq!(labels(&only_r1, "ENTAILED"), vec!["x"]);
    assert!(labels(&only_r1, "CONTINGENT").is_empty() && labels(&only_r1, "UNRESOLVED").is_empty());
}

#[test]
fn out_of_scope_queries_are_errors_except_for_universe_entries() {
    let s = sub_refs(
        &["R1", "R2"],
        json!({"A": var("x")}),
        json!([]),
        vec![bound_ev("x", "A", &["R1"]), unres_ev("y", &["R2"])],
    );
    let err = |v: Value| json!({"qid": v["qid"].clone(), "error": "out_of_scope"});
    let f = json!({"qid": "f", "family": "feasibility", "assume": [var("x")]});
    // Empty evidence, and a ref that provenance does not define.
    assert_eq!(answer_json(&s, with_ev(f.clone(), &[])), err(f.clone()));
    assert_eq!(answer_json(&s, with_ev(f.clone(), &["R9"])), err(f.clone()));
    // An expression over a binding whose evidence is outside the scope.
    let fy = json!({"qid": "fy", "family": "feasibility", "assume": [var("y")]});
    assert_eq!(answer_json(&s, with_ev(fy.clone(), &["R1"])), err(fy));
    // An explicit universe entry outside the scope is unresolved, not an error.
    let c = json!({"qid": "c", "family": "closure", "hyp": [var("x")], "universe": ["x", "y"]});
    let v = answer_json(&s, with_ev(c, &["R1"]));
    assert_eq!(v, json!({"qid": "c", "labels": {"ENTAILED": ["x"], "REFUTED": [], "CONTINGENT": [], "UNRESOLVED": ["y"]}}));
}

#[test]
fn under_cited_constraints_are_dropped_not_left_free() {
    // x (R1) is bound to A, w (R2) to W. C1 and C2 cite only AX, but both reach x, which is out of scope.
    let s = sub_refs(
        &["R1", "R2", "AX"],
        json!({"A": var("x"), "W": var("w")}),
        json!([
            cons_ev("C1", alias_ref("A"), &["AX"]),
            cons_ev("C2", json!({"op": "implies", "a": alias_ref("A"), "b": alias_ref("W")}), &["AX"]),
        ]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("w", "W", &["R2"])],
    );
    let f = json!({"qid": "f", "family": "feasibility", "assume": [not(var("w"))]});
    // Full theory: x is forced, and x implies w, so `not w` is infeasible.
    assert_eq!(decision(&answer_json(&s, f.clone())), "UNSAT");
    // Scope [R2, AX]: both constraints are dropped (they reach an outcome outside the scope), so the
    // scoped theory must not leave x as a free atom that still constrains w. `not w` is SAT.
    let v = answer_json(&s, with_ev(f.clone(), &["R2", "AX"]));
    assert_eq!(decision(&v), "SAT");
    assert_eq!(v["witness"], json!({"w": false}));
    let parsed: Query = serde_json::from_value(with_ev(f, &["R2", "AX"])).unwrap();
    let mut mono = World::new(&s);
    mono.set_monolith(true);
    assert_eq!(serde_json::to_value(mono.answer(&parsed)).unwrap(), v);
}

#[test]
fn partition_true_can_become_false_when_an_axiom_is_out_of_scope() {
    // The pair (x, not y) is exclusive under x => y, which only the axiom provides.
    let s = sub_refs(
        &["R1", "R2", "AX"],
        json!({"A": var("x"), "B": var("y")}),
        json!([cons_ev("C1", json!({"op": "implies", "a": alias_ref("A"), "b": alias_ref("B")}), &["AX"])]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"])],
    );
    let p = json!({"qid": "p", "family": "partition", "members": [var("x"), not(var("y"))], "claim": "exclusive", "target": null});
    assert_eq!(answer_json(&s, p.clone())["decision"], json!("true"));
    let v = answer_json(&s, with_ev(p, &["R1", "R2"]));
    assert_eq!(v["decision"], json!("false"));
    assert_eq!(v["counterexample"], json!({"x": true, "y": false}));
}

/// Small deterministic generator (no external crate): reproducible random submissions.
struct Lcg(u64);

impl Lcg {
    fn below(&mut self, n: u64) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (self.0 >> 33) % n
    }

    /// A random subset of `refs`, possibly empty.
    fn subset(&mut self, refs: &[String]) -> Vec<String> {
        refs.iter().filter(|_| self.below(3) == 0).cloned().collect()
    }
}

/// Random unvalidated submission: bindings over `b{i}` (bound ones via alias `A{i}` = var `b{i}`), each
/// with random evidence, and random constraints that may under-cite their provenance.
fn random_sub(rng: &mut Lcg, refs: &[String]) -> Submission {
    let nb = 2 + rng.below(9) as usize;
    let mut aliases = serde_json::Map::new();
    let mut bindings = Vec::new();
    let mut bound = Vec::new();
    for i in 0..nb {
        let ev = rng.subset(refs);
        if rng.below(2) == 0 {
            // Class definitions may reach other outcomes or an earlier class, so scopes can cut them.
            let def = match rng.below(3) {
                1 => json!({"op": "or", "args": [var(&format!("b{i}")), var(&format!("b{}", rng.below(nb as u64)))]}),
                2 if !bound.is_empty() => {
                    let k = bound[rng.below(bound.len() as u64) as usize];
                    alias_ref(&format!("A{k}"))
                }
                _ => var(&format!("b{i}")),
            };
            aliases.insert(format!("A{i}"), def);
            bound.push(i);
            bindings.push(json!({"id": format!("b{i}"), "status": "bound", "class": format!("A{i}"), "evidence": ev}));
        } else {
            bindings.push(json!({"id": format!("b{i}"), "status": "unresolved", "class": null, "missing": ["no_date"], "evidence": ev}));
        }
    }
    let mut constraints = Vec::new();
    for j in 0..rng.below(10) {
        let a = rng.below(nb as u64) as usize;
        let b = rng.below(nb as u64) as usize;
        let expr = match rng.below(3) {
            0 => var(&format!("b{a}")),
            1 => json!({"op": "implies", "a": var(&format!("b{a}")), "b": var(&format!("b{b}"))}),
            _ if !bound.is_empty() => {
                let k = bound[rng.below(bound.len() as u64) as usize];
                json!({"op": "implies", "a": var(&format!("b{a}")), "b": alias_ref(&format!("A{k}"))})
            }
            _ => json!({"op": "not", "a": var(&format!("b{a}"))}),
        };
        let prov = rng.subset(refs);
        constraints.push(json!({"id": format!("C{j}"), "expr": expr, "interps": null, "provenance": prov}));
    }
    let theory: Theory = serde_json::from_value(json!({"interpretations": ["base"], "aliases": aliases, "constraints": constraints})).unwrap();
    let bindings: Vec<Binding> = bindings.into_iter().map(|b| serde_json::from_value(b).unwrap()).collect();
    let provenance = refs
        .iter()
        .map(|r| Provenance { reference: r.clone(), kind: Kind::Source, record: "m".into(), field: "question".into() })
        .collect();
    Submission { bindings, theory, provenance, bytes: 0 }
}

fn random_refs() -> Vec<String> {
    (0..6).map(|i| format!("R{i}")).collect()
}

/// Reference reach test: every outcome reached from `e` (directly, or through alias definitions) has its
/// binding evidence inside `ev`. Naive recursion, fine at this size. Unknown names pass, as in the scope.
fn reach_in(s: &Submission, e: &Expr, ev: &[String], depth: u32) -> bool {
    let inside = |id: &str| s.bindings.iter().find(|b| b.id == id).map_or(true, |b| b.evidence.iter().all(|r| ev.contains(r)));
    let mut stack = vec![e];
    while let Some(n) = stack.pop() {
        match n {
            Expr::Var { id } if !inside(id.as_str()) => return false,
            Expr::Ref { name } => {
                if let Some(d) = s.theory.aliases.get(name) {
                    if depth > 64 || !reach_in(s, d, ev, depth + 1) {
                        return false;
                    }
                }
            }
            _ => stack.extend(n.children()),
        }
    }
    true
}

#[test]
fn evidence_index_matches_a_linear_filter_on_random_submissions() {
    let refs = random_refs();
    // Scopes that actually drop constraints, rewrite classes and drop bindings (else the test is vacuous).
    let (mut dropped_cons, mut dropped_bind, mut cut_classes) = (0, 0, 0);
    for seed in 0..60u64 {
        let mut rng = Lcg(seed.wrapping_mul(7919).wrapping_add(1));
        let s = random_sub(&mut rng, &refs);
        let mut w = World::new(&s);
        for _ in 0..6 {
            // A random non-empty scope, in random order with a possible repeat.
            let mut ev = rng.subset(&refs);
            if ev.is_empty() {
                ev.push(refs[0].clone());
            }
            if rng.below(2) == 0 {
                ev.push(ev[0].clone());
            }
            let q: Query = serde_json::from_value(json!({"qid": "c", "family": "closure", "hyp": [], "evidence": ev})).unwrap();
            let (got, sq) = w.with_scope(&q, |sw, sq| (sw.sub.clone(), sq.clone())).unwrap();
            let inside = |b: &Binding| b.evidence.iter().all(|r| ev.contains(r));
            let want_cons: Vec<&str> = s
                .theory
                .constraints
                .iter()
                .filter(|c| c.provenance.iter().all(|r| ev.contains(r)) && reach_in(&s, &c.expr, &ev, 0))
                .map(|c| c.id.as_str())
                .collect();
            let got_cons: Vec<&str> = got.theory.constraints.iter().map(|c| c.id.as_str()).collect();
            assert_eq!(got_cons, want_cons, "seed {seed}");
            let want_in: Vec<String> = s.bindings.iter().filter(|b| inside(b)).map(|b| b.id.clone()).collect();
            assert_eq!(sq.universe.clone().unwrap(), want_in, "seed {seed}");
            // Bindings: in-scope ones keep their status unless their class reaches out of scope.
            let got_ids: Vec<String> = got.bindings.iter().map(|b| b.id.clone()).collect();
            assert_eq!(got_ids, want_in, "seed {seed}");
            for b in &got.bindings {
                let orig = s.bindings.iter().find(|x| x.id == b.id).unwrap();
                let cut = orig.status == Status::Bound
                    && orig.class.as_ref().is_some_and(|c| !reach_in(&s, &Expr::Ref { name: c.clone() }, &ev, 0));
                if inside(orig) && !cut {
                    assert_eq!(b, orig, "seed {seed}");
                } else {
                    assert!(b.status == Status::Unresolved && b.class.is_none(), "seed {seed}");
                    assert_eq!(b.missing.last().map(String::as_str), Some("out_of_scope"), "seed {seed}");
                    cut_classes += usize::from(inside(orig));
                }
            }
            dropped_cons += s.theory.constraints.len() - want_cons.len();
            dropped_bind += s.bindings.len() - want_in.len();
            // Deterministic: the same scope gives the same submission, from a reused or a fresh world.
            let again = w.with_scope(&q, |sw, _| sw.sub.clone()).unwrap();
            assert_eq!(again, got, "seed {seed}");
            let fresh = World::new(&s).with_scope(&q, |sw, _| sw.sub.clone()).unwrap();
            assert_eq!(fresh, got, "seed {seed}");
        }
    }
    assert!(dropped_cons > 0 && dropped_bind > 0 && cut_classes > 0, "dropped {dropped_cons} constraints, {dropped_bind} bindings, {cut_classes} classes");
}

#[test]
fn partial_scope_answers_agree_between_components_and_one_engine() {
    // Partial scopes exercise the rewriting (classes cut, constraints dropped). Compare whole answers.
    let refs = random_refs();
    let mut compared = 0;
    for seed in 0..40u64 {
        let mut rng = Lcg(seed.wrapping_mul(104729).wrapping_add(7));
        let s = random_sub(&mut rng, &refs);
        if World::new(&s).inconsistent_interpretation(false).unwrap().is_some() {
            continue;
        }
        for _ in 0..5 {
            let mut ev = rng.subset(&refs);
            if ev.is_empty() {
                ev.push(refs[1].clone());
            }
            let inside: Vec<String> = s.bindings.iter().filter(|b| b.evidence.iter().all(|r| ev.contains(r))).map(|b| b.id.clone()).collect();
            if inside.is_empty() {
                continue;
            }
            let pick = |rng: &mut Lcg| var(&inside[rng.below(inside.len() as u64) as usize]);
            let (x, y) = (pick(&mut rng), pick(&mut rng));
            let queries = [
                json!({"qid": "c", "family": "closure", "hyp": [x.clone()]}),
                json!({"qid": "s", "family": "selection", "relation": "implies", "target": y.clone()}),
                json!({"qid": "f", "family": "feasibility", "assume": [x.clone(), not(y.clone())]}),
                json!({"qid": "p", "family": "partition", "members": [x.clone(), y.clone()], "claim": "exclusive", "target": null}),
            ];
            for q in queries {
                let parsed: Query = serde_json::from_value(with_ev(q.clone(), &ev.iter().map(String::as_str).collect::<Vec<_>>())).unwrap();
                let components = serde_json::to_value(World::new(&s).answer(&parsed)).unwrap();
                let mut mono = World::new(&s);
                mono.set_monolith(true);
                let one_engine = serde_json::to_value(mono.answer(&parsed)).unwrap();
                assert_eq!(components, one_engine, "seed {seed}, scope {ev:?}, query {q}");
                compared += 1;
            }
        }
    }
    assert!(compared > 100, "only {compared} partial-scope answers compared");
}

#[test]
fn scoped_queries_leak_nothing_and_the_scope_cache_stays_bounded() {
    let refs = random_refs();
    let mut rng = Lcg(99);
    let s = random_sub(&mut rng, &refs);
    let mut w = World::new(&s);
    let leaked_before = leaked_engines();
    let mut max_scopes = 0;
    for i in 0..200 {
        // Scopes vary, so the cache churns: each new evidence set is a new scope.
        let mut ev = rng.subset(&refs);
        if ev.is_empty() {
            ev.push(refs[0].clone());
        }
        let q = if i % 10 == 0 {
            json!({"qid": format!("q{i}"), "family": "closure", "hyp": [], "evidence": ev})
        } else {
            json!({"qid": format!("q{i}"), "family": "feasibility", "assume": [], "evidence": ev})
        };
        let q: Query = serde_json::from_value(q).unwrap();
        w.answer(&q);
        max_scopes = max_scopes.max(w.cached_scopes());
    }
    assert_eq!(leaked_engines(), leaked_before, "scoped queries leaked engine sub-submissions");
    assert!(max_scopes <= 8, "scope cache held {max_scopes} scopes");
    assert_eq!(w.cached_scopes(), 8, "the cache should be full after 200 distinct scopes");
}

#[test]
fn queries_over_no_components_do_not_leak_engines() {
    // No ID is mentioned, so the engine covers no component: it must not be cached under the empty key.
    let s = sub(&["base"], json!({}), json!([]), vec![unresolved("x")]);
    let mut w = World::new(&s);
    let closure: Query = serde_json::from_value(json!({"qid": "c", "family": "closure", "hyp": []})).unwrap();
    let feas: Query = serde_json::from_value(json!({"qid": "f", "family": "feasibility", "assume": []})).unwrap();
    let leaked_before = leaked_engines();
    let first = (serde_json::to_value(w.answer(&closure)).unwrap(), serde_json::to_value(w.answer(&feas)).unwrap());
    for _ in 0..20 {
        let again = (serde_json::to_value(w.answer(&closure)).unwrap(), serde_json::to_value(w.answer(&feas)).unwrap());
        assert_eq!(again, first, "answers must not depend on the cache state");
    }
    assert_eq!(leaked_engines(), leaked_before, "empty-component queries leaked engine sub-submissions");
}

/// Certificate example: x (R1) and z (R3) are class A, y (R2) is class B, and the axiom AX makes A imply B.
/// C1 cites both outcomes it reaches and the axiom, so a scope without AX drops it.
fn cert_sub() -> Submission {
    sub_refs(
        &["R1", "R2", "R3", "AX"],
        json!({"A": var("x"), "B": var("y")}),
        json!([cons_ev("C1", json!({"op": "implies", "a": alias_ref("A"), "b": alias_ref("B")}), &["R1", "R2", "AX"])]),
        vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"]), bound_ev("z", "A", &["R3"])],
    )
}

/// The exclusive claim {x, not y}: true, since x and not y cannot both hold once x implies y.
fn cert_query(certificate: bool) -> Value {
    let mut q = json!({"qid": "p", "family": "partition", "members": [var("x"), not(var("y"))], "claim": "exclusive", "target": null});
    if certificate {
        q["certificate"] = json!(true);
    }
    q
}

/// `check` verdict (valid, reason) for one answer to `q` against `s`, with an optional certificate cap.
fn cert_verdict(s: &Submission, q: Value, answer: Value, max: Option<u64>) -> (bool, String) {
    let q: Query = serde_json::from_value(q).unwrap();
    let a: AnswerIn = serde_json::from_value(answer).unwrap();
    let mut w = World::new(s);
    w.set_max_certificate_refs(max);
    let v = check(&mut w, std::slice::from_ref(&q), &HashMap::from([(q.qid.clone(), a)]));
    (v[0].cert_valid, v[0].reason.clone())
}

#[test]
fn certificate_must_entail_the_claim_in_its_own_scope() {
    let s = cert_sub();
    let ans = |ev: Value| json!({"qid": "p", "decision": "true", "missing": [], "evidence": ev});
    let ok = (true, "ok".to_string());
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["AX", "R1", "R2"])), None), ok);
    // Without the axiom the implication is outside the scope, so the claim is not entailed there.
    let short = (false, "claim_not_entailed".to_string());
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["R1", "R2"])), None), short);
    // An outcome outside the certificate scope is not entailed either.
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["AX", "R2"])), None), short);
    // Refs gold does not define are dropped silently: the rest still entails the claim, and without them it does not.
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["AX", "R1", "R2", "NOPE"])), None), ok);
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["NOPE"])), None), short);
    // The cap is checked on distinct refs, before gold is consulted.
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["AX", "R1", "R2"])), Some(2)), (false, "certificate_too_large".into()));
    assert_eq!(cert_verdict(&s, cert_query(false), ans(json!(["AX", "R1", "R2", "AX"])), Some(3)), ok);
    // No certificate: the old check against the full gold, which entails the claim.
    assert_eq!(cert_verdict(&s, cert_query(false), json!({"qid": "p", "decision": "true", "missing": []}), None), ok);
}

#[test]
fn certificate_must_lie_inside_the_query_scope_and_name_only_claim_outcomes() {
    let s = cert_sub();
    let ans = |ev: Value| json!({"qid": "p", "decision": "true", "missing": [], "evidence": ev});
    // A scope without the axiom: the claim is false there, and a certificate's axiom outside the scope is dropped.
    let scoped = with_ev(cert_query(false), &["R1", "R2"]);
    assert_eq!(cert_verdict(&s, scoped.clone(), ans(json!(["AX", "R1", "R2"])), None), (false, "claim_not_entailed".into()));
    assert_eq!(cert_verdict(&s, scoped, ans(json!(["R1", "R2"])), None), (false, "claim_not_entailed".into()));
    // A scope that includes the axiom admits the certificate.
    let wide = with_ev(cert_query(false), &["R1", "R2", "R3", "AX"]);
    assert_eq!(cert_verdict(&s, wide, ans(json!(["AX", "R1", "R2"])), None), (true, "ok".into()));
    // A claim over an unknown outcome is an invalid query, whatever the certificate.
    let unknown = json!({"qid": "p", "family": "partition", "members": [var("x"), var("w")], "claim": "exclusive", "target": null});
    assert_eq!(cert_verdict(&s, unknown, ans(json!(["AX", "R1", "R2"])), None), (false, "query_invalid".into()));
}

#[test]
fn certify_attaches_a_minimal_checkable_certificate() {
    let s = cert_sub();
    let mut w = World::new(&s);
    // The claim's components hold four refs (x, y and z with the constraint); z is not needed.
    assert_eq!(w.touched_refs(&["x", "y"]).len(), 4);
    let q: Query = serde_json::from_value(cert_query(true)).unwrap();
    let a = w.answer(&q);
    let cert = certify(&mut w, &q, a);
    let cert_json = serde_json::to_value(&cert).unwrap();
    assert_eq!(
        cert_json,
        json!({"qid": "p", "decision": "true", "counterexample": null, "missing": [], "evidence": ["AX", "R1", "R2"]})
    );
    assert_eq!(cert_verdict(&s, cert_query(true), cert_json, None), (true, "ok".into()));
    // Deterministic: a fresh world gives the same bytes.
    let mut w2 = World::new(&s);
    let a2 = w2.answer(&q);
    assert_eq!(serde_json::to_value(certify(&mut w2, &q, a2)).unwrap(), serde_json::to_value(&cert).unwrap());
    // Scoped: the same certificate inside a scope that contains it.
    let qs: Query = serde_json::from_value(with_ev(cert_query(true), &["R1", "R2", "R3", "AX"])).unwrap();
    let a3 = w.answer(&qs);
    let scoped = serde_json::to_value(certify(&mut w, &qs, a3)).unwrap();
    assert_eq!(scoped["evidence"], json!(["AX", "R1", "R2"]), "{scoped}");
    // Without the flag the answer is unchanged; so is a claim that is not true.
    let plain: Query = serde_json::from_value(cert_query(false)).unwrap();
    let a4 = w.answer(&plain);
    let a4_before = serde_json::to_value(&a4).unwrap();
    assert_eq!(serde_json::to_value(certify(&mut w, &plain, a4)).unwrap(), a4_before);
    let falsy: Query = serde_json::from_value(json!({"qid": "f", "family": "partition", "members": [var("x"), var("y")], "claim": "exclusive", "target": null, "certificate": true})).unwrap();
    let a5 = w.answer(&falsy);
    let a5_before = serde_json::to_value(&a5).unwrap();
    assert_eq!(serde_json::to_value(certify(&mut w, &falsy, a5)).unwrap(), a5_before);
    // Over the cap the answer is an error, never a certificate that check would refuse.
    w.set_max_certificate_refs(Some(2));
    let a6 = w.answer(&q);
    assert_eq!(serde_json::to_value(certify(&mut w, &q, a6)).unwrap(), json!({"qid": "p", "error": "certificate_too_large"}));
}

/// Certificate answer for `q` from a world with the current budget and cap, as `eval` would write it.
fn certified(w: &mut World, q: &Query) -> Value {
    let a = w.answer(q);
    serde_json::to_value(certify(w, q, a)).unwrap()
}

/// x0 -> x1 -> ... -> x{n-1}: implication i cites its two outcomes and its own axiom A{i}. The claim
/// exclusive {x0, not x{n-1}} is true, and needs every implication, so its minimal certificate has 2n-1 refs.
fn chain_sub(n: usize) -> Submission {
    let mut refs: Vec<String> = (0..n).map(|i| format!("R{i}")).collect();
    refs.extend((0..n - 1).map(|i| format!("A{i}")));
    let bindings: Vec<Value> = (0..n).map(|i| unres_ev(&format!("x{i}"), &[refs[i].as_str()])).collect();
    let constraints: Vec<Value> = (0..n - 1)
        .map(|i| {
            let expr = json!({"op": "implies", "a": var(&format!("x{i}")), "b": var(&format!("x{}", i + 1))});
            cons_ev(&format!("C{i}"), expr, &[refs[i].as_str(), refs[i + 1].as_str(), refs[n + i].as_str()])
        })
        .collect();
    let refs_s: Vec<&str> = refs.iter().map(String::as_str).collect();
    sub_refs(&refs_s, json!({}), json!(constraints), bindings)
}

fn chain_claim(n: usize) -> Value {
    json!({"qid": "c", "family": "partition", "members": [var("x0"), not(var(&format!("x{}", n - 1)))],
           "claim": "exclusive", "target": null, "certificate": true})
}

/// The padded example: C1 (x implies y) needs R1, R2 and AX. Each D_k (x implies z_k) cites R1 and its own Q_k,
/// so the reach search starts with 303 refs of which the claim needs three.
fn padded_sub() -> Submission {
    let qs: Vec<String> = (0..300).map(|k| format!("Q{k}")).collect();
    let mut refs: Vec<&str> = vec!["R1", "R2", "AX"];
    refs.extend(qs.iter().map(String::as_str));
    let mut bindings = vec![bound_ev("x", "A", &["R1"]), bound_ev("y", "B", &["R2"])];
    let mut constraints = vec![cons_ev("C1", json!({"op": "implies", "a": var("x"), "b": var("y")}), &["R1", "R2", "AX"])];
    for (k, q) in qs.iter().enumerate() {
        bindings.push(unres_ev(&format!("z{k}"), &[q.as_str()]));
        let expr = json!({"op": "implies", "a": var("x"), "b": var(&format!("z{k}"))});
        constraints.push(cons_ev(&format!("D{k}"), expr, &["R1", q.as_str()]));
    }
    sub_refs(&refs, json!({"A": var("x"), "B": var("y")}), json!(constraints), bindings)
}

#[test]
fn ddmin_finds_three_refs_among_303_under_a_cap_of_64() {
    let s = padded_sub();
    let mut w = World::new(&s);
    w.set_max_certificate_refs(Some(64));
    let q: Query = serde_json::from_value(cert_query(true)).unwrap();
    let got = certified(&mut w, &q);
    assert_eq!(got["evidence"], json!(["AX", "R1", "R2"]), "{got}");
    assert_eq!(cert_verdict(&s, cert_query(true), got, Some(64)), (true, "ok".into()));
}

#[test]
fn a_minimal_certificate_over_the_cap_is_too_large_and_one_at_the_cap_is_kept() {
    let s = chain_sub(70);
    let q: Query = serde_json::from_value(chain_claim(70)).unwrap();
    let mut w = World::new(&s);
    w.set_max_certificate_refs(Some(64));
    assert_eq!(certified(&mut w, &q), json!({"qid": "c", "error": "certificate_too_large"}));
    w.set_max_certificate_refs(Some(139));
    let got = certified(&mut w, &q);
    assert_eq!(got["evidence"].as_array().map(Vec::len), Some(139), "{got}");
    assert_eq!(cert_verdict(&s, chain_claim(70), got, Some(139)), (true, "ok".into()));
}

#[test]
fn an_exhausted_trial_budget_is_certificate_error_only_above_the_cap() {
    let s = padded_sub();
    let q: Query = serde_json::from_value(cert_query(true)).unwrap();
    let mut w = World::new(&s);
    w.set_certificate_trials(5);
    w.set_max_certificate_refs(Some(64));
    assert_eq!(certified(&mut w, &q), json!({"qid": "p", "error": "certificate_error"}));
    // Without a cap the set found so far is kept: valid, just not minimal.
    w.set_max_certificate_refs(None);
    let got = certified(&mut w, &q);
    assert!(got["evidence"].is_array(), "{got}");
    assert_eq!(cert_verdict(&s, cert_query(true), got, None), (true, "ok".into()));
}

#[test]
fn a_certificate_request_without_a_certificate_is_not_credited() {
    let s = cert_sub();
    let ok = (true, "ok".to_string());
    let no_cert = json!({"qid": "p", "decision": "true", "missing": []});
    assert_eq!(cert_verdict(&s, cert_query(true), no_cert.clone(), None), (false, "certificate_missing".into()));
    // Without the flag the old check against the full gold still applies.
    assert_eq!(cert_verdict(&s, cert_query(false), no_cert, None), ok);
    // null is an absent certificate; an empty one entails the claim only if it needs no refs.
    let null_ev = json!({"qid": "p", "decision": "true", "missing": [], "evidence": null});
    assert_eq!(cert_verdict(&s, cert_query(true), null_ev.clone(), None).1, "certificate_missing");
    assert_eq!(cert_verdict(&s, cert_query(false), null_ev, None), ok);
    let empty = json!({"qid": "p", "decision": "true", "missing": [], "evidence": []});
    assert_eq!(cert_verdict(&s, cert_query(false), empty, None), (false, "claim_not_entailed".into()));
    let tautology = json!({"qid": "t", "family": "partition", "members": [{"op": "true"}], "claim": "exclusive", "target": null});
    let empty_t = json!({"qid": "t", "decision": "true", "missing": [], "evidence": []});
    assert_eq!(cert_verdict(&s, tautology, empty_t, None), ok);
}

#[test]
fn certification_spends_the_answers_deadline_and_does_not_rearm_it() {
    let s = cert_sub();
    let mut w = World::new(&s);
    let q: Query = serde_json::from_value(cert_query(true)).unwrap();
    let a = w.answer(&q);
    // A deadline that has already passed: the certificate search must time out, not start a fresh budget.
    let past = std::time::Instant::now().checked_sub(std::time::Duration::from_secs(1)).expect("clock is past one second");
    crate::engine::set_deadline(Some(past));
    let out = serde_json::to_value(certify(&mut w, &q, a)).unwrap();
    crate::engine::set_deadline(None);
    assert_eq!(out, json!({"qid": "p", "error": "timeout"}));
}

#[test]
fn certificate_entailment_agrees_between_components_and_one_engine() {
    // The component-restricted check (`holds_on`) against one engine over the same restricted submission.
    let refs = random_refs();
    let mut rng = Lcg(7);
    let mut compared = 0;
    for _ in 0..120 {
        let s = random_sub(&mut rng, &refs);
        let ids: Vec<String> = s.bindings.iter().map(|b| b.id.clone()).collect();
        if ids.is_empty() {
            continue;
        }
        let a = ids[rng.below(ids.len() as u64) as usize].clone();
        let b = ids[rng.below(ids.len() as u64) as usize].clone();
        let members: Vec<Expr> = serde_json::from_value(json!([var(&a), not(var(&b))])).unwrap();
        let claim: Claim = serde_json::from_value(json!(["exclusive", "exhaustive", "partition"][rng.below(3) as usize])).unwrap();
        let ev = rng.subset(&refs);
        let vars: Vec<&str> = vec![a.as_str(), b.as_str()];
        let mut w = World::new(&s);
        let comp = crate::check::holds_on(&mut w, &ev, &vars, &members, claim, None).unwrap();
        let mono = w.with_evidence(&ev, &vars, |cw| {
            if !vars.iter().all(|v| cw.has_binding(v)) {
                return false;
            }
            let mut e = Engine::new(cw.sub).unwrap();
            crate::check::claim_holds(&mut e, &members, claim, None).unwrap()
        });
        assert_eq!(comp, mono, "ev {ev:?} claim {claim:?}");
        compared += 1;
    }
    assert!(compared > 100, "only {compared} comparisons");
}

#[test]
fn certificate_trials_leave_the_scope_cache_and_the_engine_caches_alone() {
    let s = padded_sub();
    let mut w = World::new(&s);
    let q: Query = serde_json::from_value(cert_query(true)).unwrap();
    let a = w.answer(&q);
    let (engines, leaked) = (w.cached_engines(), leaked_engines());
    for _ in 0..300 {
        let got = serde_json::to_value(certify(&mut w, &q, a.clone())).unwrap();
        assert!(got["evidence"].is_array(), "{got}");
    }
    assert_eq!(w.cached_scopes(), 0, "certificate scopes must not enter the query scope cache");
    assert_eq!(w.cached_engines(), engines);
    assert_eq!(leaked_engines(), leaked, "certificate trials leaked engine sub-submissions");
}
