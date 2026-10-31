//! Wire-format types and validator for Mentat Bench submissions (see docs/ir.md).

pub mod ir;
pub mod query;
pub mod sha256;
pub mod validate;

pub use ir::{expr_depth_cap, from_json, strip_bom, MAX_JSON_NESTING, Binding, Constraint, Cmp, Expr, Kind, Profile, Provenance, Status, Submission, Term, Theory, MAX_EXPR_DEPTH};
pub use query::{AnswerIn, AnswerOut, Claim, Labels, Query, QueryBody, Relation, Verdict};
pub use validate::{alias_order, validate, ValidationError};

#[cfg(test)]
mod tests {
    use super::*;

    fn sub(theory: &str, bindings: &str, prov: &str) -> Submission {
        let theory: Theory = serde_json::from_str(theory).unwrap();
        let bindings = bindings
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        let provenance = prov
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        Submission { bindings, theory, provenance, bytes: 0 }
    }

    const PROV: &str = r#"{"ref":"R1","kind":"source","record":"m1","field":"question"}"#;

    #[test]
    fn alias_cycle_is_rejected() {
        let s = sub(
            r#"{"interpretations":["base"],"aliases":{
                "A":{"op":"and","args":[{"op":"ref","name":"B"},{"op":"var","id":"toy:m1:0"}]},
                "B":{"op":"not","a":{"op":"ref","name":"A"}}},
               "constraints":[]}"#,
            r#"{"id":"toy:m1:0","status":"unresolved","class":null,"missing":["no_date"]}"#,
            "",
        );
        let errs = validate(&s, None);
        assert!(errs.iter().any(|e| e.code == "alias_cycle"), "{errs:?}");
    }

    #[test]
    fn self_alias_and_implication_cycle_rules() {
        // Expression-definition self-cycle is rejected.
        let s = sub(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"ref","name":"A"}},"constraints":[]}"#,
            "",
            "",
        );
        assert!(validate(&s, None).iter().any(|e| e.code == "alias_cycle"));
        // Implication cycles through constraints are fine.
        let s = sub(
            r#"{"interpretations":["base"],"aliases":{},"constraints":[
                {"id":"C1","expr":{"op":"implies","a":{"op":"var","id":"toy:m1:0"},"b":{"op":"var","id":"toy:m2:0"}},"interps":null,"provenance":["R1"]},
                {"id":"C2","expr":{"op":"implies","a":{"op":"var","id":"toy:m2:0"},"b":{"op":"var","id":"toy:m1:0"}},"interps":null,"provenance":["R1"]}]}"#,
            r#"{"id":"toy:m1:0","status":"unresolved","class":null}
{"id":"toy:m2:0","status":"unresolved","class":null}"#,
            PROV,
        );
        assert!(validate(&s, None).is_empty(), "{:?}", validate(&s, None));
    }

    #[test]
    fn reference_and_provenance_checks() {
        let s = sub(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"true"}},"constraints":[
                {"id":"C1","expr":{"op":"var","id":"toy:nope:0"},"interps":["other"],"provenance":[]}]}"#,
            r#"{"id":"toy:m1:0","status":"bound","class":"Missing","evidence":["R9"]}"#,
            PROV,
        );
        let codes: Vec<String> = validate(&s, None).into_iter().map(|e| e.code).collect();
        for want in ["unknown_alias", "unknown_ref", "unknown_id", "unknown_interpretation", "missing_provenance"] {
            assert!(codes.iter().any(|c| c == want), "missing {want} in {codes:?}");
        }
    }

    #[test]
    fn profile_caps() {
        let s = sub(
            r#"{"interpretations":["a","b"],"aliases":{},"constraints":[
                {"id":"C1","expr":{"op":"wsum","cmp":"le","k":1000,"terms":[{"w":300,"e":{"op":"var","id":"toy:m1:0"}}]},"interps":null,"provenance":["R1"]}]}"#,
            r#"{"id":"toy:m1:0","status":"unresolved","class":null}"#,
            PROV,
        );
        let p = Profile { max_interpretations: Some(1), max_weight_bits: Some(8), max_expr_nodes: Some(1), ..Default::default() };
        let codes: Vec<String> = validate(&s, Some(&p)).into_iter().map(|e| e.code).collect();
        for want in ["cap_interpretations", "cap_weight_bits", "cap_expr_nodes"] {
            assert!(codes.iter().any(|c| c == want), "missing {want} in {codes:?}");
        }
        assert!(validate(&s, None).is_empty());
    }
}

#[cfg(test)]
mod deep_tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn long_alias_chain_is_ordered_and_cycle_checked_without_recursion() {
        const N: usize = 200_000;
        let mut aliases = BTreeMap::new();
        aliases.insert("A0".to_string(), Expr::Var { id: "toy:m1:0".into() });
        for i in 1..N {
            aliases.insert(format!("A{i}"), Expr::Ref { name: format!("A{}", i - 1) });
        }
        let order = alias_order(&aliases).unwrap();
        assert_eq!(order.len(), N);
        assert_eq!(order[0], "A0");
        // Closing the chain into a loop is still detected.
        aliases.insert("A0".to_string(), Expr::Ref { name: format!("A{}", N - 1) });
        assert!(alias_order(&aliases).is_err());
    }

    #[test]
    fn query_bodies_reject_alias_refs() {
        let q: Query = serde_json::from_value(serde_json::json!({
            "qid": "q", "family": "feasibility",
            "assume": [{"op": "and", "args": [{"op": "var", "id": "a"}, {"op": "ref", "name": "A"}]}]
        }))
        .unwrap();
        assert!(q.uses_ref());
        let q: Query = serde_json::from_value(serde_json::json!({
            "qid": "q", "family": "feasibility", "assume": [{"op": "var", "id": "a"}]
        }))
        .unwrap();
        assert!(!q.uses_ref());
    }
}

#[cfg(test)]
mod hardening_tests {
    use super::*;
    use crate::ir::MAX_JSON_NESTING;

    /// `not(not(...(true)))` with `n` levels, as JSON text.
    fn deep_not(n: usize) -> String {
        format!("{}{}{}", r#"{"op":"not","a":"#.repeat(n), r#"{"op":"true"}"#, "}".repeat(n))
    }

    fn sub_with(theory: &str, bindings: &str) -> Submission {
        let theory: Theory = from_json(theory).unwrap();
        let bindings = bindings.lines().filter(|l| !l.trim().is_empty()).map(|l| from_json(l).unwrap()).collect();
        Submission { bindings, theory, provenance: vec![], bytes: 0 }
    }

    fn codes(s: &Submission, p: Option<&Profile>) -> Vec<String> {
        validate(s, p).into_iter().map(|e| e.code).collect()
    }

    fn with_ref_r1(mut s: Submission) -> Submission {
        s.provenance = vec![Provenance { reference: "R1".into(), kind: Kind::Source, record: "m1".into(), field: "question".into() }];
        s
    }

    /// Runs `f` on a thread with a stack large enough for 100k-level recursion in tests.
    fn big_stack<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
        std::thread::Builder::new().stack_size(1 << 30).spawn(f).unwrap().join().unwrap()
    }

    #[test]
    fn duplicate_alias_name_is_rejected() {
        let s = sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"true"},"A":{"op":"false"}},"constraints":[]}"#,
            "",
        );
        assert!(codes(&s, None).contains(&"duplicate_alias".to_string()), "{:?}", codes(&s, None));
    }

    #[test]
    fn empty_interps_list_is_rejected() {
        let s = sub_with(
            r#"{"interpretations":["base"],"aliases":{},"constraints":[{"id":"C1","expr":{"op":"false"},"interps":[],"provenance":["R1"]}]}"#,
            "",
        );
        assert!(codes(&s, None).contains(&"empty_interps".to_string()), "{:?}", codes(&s, None));
    }

    #[test]
    fn unknown_keys_are_rejected_in_records_and_expressions() {
        assert!(from_json::<Binding>(r#"{"id":"x","status":"unresolved","class":null,"extra":1}"#).is_err());
        assert!(from_json::<Constraint>(r#"{"id":"C","expr":{"op":"true"},"interps":null,"provenance":[],"x":1}"#).is_err());
        assert!(from_json::<Provenance>(r#"{"ref":"R","kind":"source","record":"m","field":"q","junk":1}"#).is_err());
        assert!(from_json::<Expr>(r#"{"op":"var","id":"x","extra":1}"#).is_err());
        assert!(from_json::<Expr>(r#"{"op":"wsum","cmp":"le","k":1,"terms":[{"w":1,"e":{"op":"true"},"z":2}]}"#).is_err());
        // Profiles may carry keys the harness owns; they are ignored here.
        assert!(from_json::<Profile>(r#"{"max_bytes":10,"build_deadline_s":5.0}"#).is_ok());
    }

    #[test]
    fn expression_depth_cap_applies_and_deep_input_parses() {
        big_stack(|| {
            let ok: Expr = from_json(&deep_not(50_000)).unwrap();
            assert_eq!(ok.depth(), 50_001);
            let s = sub_with(&format!(r#"{{"interpretations":["base"],"aliases":{{"A":{}}},"constraints":[]}}"#, deep_not(50_000)), "");
            assert!(codes(&s, None).is_empty(), "{:?}", codes(&s, None));
            // Clone and drop of the deep tree fit the stack at this depth.
            assert_eq!(s.theory.aliases["A"].clone(), ok);
            let s = sub_with(&format!(r#"{{"interpretations":["base"],"aliases":{{"A":{}}},"constraints":[]}}"#, deep_not(100_001)), "");
            assert!(codes(&s, None).contains(&"cap_depth".to_string()), "{:?}", codes(&s, None));
            let p = Profile { max_expr_depth: Some(10), ..Default::default() };
            let s = sub_with(&format!(r#"{{"interpretations":["base"],"aliases":{{"A":{}}},"constraints":[]}}"#, deep_not(10)), "");
            assert!(codes(&s, None).is_empty());
            assert!(codes(&s, Some(&p)).contains(&"cap_depth".to_string()));
        });
    }

    #[test]
    fn json_nesting_beyond_the_hard_limit_is_refused() {
        let text = "[".repeat(MAX_JSON_NESTING + 1);
        let err = from_json::<serde_json::Value>(&text).unwrap_err();
        assert!(err.contains("nesting"), "{err}");
    }

    #[test]
    fn weight_sums_are_capped_at_two_to_the_62() {
        let at = |w1: i64, w2: i64| {
            with_ref_r1(sub_with(
                &format!(
                    r#"{{"interpretations":["base"],"aliases":{{}},"constraints":[{{"id":"C1","expr":{{"op":"wsum","cmp":"le","k":0,"terms":[{{"w":{w1},"e":{{"op":"var","id":"toy:m1:0"}}}},{{"w":{w2},"e":{{"op":"var","id":"toy:m1:0"}}}}]}},"interps":null,"provenance":["R1"]}}]}}"#
                ),
                r#"{"id":"toy:m1:0","status":"unresolved","class":null,"missing":["no_date"]}"#,
            ))
        };
        assert!(codes(&at(1 << 61, 1 << 61), None).is_empty());
        assert!(codes(&at(1 << 62, 1 << 62), None).contains(&"cap_weight_sum".to_string()));
        assert!(codes(&at(i64::MIN, 0), None).contains(&"cap_weight_sum".to_string()));
    }

    #[test]
    fn cardinality_bounds_count_against_max_weight_bits() {
        let s = sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"atmost","k":300,"args":[{"op":"true"}]}},"constraints":[]}"#,
            "",
        );
        let p = Profile { max_weight_bits: Some(8), ..Default::default() };
        assert!(codes(&s, Some(&p)).contains(&"cap_weight_bits".to_string()));
        assert!(codes(&s, None).is_empty());
    }

    #[test]
    fn expression_node_budget_is_total_across_the_theory() {
        // Alias A has 3 nodes; C1 = ref (1) and C2 = not(ref) (2): 6 in total.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"and","args":[{"op":"true"},{"op":"false"}]}},"constraints":[
                {"id":"C1","expr":{"op":"ref","name":"A"},"interps":null,"provenance":["R1"]},
                {"id":"C2","expr":{"op":"not","a":{"op":"ref","name":"A"}},"interps":null,"provenance":["R1"]}]}"#,
            "",
        ));
        let p = Profile { max_expr_nodes: Some(6), ..Default::default() };
        assert!(codes(&s, Some(&p)).is_empty(), "{:?}", codes(&s, Some(&p)));
        let p = Profile { max_expr_nodes: Some(5), ..Default::default() };
        assert!(codes(&s, Some(&p)).contains(&"cap_expr_nodes".to_string()));
    }

    #[test]
    fn profile_timeout_must_be_finite_and_positive() {
        let s = sub_with(r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#, "");
        for t in [0.0, -1.0, 1e10] {
            let p = Profile { query_timeout_s: Some(t), ..Default::default() };
            assert!(codes(&s, Some(&p)).contains(&"profile_invalid".to_string()), "{t}");
        }
        let p = Profile { query_timeout_s: Some(2.5), ..Default::default() };
        assert!(codes(&s, Some(&p)).is_empty());
    }

    #[test]
    fn byte_order_mark_is_tolerated() {
        let v: Theory = from_json("\u{feff}{\"interpretations\":[\"base\"],\"aliases\":{},\"constraints\":[]}").unwrap();
        assert_eq!(v.interpretations, vec!["base".to_string()]);
    }

    #[test]
    fn load_digested_hashes_raw_bytes_with_bom() {
        let dir = std::env::temp_dir().join(format!("mb-ir-digest-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let theory = b"\xef\xbb\xbf{\"interpretations\":[\"base\"],\"aliases\":{},\"constraints\":[]}\n";
        std::fs::write(dir.join("theory.json"), theory).unwrap();
        std::fs::write(dir.join("bindings.jsonl"), b"\xef\xbb\xbf").unwrap();
        std::fs::write(dir.join("provenance.jsonl"), b"").unwrap();
        let loaded = Submission::load_digested(&dir);
        let plain = Submission::load(&dir);
        std::fs::remove_dir_all(&dir).unwrap();
        let (sub, [t, b, p]) = loaded.unwrap();
        assert!(plain.is_ok());
        assert_eq!(sub.theory.interpretations, vec!["base".to_string()]);
        assert_eq!(t, sha256::sha256_hex(theory));
        assert_eq!(b, sha256::sha256_hex(b"\xef\xbb\xbf"));
        assert_eq!(p, sha256::sha256_hex(b""));
        assert_ne!(t, sha256::sha256_hex(&theory[3..]));
    }

    #[test]
    fn read_errors_come_in_base_file_order() {
        // All three files are read before any is parsed. Each read (and UTF-8 check) follows the order bindings,
        // theory, provenance, and the first failing file in that order is reported.
        let dir = std::env::temp_dir().join(format!("mb-ir-order-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let theory = r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#;
        std::fs::write(dir.join("bindings.jsonl"), b"\xff\n").unwrap();
        std::fs::write(dir.join("provenance.jsonl"), b"\xff\n").unwrap();
        // theory.json is missing: bindings (invalid UTF-8) comes before it, theory before provenance.
        let first = Submission::load(&dir).unwrap_err();
        assert_eq!(first[0].message, "bindings.jsonl: cannot read: stream did not contain valid UTF-8");
        std::fs::write(dir.join("bindings.jsonl"), b"").unwrap();
        let second = Submission::load(&dir).unwrap_err();
        assert!(second[0].message.starts_with("theory.json: cannot read: "), "{:?}", second[0].message);
        std::fs::write(dir.join("theory.json"), theory).unwrap();
        let third = Submission::load(&dir).unwrap_err();
        assert_eq!(third[0].message, "provenance.jsonl: cannot read: stream did not contain valid UTF-8");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn parse_errors_follow_the_file_order_bindings_theory_provenance() {
        let dir = std::env::temp_dir().join(format!("mb-ir-parse-order-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let theory = r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#;
        std::fs::write(dir.join("bindings.jsonl"), "").unwrap();
        std::fs::write(dir.join("theory.json"), "{ not json").unwrap();
        std::fs::write(dir.join("provenance.jsonl"), "{ not json\n").unwrap();
        // An invalid theory and a bad provenance line: theory comes first in the file order.
        let errs = Submission::load(&dir).unwrap_err();
        assert!(errs[0].message.starts_with("theory.json: "), "{errs:?}");
        // An invalid binding line comes before both.
        std::fs::write(dir.join("bindings.jsonl"), "{ not json\n").unwrap();
        let errs = Submission::load(&dir).unwrap_err();
        assert!(errs[0].message.starts_with("bindings.jsonl: "), "{errs:?}");
        // With a valid theory, the bad provenance line is the one reported.
        std::fs::write(dir.join("bindings.jsonl"), "").unwrap();
        std::fs::write(dir.join("theory.json"), theory).unwrap();
        let errs = Submission::load(&dir).unwrap_err();
        assert!(errs[0].message.starts_with("provenance.jsonl: "), "{errs:?}");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn alias_definition_cycles_are_still_rejected() {
        let s = sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"ref","name":"B"},"B":{"op":"ref","name":"A"}},"constraints":[]}"#,
            "",
        );
        assert!(codes(&s, None).contains(&"alias_cycle".to_string()));
    }

    #[test]
    fn query_lines_reject_unknown_fields_and_foreign_fields() {
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[],"extra":1}"#).is_err());
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","hyp":[]}"#).is_err());
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[]}"#).is_ok());
        assert!(Query::from_line(r#"{"qid":"q","family":"partition","members":[],"claim":"exclusive"}"#).is_ok());
        // `evidence` is an optional scope key on every family; a non-list value is refused.
        assert!(Query::from_line(r#"{"qid":"q","family":"closure","hyp":[],"evidence":["R1"]}"#).is_ok());
        assert!(Query::from_line(r#"{"qid":"q","family":"selection","relation":"equiv","target":{"op":"true"},"evidence":[]}"#).is_ok());
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[],"evidence":"R1"}"#).is_err());
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[],"evidence":null}"#).is_err());
        assert!(Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[],"universe":null}"#).is_ok());
        assert!(Query::from_line(r#"{"qid":"q","family":"closure","hyp":[],"evidence":["R1"],"evidence":["R2"]}"#).is_err());
    }

    #[test]
    fn literal_alias_may_be_bound_to_its_own_variable() {
        // The shipped gold encodes each class as the literal of its representative outcome.
        for def in [r#"{"op":"var","id":"toy:m1:0"}"#, r#"{"op":"not","a":{"op":"var","id":"toy:m1:0"}}"#] {
            let s = sub_with(
                &format!(r#"{{"interpretations":["base"],"aliases":{{"A":{def}}},"constraints":[]}}"#),
                r#"{"id":"toy:m1:0","status":"bound","class":"A","negated":false,"missing":[],"evidence":["R1"]}"#,
            );
            let s = with_ref_r1(s);
            assert!(codes(&s, None).is_empty(), "{:?}", codes(&s, None));
        }
    }

    #[test]
    fn non_literal_definition_bound_through_its_own_variable_is_rejected() {
        // The hunter's case: A = and(a, b) with a bound to A.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"and","args":[{"op":"var","id":"toy:a:0"},{"op":"var","id":"toy:b:0"}]}},"constraints":[]}"#,
            r#"{"id":"toy:a:0","status":"bound","class":"A","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:b:0","status":"unresolved","class":null,"missing":["no_date"]}"#,
        ));
        assert!(codes(&s, None).contains(&"alias_binding_cycle".to_string()), "{:?}", codes(&s, None));
        // Hidden implication through two aliases: A = or(a, b) bound from b, B = and(x, c) with x bound to A.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"or","args":[{"op":"var","id":"toy:a:0"},{"op":"var","id":"toy:b:0"}]},"B":{"op":"and","args":[{"op":"var","id":"toy:x:0"},{"op":"var","id":"toy:c:0"}]}},"constraints":[]}"#,
            r#"{"id":"toy:x:0","status":"bound","class":"A","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:b:0","status":"bound","class":"B","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:a:0","status":"unresolved","class":null,"missing":["no_date"]}
{"id":"toy:c:0","status":"unresolved","class":null,"missing":["no_date"]}"#,
        ));
        assert!(codes(&s, None).contains(&"alias_binding_cycle".to_string()), "{:?}", codes(&s, None));
    }

    #[test]
    fn literal_alias_cannot_hide_a_non_literal_binding_cycle() {
        // C = and(D, Y) is non-literal; D = X is a literal that X's binding to C closes into a cycle.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"C":{"op":"and","args":[{"op":"ref","name":"D"},{"op":"var","id":"toy:y:0"}]},"D":{"op":"var","id":"toy:x:0"}},"constraints":[]}"#,
            r#"{"id":"toy:x:0","status":"bound","class":"C","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:y:0","status":"unresolved","class":null,"missing":["no_date"]}"#,
        ));
        assert!(codes(&s, None).contains(&"alias_binding_cycle".to_string()), "{:?}", codes(&s, None));
    }

    #[test]
    fn mutually_bound_literal_aliases_are_accepted() {
        // A = x and B = y, with x bound to B and y bound to A: equivalent literals, not a definition cycle.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"toy:y:0"},"B":{"op":"var","id":"toy:x:0"}},"constraints":[]}"#,
            r#"{"id":"toy:x:0","status":"bound","class":"B","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:y:0","status":"bound","class":"A","negated":false,"missing":[],"evidence":["R1"]}"#,
        ));
        assert!(codes(&s, None).is_empty(), "{:?}", codes(&s, None));
    }

    #[test]
    fn large_equivalence_class_with_bound_members_is_accepted() {
        // C = literal of representative x; members y1..y3 are bound to C, as the generators emit.
        let s = with_ref_r1(sub_with(
            r#"{"interpretations":["base"],"aliases":{"C":{"op":"var","id":"toy:x:0"}},"constraints":[]}"#,
            r#"{"id":"toy:x:0","status":"bound","class":"C","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:y1:0","status":"bound","class":"C","negated":true,"missing":[],"evidence":["R1"]}
{"id":"toy:y2:0","status":"bound","class":"C","negated":false,"missing":[],"evidence":["R1"]}
{"id":"toy:y3:0","status":"bound","class":"C","negated":true,"missing":[],"evidence":["R1"]}"#,
        ));
        assert!(codes(&s, None).is_empty(), "{:?}", codes(&s, None));
    }

    #[test]
    fn query_depth_is_measured_over_every_expression() {
        let deep = format!("{}{}{}", r#"{"op":"not","a":"#.repeat(20), r#"{"op":"true"}"#, "}".repeat(20));
        let q = Query::from_line(&format!(r#"{{"qid":"q","family":"feasibility","assume":[{deep}]}}"#)).unwrap();
        assert_eq!(q.max_depth(), 21);
        let q = Query::from_line(r#"{"qid":"q","family":"feasibility","assume":[{"op":"true"}]}"#).unwrap();
        assert_eq!(q.max_depth(), 1);
    }

    #[test]
    fn answers_accept_missing_null_and_eval_output_keys() {
        let a: AnswerIn = from_json(r#"{"qid":"f","decision":"SAT","witness":{},"core":null,"missing":null}"#).unwrap();
        assert_eq!(a.missing, None);
        let a: AnswerIn = from_json(r#"{"qid":"c","labels":null,"error":"hyp_inconsistent"}"#).unwrap();
        assert_eq!(a.error.as_deref(), Some("hyp_inconsistent"));
        assert!(from_json::<AnswerIn>(r#"{"qid":"f","decision":"SAT","decision":"UNSAT"}"#).is_err());
    }
}
