//! End-to-end checks of the `mb-engine` binary on small on-disk submissions.

use std::path::{Path, PathBuf};
use std::process::Command;

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("mb-engine-cli-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Writes a submission with the given theory JSON and a single unresolved binding `x`.
fn write_submission(dir: &Path, theory: &str) {
    std::fs::write(
        dir.join("bindings.jsonl"),
        r#"{"id":"x","status":"unresolved","class":null,"missing":["no_date"]}
{"id":"y","status":"bound","class":"A","negated":false,"evidence":["R1"]}
"#,
    )
    .unwrap();
    std::fs::write(dir.join("theory.json"), theory).unwrap();
    std::fs::write(
        dir.join("provenance.jsonl"),
        r#"{"ref":"R1","kind":"source","record":"m1","field":"question"}
"#,
    )
    .unwrap();
}

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_mb-engine"))
}

#[test]
fn inconsistent_base_fails_eval_instead_of_entailing() {
    // base forces x; alt forces not-x. Eval must fail, not report x ENTAILED.
    let dir = scratch("inconsistent");
    write_submission(
        &dir,
        r#"{"interpretations":["base","alt"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":null,"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["alt"],"provenance":["R1"]}]}"#,
    );
    std::fs::write(dir.join("q.jsonl"), r#"{"qid":"c","family":"closure","hyp":[]}"#).unwrap();
    let out = dir.join("a.jsonl");
    let st = bin()
        .args(["eval", dir.to_str().unwrap(), dir.join("q.jsonl").to_str().unwrap(), out.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(1), "stderr: {}", String::from_utf8_lossy(&st.stderr));
    assert!(String::from_utf8_lossy(&st.stderr).contains("inconsistent_base"));
    assert!(!out.exists(), "no answers may be written for an invalid submission");
}

#[test]
fn alias_ref_in_query_is_a_query_error() {
    let dir = scratch("refquery");
    write_submission(
        &dir,
        r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#,
    );
    std::fs::write(
        dir.join("q.jsonl"),
        r#"{"qid":"c","family":"closure","hyp":[{"op":"ref","name":"A"}]}"#,
    )
    .unwrap();
    let out = dir.join("a.jsonl");
    let st = bin()
        .args(["eval", dir.to_str().unwrap(), dir.join("q.jsonl").to_str().unwrap(), out.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(0));
    let text = std::fs::read_to_string(&out).unwrap();
    assert_eq!(text.trim(), r#"{"qid":"c","error":"ref_in_query"}"#);
}

#[test]
fn check_fails_on_inconsistent_gold_without_leaking_detail() {
    let dir = scratch("check-inconsistent");
    write_submission(
        &dir,
        r#"{"interpretations":["base","alt"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":null,"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["alt"],"provenance":["R1"]}]}"#,
    );
    std::fs::write(dir.join("q.jsonl"), "").unwrap();
    std::fs::write(dir.join("a.jsonl"), "").unwrap();
    let st = bin()
        .args([
            "check",
            dir.to_str().unwrap(),
            dir.join("q.jsonl").to_str().unwrap(),
            dir.join("a.jsonl").to_str().unwrap(),
            dir.join("v.jsonl").to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(1));
    let err = String::from_utf8_lossy(&st.stderr);
    assert!(err.contains("inconsistent_base"));
    assert!(!err.contains("alt"), "gold interpretation names must not be disclosed: {err}");
}

#[test]
fn eval_enforces_profile_query_timeout() {
    // A timeout far below any solve time: the query answers `timeout` and eval still succeeds.
    let dir = scratch("timeout");
    write_submission(&dir, r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#);
    std::fs::write(dir.join("profile.json"), r#"{"query_timeout_s":1e-9}"#).unwrap();
    std::fs::write(dir.join("q.jsonl"), r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let out = dir.join("a.jsonl");
    let st = bin()
        .args(["eval", dir.to_str().unwrap(), dir.join("q.jsonl").to_str().unwrap(), out.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(0), "stderr: {}", String::from_utf8_lossy(&st.stderr));
    let text = std::fs::read_to_string(&out).unwrap();
    assert_eq!(text.trim(), r#"{"qid":"f","error":"timeout"}"#);
}

/// Fresh scratch directory with a valid two-binding submission (`x` unresolved, `y` bound to `A`).
fn fresh_sub(name: &str) -> PathBuf {
    let dir = scratch(name);
    write_submission(&dir, r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#);
    dir
}

fn run_eval(dir: &Path, q: &Path, a: &Path) -> std::process::Output {
    bin().args(["eval", dir.to_str().unwrap(), q.to_str().unwrap(), a.to_str().unwrap()]).output().unwrap()
}

fn run_check(gold: &Path, q: &Path, a: &Path, v: &Path) -> std::process::Output {
    bin()
        .args(["check", gold.to_str().unwrap(), q.to_str().unwrap(), a.to_str().unwrap(), v.to_str().unwrap()])
        .output()
        .unwrap()
}

fn stderr(o: &std::process::Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

/// Output location outside the submission directory: outputs may not name files inside it.
fn outside(dir: &Path, name: &str) -> PathBuf {
    let d = dir.with_file_name(format!("{}-out", dir.file_name().unwrap().to_string_lossy()));
    std::fs::create_dir_all(&d).unwrap();
    let p = d.join(name);
    let _ = std::fs::remove_file(&p);
    p
}

const PROV_R1: &str = "{\"ref\":\"R1\",\"kind\":\"source\",\"record\":\"m1\",\"field\":\"question\"}\n";

#[test]
fn bad_query_lines_are_answered_and_the_rest_still_runs() {
    let dir = fresh_sub("badq");
    std::fs::write(
        dir.join("q.jsonl"),
        "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[{\"op\":\"var\",\"id\":\"x\"}]}\n\
         {\"qid\":\"bad\",\"family\":\"nope\"}\n\
         not json at all\n\
         {\"qid\":\"f2\",\"family\":\"feasibility\",\"assume\":[],\"extra\":1}\n\
         {\"qid\":\"g\",\"family\":\"feasibility\",\"assume\":[]}\n",
    )
    .unwrap();
    let out = dir.join("a.jsonl");
    let o = run_eval(&dir, &dir.join("q.jsonl"), &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let lines: Vec<String> = std::fs::read_to_string(&out).unwrap().lines().map(String::from).collect();
    // The unparseable line 3 has no qid, so it gets no answer (and is reported on stderr).
    assert_eq!(lines.len(), 4);
    assert!(lines[0].contains(r#""qid":"f""#) && lines[0].contains("SAT"), "{}", lines[0]);
    assert_eq!(lines[1], r#"{"qid":"bad","error":"bad_query"}"#);
    assert_eq!(lines[2], r#"{"qid":"f2","error":"bad_query"}"#);
    assert!(lines[3].contains(r#""qid":"g""#), "{}", lines[3]);
}

#[test]
fn duplicate_json_keys_are_rejected_per_line() {
    let dir = fresh_sub("dupkeys");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"q\",\"family\":\"feasibility\",\"family\":\"feasibility\",\"assume\":[]}
         {\"qid\":\"r\",\"family\":\"feasibility\",\"assume\":[]}
",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    assert_eq!(run_eval(&dir, &q, &out).status.code(), Some(0));
    let text = std::fs::read_to_string(&out).unwrap();
    let got: Vec<&str> = text.lines().collect();
    assert_eq!(got[0], r#"{"qid":"q","error":"bad_query"}"#);
    assert!(got[1].contains(r#""qid":"r""#), "{}", got[1]);
    // Check: a duplicated decision key is a malformed answer for its qid.
    let a = outside(&dir, "answers.jsonl");
    std::fs::write(&a, "{\"qid\":\"r\",\"decision\":\"UNSAT\",\"decision\":\"SAT\",\"witness\":{}}
").unwrap();
    let v = outside(&dir, "v.jsonl");
    assert_eq!(run_check(&dir, &q, &a, &v).status.code(), Some(0));
    assert!(std::fs::read_to_string(&v).unwrap().contains(r#""reason":"answer_error""#));
}

#[test]
fn duplicate_query_ids_are_an_invalid_workload() {
    let dir = fresh_sub("dupq");
    std::fs::write(
        dir.join("q.jsonl"),
        "{\"qid\":\"q\",\"family\":\"feasibility\",\"assume\":[]}\n{\"qid\":\"q\",\"family\":\"feasibility\",\"assume\":[]}\n",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    std::fs::write(&out, "stale").unwrap();
    let o = run_eval(&dir, &dir.join("q.jsonl"), &out);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(stderr(&o).contains("duplicate qid q"));
    assert!(!out.exists(), "a failed run must not leave an answers file");
}

#[test]
fn byte_order_marks_are_tolerated_on_every_input() {
    let dir = scratch("bom");
    let bom = "\u{feff}";
    std::fs::write(
        dir.join("bindings.jsonl"),
        format!("{bom}{}\n", r#"{"id":"x","status":"unresolved","class":null,"missing":["no_date"]}"#),
    )
    .unwrap();
    std::fs::write(dir.join("theory.json"), format!("{bom}{}", r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#)).unwrap();
    std::fs::write(dir.join("provenance.jsonl"), format!("{bom}{PROV_R1}")).unwrap();
    std::fs::write(dir.join("profile.json"), format!("{bom}{}", r#"{"query_timeout_s":30}"#)).unwrap();
    std::fs::write(dir.join("q.jsonl"), format!("{bom}{}\n", r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#)).unwrap();
    let out = dir.join("a.jsonl");
    let o = run_eval(&dir, &dir.join("q.jsonl"), &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert!(std::fs::read_to_string(&out).unwrap().contains("SAT"));
}

#[test]
fn output_naming_an_input_is_refused_and_the_input_survives() {
    let dir = fresh_sub("refuse");
    let q = dir.join("q.jsonl");
    std::fs::write(&q, "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[]}\n").unwrap();
    let before = std::fs::read(&q).unwrap();
    let o = run_eval(&dir, &q, &q);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert_eq!(std::fs::read(&q).unwrap(), before);
    let binds = dir.join("bindings.jsonl");
    let before = std::fs::read(&binds).unwrap();
    let o = run_eval(&dir, &q, &binds);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert_eq!(std::fs::read(&binds).unwrap(), before);
    // Check: a verdicts path equal to the answers path is refused too.
    let a = dir.join("a.jsonl");
    std::fs::write(&a, "{\"qid\":\"f\",\"decision\":\"SAT\",\"witness\":{}}\n").unwrap();
    let o = run_check(&dir, &q, &a, &a);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(std::fs::read_to_string(&a).unwrap().contains("witness"));
}

#[test]
fn failed_run_removes_stale_answers() {
    let dir = scratch("stale");
    write_submission(
        &dir,
        r#"{"interpretations":["base","alt"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":null,"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["alt"],"provenance":["R1"]}]}"#,
    );
    std::fs::write(dir.join("q.jsonl"), r#"{"qid":"c","family":"closure","hyp":[]}"#).unwrap();
    let out = outside(&dir, "a.jsonl");
    std::fs::write(&out, "stale answers").unwrap();
    let o = run_eval(&dir, &dir.join("q.jsonl"), &out);
    assert_eq!(o.status.code(), Some(1));
    assert!(!out.exists(), "stale answers must be deleted on failure");
}

#[test]
fn profile_caps_apply_to_eval_and_bad_profiles_exit_1() {
    let dir = fresh_sub("caps");
    let q = dir.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let out = outside(&dir, "a.jsonl");
    // The node cap from profile.json is enforced by eval (the theory has one node).
    std::fs::write(dir.join("profile.json"), r#"{"max_expr_nodes":0}"#).unwrap();
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("cap_expr_nodes"));
    assert!(!out.exists());
    // A zero timeout is a profile error, not "unlimited".
    std::fs::write(dir.join("profile.json"), r#"{"query_timeout_s":0}"#).unwrap();
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("profile_invalid"));
    // A float where an integer cap belongs is an invalid profile (exit 1), not a crash.
    std::fs::write(dir.join("profile.json"), r#"{"max_weight_bits":8.5,"query_timeout_s":5}"#).unwrap();
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("profile_invalid"));
}

#[test]
fn check_applies_the_gold_query_timeout() {
    let gold = fresh_sub("ctimeout");
    std::fs::write(gold.join("profile.json"), r#"{"query_timeout_s":1e-9}"#).unwrap();
    let q = gold.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let a = gold.join("a.jsonl");
    std::fs::write(&a, r#"{"qid":"f","decision":"SAT","witness":{"x":true}}"#).unwrap();
    let v = gold.join("v.jsonl");
    let o = run_check(&gold, &q, &a, &v);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"f","cert_valid":false,"reason":"timeout"}"#);
}

#[test]
fn check_scores_duplicate_and_malformed_answers_per_qid() {
    let gold = fresh_sub("cdup");
    let q = gold.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"f1\",\"family\":\"feasibility\",\"assume\":[{\"op\":\"var\",\"id\":\"x\"}]}\n\
         {\"qid\":\"f2\",\"family\":\"feasibility\",\"assume\":[{\"op\":\"var\",\"id\":\"x\"}]}\n\
         {\"qid\":\"f3\",\"family\":\"feasibility\",\"assume\":[{\"op\":\"var\",\"id\":\"x\"}]}\n",
    )
    .unwrap();
    let a = gold.join("a.jsonl");
    std::fs::write(
        &a,
        "{\"qid\":\"f1\",\"decision\":\"SAT\",\"witness\":{\"x\":true}}\n\
         {\"qid\":\"f2\",\"decision\":\"SAT\",\"witness\":{\"x\":true}}\n\
         {\"qid\":\"f2\",\"decision\":\"SAT\",\"witness\":{\"x\":true}}\n\
         {\"qid\":\"f3\",\"decision\":\"SAT\",\"witness\":5}\n\
         this line is not json\n",
    )
    .unwrap();
    let v = gold.join("v.jsonl");
    let o = run_check(&gold, &q, &a, &v);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let text = std::fs::read_to_string(&v).unwrap();
    let got: Vec<&str> = text.lines().collect();
    assert_eq!(got[0], r#"{"qid":"f1","cert_valid":true,"reason":"ok"}"#);
    assert_eq!(got[1], r#"{"qid":"f2","cert_valid":false,"reason":"answer_error"}"#);
    assert_eq!(got[2], r#"{"qid":"f3","cert_valid":false,"reason":"answer_error"}"#);
}

#[test]
fn eval_closure_output_is_uncertified_not_an_answer_error_in_check() {
    let gold = fresh_sub("cclosure");
    let q = gold.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"c","family":"closure","hyp":[]}"#).unwrap();
    let a = gold.join("a.jsonl");
    assert_eq!(run_eval(&gold, &q, &a).status.code(), Some(0));
    let v = gold.join("v.jsonl");
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert_eq!(
        std::fs::read_to_string(&v).unwrap().trim(),
        r#"{"qid":"c","cert_valid":false,"reason":"family_not_certified"}"#
    );
}

#[test]
fn unresolved_answers_must_name_the_gold_missing_codes() {
    // i1 needs x and i2 needs not-x: UNRESOLVED over {x}, whose gold missing code is no_date.
    let gold = scratch("cmissing");
    write_submission(
        &gold,
        r#"{"interpretations":["i1","i2"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":["i1"],"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["i2"],"provenance":["R1"]}]}"#,
    );
    let q = gold.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let a = gold.join("a.jsonl");
    let v = outside(&gold, "v.jsonl");
    std::fs::write(&a, r#"{"qid":"f","decision":"UNRESOLVED","missing":["no_source"]}"#).unwrap();
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"f","cert_valid":false,"reason":"missing_invalid"}"#);
    std::fs::write(&a, r#"{"qid":"f","decision":"UNRESOLVED","missing":["no_date"]}"#).unwrap();
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"f","cert_valid":true,"reason":"ok"}"#);
}

#[test]
fn weight_overflow_is_a_query_error_and_a_validation_error_in_the_theory() {
    let dir = fresh_sub("ovf");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        r#"{"qid":"f","family":"feasibility","assume":[{"op":"wsum","cmp":"le","k":0,"terms":[{"w":4611686018427387904,"e":{"op":"var","id":"x"}},{"w":4611686018427387904,"e":{"op":"var","id":"y"}}]}]}"#,
    )
    .unwrap();
    let out = dir.join("a.jsonl");
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert_eq!(std::fs::read_to_string(&out).unwrap().trim(), r#"{"qid":"f","error":"weight_overflow"}"#);
    let v = dir.join("v.jsonl");
    std::fs::write(&out, r#"{"qid":"f","decision":"SAT","witness":{}}"#).unwrap();
    assert_eq!(run_check(&dir, &q, &out, &v).status.code(), Some(0));
    assert!(std::fs::read_to_string(&v).unwrap().contains(r#""reason":"query_invalid""#));
    // The same magnitude inside the theory is rejected by validation (exit 1), never by a crash.
    let bad = scratch("ovf-theory");
    write_submission(
        &bad,
        r#"{"interpretations":["base"],"aliases":{},"constraints":[{"id":"C1","expr":{"op":"wsum","cmp":"le","k":0,"terms":[{"w":-9223372036854775808,"e":{"op":"var","id":"x"}}]},"interps":null,"provenance":["R1"]}]}"#,
    );
    let o = bin().args(["validate", bad.to_str().unwrap()]).output().unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&o.stdout).contains("cap_weight_sum"));
}

#[test]
fn schema_and_structure_errors_are_invalid_submissions_exit_1() {
    let cases = [
        ("dupalias", r#"{"interpretations":["base"],"aliases":{"A":{"op":"true"},"A":{"op":"false"}},"constraints":[]}"#, r#"{"id":"x","status":"unresolved","class":null}"#),
        ("noint", r#"{"interpretations":["base"],"aliases":{},"constraints":[{"id":"C1","expr":{"op":"false"},"interps":[],"provenance":["R1"]}]}"#, r#"{"id":"x","status":"unresolved","class":null}"#),
        ("unknownbind", r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#, r#"{"id":"x","status":"unresolved","class":null,"junk":1}"#),
        ("unknownexpr", r#"{"interpretations":["base"],"aliases":{},"constraints":[{"id":"C1","expr":{"op":"var","id":"x","zz":1},"interps":null,"provenance":["R1"]}]}"#, r#"{"id":"x","status":"unresolved","class":null}"#),
    ];
    for (name, theory, binding) in cases {
        let dir = scratch(name);
        std::fs::write(dir.join("bindings.jsonl"), format!("{binding}\n")).unwrap();
        std::fs::write(dir.join("theory.json"), theory).unwrap();
        std::fs::write(dir.join("provenance.jsonl"), PROV_R1).unwrap();
        let o = bin().args(["validate", dir.to_str().unwrap()]).output().unwrap();
        assert_eq!(o.status.code(), Some(1), "{name}: {}", String::from_utf8_lossy(&o.stdout));
    }
}

#[test]
fn missing_directory_is_usage_error_and_missing_file_is_invalid() {
    let none = std::env::temp_dir().join(format!("mb-engine-none-{}", std::process::id()));
    let o = bin().args(["validate", none.to_str().unwrap()]).output().unwrap();
    assert_eq!(o.status.code(), Some(2));
    let dir = scratch("nofile");
    let o = bin().args(["validate", dir.to_str().unwrap()]).output().unwrap();
    assert_eq!(o.status.code(), Some(1));
}

#[test]
fn deep_expressions_evaluate_and_the_depth_cap_is_enforced() {
    let dir = scratch("deepcli");
    let build = |n: usize| {
        let expr = format!("{}{}{}", r#"{"op":"not","a":"#.repeat(n), r#"{"op":"var","id":"x"}"#, "}".repeat(n));
        std::fs::write(dir.join("bindings.jsonl"), "{\"id\":\"x\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n").unwrap();
        std::fs::write(
            dir.join("theory.json"),
            format!("{{\"interpretations\":[\"base\"],\"aliases\":{{}},\"constraints\":[{{\"id\":\"C1\",\"expr\":{expr},\"interps\":null,\"provenance\":[\"R1\"]}}]}}"),
        )
        .unwrap();
        std::fs::write(dir.join("provenance.jsonl"), PROV_R1).unwrap();
    };
    let q = dir.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[]}"#).unwrap();
    let out = outside(&dir, "a.jsonl");
    build(20_000);
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    build(100_001);
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("cap_depth"));
}

#[test]
fn evaluation_is_byte_identical_across_processes() {
    // Three independent components; a later query can be served by either of two cached engines.
    let dir = scratch("determinism");
    std::fs::write(
        dir.join("bindings.jsonl"),
        "{\"id\":\"a\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n\
         {\"id\":\"c\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n\
         {\"id\":\"e\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n",
    )
    .unwrap();
    std::fs::write(dir.join("theory.json"), r#"{"interpretations":["base"],"aliases":{},"constraints":[]}"#).unwrap();
    std::fs::write(dir.join("provenance.jsonl"), PROV_R1).unwrap();
    let v = |id: &str| format!("{{\"op\":\"var\",\"id\":\"{id}\"}}");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        format!(
            "{{\"qid\":\"q1\",\"family\":\"feasibility\",\"assume\":[{},{}]}}\n{{\"qid\":\"q2\",\"family\":\"feasibility\",\"assume\":[{},{}]}}\n{{\"qid\":\"q3\",\"family\":\"feasibility\",\"assume\":[{}]}}\n{{\"qid\":\"q4\",\"family\":\"partition\",\"members\":[{},{}],\"claim\":\"exclusive\",\"target\":null}}\n",
            v("a"), v("c"), v("a"), v("e"), v("a"), v("a"), v("e")
        ),
    )
    .unwrap();
    let mut outs = Vec::new();
    for i in 0..4 {
        let out = dir.join(format!("a{i}.jsonl"));
        assert_eq!(run_eval(&dir, &q, &out).status.code(), Some(0));
        outs.push(std::fs::read(&out).unwrap());
    }
    assert!(outs.windows(2).all(|w| w[0] == w[1]), "answers differ between processes");
}

#[test]
fn mb_timing_writes_stage_lines_to_stderr_and_leaves_answers_alone() {
    let dir = fresh_sub("timing");
    let q = dir.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"c","family":"closure","hyp":[]}"#).unwrap();
    let plain = dir.join("plain.jsonl");
    assert_eq!(run_eval(&dir, &q, &plain).status.code(), Some(0));
    let timed = dir.join("timed.jsonl");
    let st = bin()
        .env("MB_TIMING", "1")
        .args(["eval", dir.to_str().unwrap(), q.to_str().unwrap(), timed.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(0), "stderr: {}", stderr(&st));
    assert_eq!(std::fs::read(&plain).unwrap(), std::fs::read(&timed).unwrap());
    let err = stderr(&st);
    for name in ["load", "validate", "read_queries", "world_new", "base", "queries", "answer", "certify"] {
        assert!(err.lines().any(|l| l.starts_with(&format!("stage\t{name}\t"))), "no {name} stage in: {err}");
    }
}

#[test]
fn partial_counterexample_is_maximal_and_certified() {
    // i0 forces x & y & !z, i1 forces !x & y & z: no joint model, so the counterexample is partial.
    let gold = scratch("partial");
    std::fs::write(
        gold.join("bindings.jsonl"),
        "{\"id\":\"x\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n\
         {\"id\":\"y\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n\
         {\"id\":\"z\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"]}\n",
    )
    .unwrap();
    std::fs::write(
        gold.join("theory.json"),
        r#"{"interpretations":["i0","i1"],"aliases":{},"constraints":[
            {"id":"C1","expr":{"op":"and","args":[{"op":"var","id":"x"},{"op":"var","id":"y"},{"op":"not","a":{"op":"var","id":"z"}}]},"interps":["i0"],"provenance":["R1"]},
            {"id":"C2","expr":{"op":"and","args":[{"op":"not","a":{"op":"var","id":"x"}},{"op":"var","id":"y"},{"op":"var","id":"z"}]},"interps":["i1"],"provenance":["R1"]}]}"#,
    )
    .unwrap();
    std::fs::write(gold.join("provenance.jsonl"), PROV_R1).unwrap();
    let q = gold.join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"p","family":"partition","members":[{"op":"var","id":"x"},{"op":"var","id":"y"},{"op":"var","id":"z"}],"claim":"exclusive","target":null}"#).unwrap();
    let a = gold.join("a.jsonl");
    assert_eq!(run_eval(&gold, &q, &a).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&a).unwrap().trim(), r#"{"qid":"p","decision":"false","counterexample":{"y":true},"missing":[]}"#);
    let v = gold.join("v.jsonl");
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"p","cert_valid":true,"reason":"ok"}"#);
}

#[test]
fn shipped_gold_submissions_validate() {
    // Regression for the literal-alias representation; the public toy10k fixture is required.
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/toy10k/gold");
    assert!(dir.is_dir(), "required shipped toy10k gold fixture is missing: {}", dir.display());
    let o = bin().args(["validate", dir.to_str().unwrap()]).output().unwrap();
    assert_eq!(o.status.code(), Some(0), "toy10k: {}", String::from_utf8_lossy(&o.stdout));
}

#[test]
fn output_guard_runs_before_loading_and_never_removes_an_input() {
    // A mistyped submission directory must not make an input file the removal target.
    let dir = fresh_sub("guardtypo");
    let typo = dir.with_file_name(format!("{}-typo", dir.file_name().unwrap().to_string_lossy()));
    let _ = std::fs::remove_dir_all(&typo);
    let q = dir.join("q.jsonl");
    std::fs::write(&q, "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[]}\n").unwrap();
    let before = std::fs::read(&q).unwrap();
    let o = run_eval(&typo, &q, &q);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(stderr(&o).contains("refusing to write output"), "{}", stderr(&o));
    assert_eq!(std::fs::read(&q).unwrap(), before);
    // Check with the answers file as the verdicts path, on a missing gold directory.
    let a = dir.join("a.jsonl");
    std::fs::write(&a, "{\"qid\":\"f\",\"decision\":\"SAT\",\"witness\":{}}\n").unwrap();
    let before = std::fs::read(&a).unwrap();
    let o = run_check(&typo, &q, &a, &a);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert_eq!(std::fs::read(&a).unwrap(), before);
    // Stale output that is not an input is still cleared, even when the run fails early.
    let out = outside(&dir, "stale.jsonl");
    std::fs::write(&out, "stale").unwrap();
    let o = run_eval(&typo, &q, &out);
    assert_eq!(o.status.code(), Some(2));
    assert!(!out.exists(), "stale output must be removed");
}

#[test]
fn hard_linked_output_is_refused_as_the_same_file() {
    let dir = fresh_sub("hardlink");
    let q = dir.join("q.jsonl");
    std::fs::write(&q, "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[]}\n").unwrap();
    let link = outside(&dir, "link.jsonl");
    if std::fs::hard_link(&q, &link).is_err() {
        eprintln!("skipping: hard links unavailable");
        return;
    }
    let before = std::fs::read(&q).unwrap();
    let o = run_eval(&dir, &q, &link);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert_eq!(std::fs::read(&q).unwrap(), before, "the input behind the hard link must survive");
    assert!(link.exists());
}

#[test]
fn output_inside_a_submission_subdirectory_is_refused() {
    let dir = fresh_sub("nested");
    let sub = dir.join("notes");
    std::fs::create_dir_all(&sub).unwrap();
    let inner = sub.join("x.jsonl");
    std::fs::write(&inner, "keep me").unwrap();
    let q = outside(&dir, "q.jsonl");
    std::fs::write(&q, "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[]}\n").unwrap();
    let o = run_eval(&dir, &q, &inner);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert_eq!(std::fs::read_to_string(&inner).unwrap(), "keep me");
}

#[test]
fn invalid_gold_reports_codes_only() {
    let gold = scratch("goldcodes");
    write_submission(&gold, r#"{"interpretations":["base"],"aliases":{"SECRET":{"op":"true"},"SECRET":{"op":"false"}},"constraints":[]}"#);
    let q = outside(&gold, "q.jsonl");
    std::fs::write(&q, "{\"qid\":\"f\",\"family\":\"feasibility\",\"assume\":[]}\n").unwrap();
    let a = outside(&gold, "a.jsonl");
    std::fs::write(&a, "").unwrap();
    let v = outside(&gold, "v.jsonl");
    let o = run_check(&gold, &q, &a, &v);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    let err = stderr(&o);
    assert!(err.contains("duplicate_alias"), "{err}");
    assert!(!err.contains("SECRET") && !err.contains("message"), "{err}");
}

#[test]
fn missing_claims_null_duplicates_and_malformed_lists_are_missing_invalid() {
    let gold = scratch("cmissing-shape");
    write_submission(
        &gold,
        r#"{"interpretations":["i1","i2"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":["i1"],"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["i2"],"provenance":["R1"]}]}"#,
    );
    let q = outside(&gold, "q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let a = outside(&gold, "a.jsonl");
    let v = outside(&gold, "v.jsonl");
    for claim in [r#"null"#, r#"["no_date","no_date"]"#, r#"[""]"#, r#"["a","b","c","d","e","f","g","h","no_date"]"#] {
        std::fs::write(&a, format!("{{\"qid\":\"f\",\"decision\":\"UNRESOLVED\",\"missing\":{claim}}}\n")).unwrap();
        assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
        assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"f","cert_valid":false,"reason":"missing_invalid"}"#, "{claim}");
    }
    // An absent claim is treated like null: no match against gold's no_date.
    std::fs::write(&a, "{\"qid\":\"f\",\"decision\":\"UNRESOLVED\"}\n").unwrap();
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert!(std::fs::read_to_string(&v).unwrap().contains("missing_invalid"));
}

#[test]
fn query_expressions_nested_past_the_depth_cap_are_bad_queries() {
    let dir = scratch("qdepth");
    write_submission(&dir, r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#);
    let deep = format!("{}{}{}", r#"{"op":"not","a":"#.repeat(100_001), r#"{"op":"var","id":"x"}"#, "}".repeat(100_001));
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!("{{\"qid\":\"deep\",\"family\":\"feasibility\",\"assume\":[{deep}]}}\n{{\"qid\":\"ok\",\"family\":\"feasibility\",\"assume\":[]}}\n")).unwrap();
    let out = outside(&dir, "a.jsonl");
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let text = std::fs::read_to_string(&out).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines[0], r#"{"qid":"deep","error":"bad_query"}"#);
    assert!(lines[1].contains(r#""qid":"ok""#), "{}", lines[1]);
}

#[test]
fn unparseable_lines_get_no_answer_and_bad_qids_do_not_collide() {
    let dir = fresh_sub("badkeep");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"q\",\"family\":\"feasibility\",\"assume\":[]}\n\
         {\"qid\":\"q\",\"family\":\"nope\"}\n\
         this is not json\n\
         {\"qid\":\"r\",\"family\":\"feasibility\",\"assume\":[]}\n",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    let o = run_eval(&dir, &q, &out);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert!(stderr(&o).contains("line 3"), "{}", stderr(&o));
    let text = std::fs::read_to_string(&out).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 3, "{text}");
    assert!(lines[0].contains(r#""qid":"q""#) && lines[0].contains("SAT"));
    assert_eq!(lines[1], r#"{"qid":"q","error":"bad_query"}"#);
    assert!(lines[2].contains(r#""qid":"r""#));
}

#[test]
fn bad_missing_or_wrong_typed_key_in_a_query_is_not_a_duplicate_workload() {
    // A bad line that recovers qid "dup" does not make the workload invalid next to a good "dup".
    let dir = fresh_sub("baddup");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"dup\",\"family\":\"feasibility\",\"assume\":[]}\n{\"qid\":\"dup\",\"family\":\"feasibility\",\"assume\":[],\"zz\":1}\n",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    assert_eq!(run_eval(&dir, &q, &out).status.code(), Some(0));
}

#[test]
#[cfg(not(debug_assertions))]
fn nesting_limit_is_reached_on_the_production_worker_and_one_more_is_refused() {
    // Expression nesting n gives JSON nesting n + 4 (object, array, object, then n + 1 expression
    // objects). Release-only: the debug build's frames are several times larger than production's.
    let limit = mb_ir::MAX_JSON_NESTING;
    let build = |n: usize| {
        let dir = scratch(&format!("limit-{n}"));
        write_submission(&dir, r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#);
        let expr = format!("{}{}{}", r#"{"op":"not","a":"#.repeat(n), r#"{"op":"var","id":"x"}"#, "}".repeat(n));
        std::fs::write(
            dir.join("theory.json"),
            format!(r#"{{"interpretations":["base"],"aliases":{{"A":{{"op":"var","id":"x"}}}},"constraints":[{{"id":"C1","expr":{expr},"interps":null,"provenance":["R1"]}}]}}"#),
        )
        .unwrap();
        dir
    };
    let q = scratch("limit-q").join("q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[]}"#).unwrap();
    // Exactly at the limit: parsed in full, then refused by the depth cap (clean exit 1).
    let at = build(limit - 4);
    let o = run_eval(&at, &q, &at.join("a.jsonl"));
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("cap_depth"), "{}", stderr(&o));
    // One level over: refused while parsing (clean exit 1).
    let over = build(limit - 3);
    let o = run_eval(&over, &q, &over.join("a.jsonl"));
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("nesting deeper"), "{}", stderr(&o));
}

#[test]
fn claimed_missing_codes_matching_gold_certify() {
    let gold = scratch("cmissing-ok");
    write_submission(
        &gold,
        r#"{"interpretations":["i1","i2"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":["i1"],"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["i2"],"provenance":["R1"]}]}"#,
    );
    let q = outside(&gold, "q.jsonl");
    std::fs::write(&q, r#"{"qid":"f","family":"feasibility","assume":[{"op":"var","id":"x"}]}"#).unwrap();
    let a = outside(&gold, "a.jsonl");
    std::fs::write(&a, r#"{"qid":"f","decision":"UNRESOLVED","missing":["no_date"]}"#).unwrap();
    let v = outside(&gold, "v.jsonl");
    assert_eq!(run_check(&gold, &q, &a, &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"f","cert_valid":true,"reason":"ok"}"#);
}

/// Evidence-scope submission, built here (no committed fixture). x = A (R1) and y = B (R2) are
/// bound; z is unresolved with evidence R3. The implication A -> B comes only from the axiom AX.
fn write_scope_submission(dir: &Path) {
    std::fs::write(
        dir.join("bindings.jsonl"),
        "{\"id\":\"x\",\"status\":\"bound\",\"class\":\"A\",\"negated\":false,\"evidence\":[\"R1\"]}\n\
         {\"id\":\"y\",\"status\":\"bound\",\"class\":\"B\",\"negated\":false,\"evidence\":[\"R2\"]}\n\
         {\"id\":\"z\",\"status\":\"unresolved\",\"class\":null,\"missing\":[\"no_date\"],\"evidence\":[\"R3\"]}\n",
    )
    .unwrap();
    std::fs::write(
        dir.join("theory.json"),
        r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"},"B":{"op":"var","id":"y"}},"constraints":[
            {"id":"C1","expr":{"op":"implies","a":{"op":"ref","name":"A"},"b":{"op":"ref","name":"B"}},"interps":null,"provenance":["AX"]}]}"#,
    )
    .unwrap();
    std::fs::write(
        dir.join("provenance.jsonl"),
        "{\"ref\":\"R1\",\"kind\":\"source\",\"record\":\"m1\",\"field\":\"question\"}\n\
         {\"ref\":\"R2\",\"kind\":\"source\",\"record\":\"m2\",\"field\":\"question\"}\n\
         {\"ref\":\"R3\",\"kind\":\"source\",\"record\":\"m3\",\"field\":\"question\"}\n\
         {\"ref\":\"AX\",\"kind\":\"axiom\",\"record\":\"ax:link\",\"field\":\"rule\"}\n",
    )
    .unwrap();
}

#[test]
fn evidence_scope_restricts_answers_and_certificates() {
    let dir = scratch("scope");
    write_scope_submission(&dir);
    let x = r#"{"op":"var","id":"x"}"#;
    let y = r#"{"op":"var","id":"y"}"#;
    let not_y = format!(r#"{{"op":"not","a":{y}}}"#);
    let queries = [
        format!(r#"{{"qid":"c_full","family":"closure","hyp":[{x}],"evidence":["R1","R2","AX"]}}"#),
        format!(r#"{{"qid":"c_noax","family":"closure","hyp":[{x}],"evidence":["R1","R2"]}}"#),
        format!(r#"{{"qid":"c_univ","family":"closure","hyp":[{x}],"universe":["x","z"],"evidence":["R1"]}}"#),
        format!(r#"{{"qid":"f_full","family":"feasibility","assume":[{x},{not_y}],"evidence":["R1","R2","AX"]}}"#),
        format!(r#"{{"qid":"f_noax","family":"feasibility","assume":[{x},{not_y}],"evidence":["R1","R2"]}}"#),
        format!(r#"{{"qid":"plain","family":"feasibility","assume":[{x},{not_y}]}}"#),
        format!(r#"{{"qid":"oos_expr","family":"feasibility","assume":[{{"op":"var","id":"z"}}],"evidence":["R1","R2"]}}"#),
        r#"{"qid":"oos_unknown","family":"feasibility","assume":[{"op":"var","id":"x"}],"evidence":["R1","NOPE"]}"#.to_string(),
        r#"{"qid":"oos_empty","family":"feasibility","assume":[{"op":"var","id":"x"}],"evidence":[]}"#.to_string(),
    ];
    let q = dir.join("q.jsonl");
    std::fs::write(&q, queries.join("\n")).unwrap();
    let a = dir.join("a.jsonl");
    let o = run_eval(&dir, &q, &a);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let text = std::fs::read_to_string(&a).unwrap();
    let got: Vec<&str> = text.lines().collect();
    assert_eq!(got[0], r#"{"qid":"c_full","labels":{"ENTAILED":["x","y"],"REFUTED":[],"CONTINGENT":[],"UNRESOLVED":[]}}"#);
    assert_eq!(got[1], r#"{"qid":"c_noax","labels":{"ENTAILED":["x"],"REFUTED":[],"CONTINGENT":["y"],"UNRESOLVED":[]}}"#);
    // z is outside the scope, but an explicit universe entry is unresolved rather than an error.
    assert_eq!(got[2], r#"{"qid":"c_univ","labels":{"ENTAILED":["x"],"REFUTED":[],"CONTINGENT":[],"UNRESOLVED":["z"]}}"#);
    assert!(got[3].contains(r#""decision":"UNSAT""#), "{}", got[3]);
    assert!(got[4].contains(r#""decision":"SAT""#), "{}", got[4]);
    assert!(got[5].contains(r#""decision":"UNSAT""#), "{}", got[5]);
    assert_eq!(got[6], r#"{"qid":"oos_expr","error":"out_of_scope"}"#);
    assert_eq!(got[7], r#"{"qid":"oos_unknown","error":"out_of_scope"}"#);
    assert_eq!(got[8], r#"{"qid":"oos_empty","error":"out_of_scope"}"#);
    // Same workload again: byte-identical.
    let a2 = dir.join("a2.jsonl");
    assert_eq!(run_eval(&dir, &q, &a2).status.code(), Some(0));
    assert_eq!(std::fs::read(&a2).unwrap(), std::fs::read(&a).unwrap());
    let monolith = dir.join("monolith.jsonl");
    let mono = bin()
        .env("MB_MONOLITH", "1")
        .args(["eval", dir.to_str().unwrap(), q.to_str().unwrap(), monolith.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(mono.status.code(), Some(0), "{}", stderr(&mono));
    assert_eq!(std::fs::read(&monolith).unwrap(), std::fs::read(&a).unwrap());

    // Certificates are checked against gold restricted to the same scope.
    let v = dir.join("v.jsonl");
    assert_eq!(run_check(&dir, &q, &a, &v).status.code(), Some(0));
    let verdicts = std::fs::read_to_string(&v).unwrap();
    let reasons: Vec<&str> = verdicts.lines().collect();
    assert_eq!(reasons[0], r#"{"qid":"c_full","cert_valid":false,"reason":"family_not_certified"}"#);
    assert_eq!(reasons[3], r#"{"qid":"f_full","cert_valid":true,"reason":"ok"}"#);
    assert_eq!(reasons[4], r#"{"qid":"f_noax","cert_valid":true,"reason":"ok"}"#);
    // Error answers are judged before any scope lookup, so they never reveal scope membership.
    assert_eq!(reasons[6], r#"{"qid":"oos_expr","cert_valid":false,"reason":"answer_error"}"#);
    assert_eq!(reasons[7], r#"{"qid":"oos_unknown","cert_valid":false,"reason":"answer_error"}"#);
    // A well-formed answer to a query outside its scope is query_invalid.
    let well_formed = dir.join("wf.jsonl");
    std::fs::write(&well_formed, "{\"qid\":\"oos_expr\",\"decision\":\"SAT\",\"witness\":{\"z\":true},\"core\":null,\"missing\":[]}\n").unwrap();
    let v4 = dir.join("v4.jsonl");
    assert_eq!(run_check(&dir, &q, &well_formed, &v4).status.code(), Some(0));
    assert!(std::fs::read_to_string(&v4).unwrap().contains(r#""qid":"oos_expr","cert_valid":false,"reason":"query_invalid""#));
    // A malformed answer (SAT without a witness) is the same whatever the scope.
    let malformed = dir.join("mf.jsonl");
    std::fs::write(&malformed, "{\"qid\":\"oos_expr\",\"decision\":\"SAT\"}\n{\"qid\":\"f_noax\",\"decision\":\"SAT\"}\n").unwrap();
    let v5 = dir.join("v5.jsonl");
    assert_eq!(run_check(&dir, &q, &malformed, &v5).status.code(), Some(0));
    let v5_text = std::fs::read_to_string(&v5).unwrap();
    assert!(v5_text.contains(r#""qid":"oos_expr","cert_valid":false,"reason":"witness_invalid""#), "{v5_text}");
    assert!(v5_text.contains(r#""qid":"f_noax","cert_valid":false,"reason":"witness_invalid""#), "{v5_text}");
    // An UNSAT core from the full scope does not hold without the axiom ...
    let q_noax = dir.join("q_noax.jsonl");
    std::fs::write(&q_noax, format!(r#"{{"qid":"f_full","family":"feasibility","assume":[{x},{not_y}],"evidence":["R1","R2"]}}"#)).unwrap();
    let a_full = dir.join("a_full.jsonl");
    std::fs::write(&a_full, format!("{}\n", got[3])).unwrap();
    let v2 = dir.join("v2.jsonl");
    assert_eq!(run_check(&dir, &q_noax, &a_full, &v2).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v2).unwrap().trim(), r#"{"qid":"f_full","cert_valid":false,"reason":"core_not_unsat"}"#);
    // ... and a SAT witness from the restricted scope is not a model of the full theory.
    let q_full = dir.join("q_full.jsonl");
    std::fs::write(&q_full, format!(r#"{{"qid":"f_noax","family":"feasibility","assume":[{x},{not_y}],"evidence":["R1","R2","AX"]}}"#)).unwrap();
    let a_noax = dir.join("a_noax.jsonl");
    std::fs::write(&a_noax, format!("{}\n", got[4])).unwrap();
    let v3 = dir.join("v3.jsonl");
    assert_eq!(run_check(&dir, &q_full, &a_noax, &v3).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v3).unwrap().trim(), r#"{"qid":"f_noax","cert_valid":false,"reason":"witness_invalid"}"#);
}

/// Required shipped toy10k fixture root (`queries.jsonl` and `gold/`).
fn shipped() -> PathBuf {
    let fx = "toy10k";
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures").join(fx);
    assert!(root.is_dir(), "required shipped fixture {fx} is missing: {}", root.display());
    root
}

/// Bytes of a fixture file that must exist (a missing or unreadable file fails the test).
fn fixture_bytes(path: &Path) -> Vec<u8> {
    std::fs::read(path).unwrap_or_else(|e| panic!("fixture file {}: {e}", path.display()))
}

/// Every non-blank line of a fixture file, parsed as JSON.
fn fixture_lines(path: &Path) -> Vec<serde_json::Value> {
    String::from_utf8(fixture_bytes(path))
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap_or_else(|e| panic!("{}: {e}", path.display())))
        .collect()
}

#[test]
fn evidence_free_gold_answers_and_verdicts_are_byte_identical() {
    // The shipped answers and verdicts were produced without evidence scopes; they must not move.
    let root = shipped();
    let gold = root.join("gold");
    let queries = root.join("queries.jsonl");
    let dir = scratch("golden-toy10k");
    let a = dir.join("a.jsonl");
    assert_eq!(run_eval(&gold, &queries, &a).status.code(), Some(0), "toy10k");
    assert!(fixture_bytes(&a) == fixture_bytes(&gold.join("answers.jsonl")), "toy10k: answers differ");
    let v = dir.join("v.jsonl");
    assert_eq!(run_check(&gold, &queries, &gold.join("answers.jsonl"), &v).status.code(), Some(0), "toy10k");
    assert!(fixture_bytes(&v) == fixture_bytes(&gold.join("verdicts.jsonl")), "toy10k: verdicts differ");
}

/// The queries of a fixture, one JSON line each, with `evidence` set to `refs`.
fn with_evidence(queries: &Path, refs: &[String]) -> String {
    fixture_lines(queries)
        .into_iter()
        .map(|mut v| {
            v["evidence"] = serde_json::json!(refs);
            format!("{v}\n")
        })
        .collect()
}

#[test]
fn complete_evidence_scope_matches_unscoped_shipped_gold() {
    let root = shipped();
    let fx = "toy10k";
    let gold = root.join("gold");
    // Every provenance ref in scope: the scoped answers and verdicts must match the unscoped ones.
    let refs: Vec<String> = fixture_lines(&gold.join("provenance.jsonl"))
        .iter()
        .map(|v| v["ref"].as_str().unwrap_or_else(|| panic!("{fx}: provenance line without ref")).to_string())
        .collect();
    let dir = scratch(&format!("complete-{fx}"));
    let scoped_q = dir.join("scoped.jsonl");
    std::fs::write(&scoped_q, with_evidence(&root.join("queries.jsonl"), &refs)).unwrap();
    let plain_q = root.join("queries.jsonl");
    let plain = dir.join("plain.jsonl");
    assert_eq!(run_eval(&gold, &plain_q, &plain).status.code(), Some(0), "{fx}");
    let s1 = dir.join("s1.jsonl");
    assert_eq!(run_eval(&gold, &scoped_q, &s1).status.code(), Some(0), "{fx}");
    assert!(fixture_bytes(&s1) == fixture_bytes(&plain), "{fx}: scoped answers differ from unscoped");
    let v_plain = dir.join("vp.jsonl");
    assert_eq!(run_check(&gold, &plain_q, &gold.join("answers.jsonl"), &v_plain).status.code(), Some(0), "{fx}");
    let v_scoped = dir.join("vs.jsonl");
    assert_eq!(run_check(&gold, &scoped_q, &gold.join("answers.jsonl"), &v_scoped).status.code(), Some(0), "{fx}");
    assert!(fixture_bytes(&v_scoped) == fixture_bytes(&v_plain), "{fx}: scoped verdicts differ");
}

#[test]
fn under_cited_provenance_and_partition_scope_certificates() {
    let dir = scratch("undercited");
    std::fs::write(
        dir.join("bindings.jsonl"),
        "{\"id\":\"x\",\"status\":\"bound\",\"class\":\"A\",\"negated\":false,\"evidence\":[\"R1\"]}\n\
         {\"id\":\"w\",\"status\":\"bound\",\"class\":\"W\",\"negated\":false,\"evidence\":[\"R2\"]}\n",
    )
    .unwrap();
    // C1 and C2 cite only AX, but both reach x (through A), which lies outside the scope [R2, AX].
    std::fs::write(
        dir.join("theory.json"),
        r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"},"W":{"op":"var","id":"w"}},"constraints":[
            {"id":"C1","expr":{"op":"ref","name":"A"},"interps":null,"provenance":["AX"]},
            {"id":"C2","expr":{"op":"implies","a":{"op":"ref","name":"A"},"b":{"op":"ref","name":"W"}},"interps":null,"provenance":["AX"]}]}"#,
    )
    .unwrap();
    std::fs::write(
        dir.join("provenance.jsonl"),
        "{\"ref\":\"R1\",\"kind\":\"source\",\"record\":\"m1\",\"field\":\"question\"}\n\
         {\"ref\":\"R2\",\"kind\":\"source\",\"record\":\"m2\",\"field\":\"question\"}\n\
         {\"ref\":\"AX\",\"kind\":\"axiom\",\"record\":\"ax:link\",\"field\":\"rule\"}\n",
    )
    .unwrap();
    let not_w = r#"{"op":"not","a":{"op":"var","id":"w"}}"#;
    let x = r#"{"op":"var","id":"x"}"#;
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        format!(
            "{{\"qid\":\"u\",\"family\":\"feasibility\",\"assume\":[{not_w}],\"evidence\":[\"R2\",\"AX\"]}}\n\
             {{\"qid\":\"pp\",\"family\":\"partition\",\"members\":[{x},{not_w}],\"claim\":\"exclusive\",\"target\":null,\"evidence\":[\"R1\",\"R2\",\"AX\"]}}\n"
        ),
    )
    .unwrap();
    let a = dir.join("a.jsonl");
    let o = run_eval(&dir, &q, &a);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    let text = std::fs::read_to_string(&a).unwrap();
    let got: Vec<&str> = text.lines().collect();
    // Scope [R2, AX]: C1 and C2 reach x, which is out of scope, so both are dropped and `not w` is SAT.
    assert!(got[0].contains(r#""decision":"SAT""#) && got[0].contains(r#""w":false"#), "{}", got[0]);
    // Full scope: x is forced and x implies w, so the exclusive pair cannot both hold: the claim is true.
    assert!(got[1].contains(r#""decision":"true""#), "{}", got[1]);
    // Certificates: the SAT answer checks under its scope, and fails against the full theory.
    let v = dir.join("v.jsonl");
    assert_eq!(run_check(&dir, &q, &a, &v).status.code(), Some(0));
    let verdicts = std::fs::read_to_string(&v).unwrap();
    assert!(verdicts.contains(r#"{"qid":"u","cert_valid":true,"reason":"ok"}"#), "{verdicts}");
    assert!(verdicts.contains(r#"{"qid":"pp","cert_valid":true,"reason":"ok"}"#), "{verdicts}");
    let full_q = dir.join("full.jsonl");
    std::fs::write(&full_q, format!("{{\"qid\":\"u\",\"family\":\"feasibility\",\"assume\":[{not_w}]}}\n")).unwrap();
    let v2 = dir.join("v2.jsonl");
    assert_eq!(run_check(&dir, &full_q, &a, &v2).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v2).unwrap().trim(), r#"{"qid":"u","cert_valid":false,"reason":"witness_invalid"}"#);
    // The partition is `true` under the full theory; a scope without the axiom makes it `false`, so the
    // full-theory answer does not certify there.
    let pscope = dir.join("pscope.jsonl");
    std::fs::write(&pscope, format!("{{\"qid\":\"pp\",\"family\":\"partition\",\"members\":[{x},{not_w}],\"claim\":\"exclusive\",\"target\":null,\"evidence\":[\"R1\",\"R2\"]}}\n")).unwrap();
    let v3 = dir.join("v3.jsonl");
    assert_eq!(run_check(&dir, &pscope, &a, &v3).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v3).unwrap().trim(), r#"{"qid":"pp","cert_valid":false,"reason":"claim_not_entailed"}"#);
}

#[test]
fn null_evidence_is_a_bad_query_line() {
    let dir = fresh_sub("evnull");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"n\",\"family\":\"feasibility\",\"assume\":[],\"evidence\":null}\n{\"qid\":\"ok\",\"family\":\"feasibility\",\"assume\":[]}\n",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    assert_eq!(run_eval(&dir, &q, &out).status.code(), Some(0));
    let text = std::fs::read_to_string(&out).unwrap();
    let got: Vec<&str> = text.lines().collect();
    assert_eq!(got[0], r#"{"qid":"n","error":"bad_query"}"#);
    assert!(got[1].contains(r#""qid":"ok""#), "{}", got[1]);
    let v = outside(&dir, "v.jsonl");
    let empty = outside(&dir, "empty.jsonl");
    std::fs::write(&empty, "").unwrap();
    assert_eq!(run_check(&dir, &q, &empty, &v).status.code(), Some(0));
    assert!(std::fs::read_to_string(&v).unwrap().contains(r#"{"qid":"n","cert_valid":false,"reason":"query_invalid"}"#));
}

#[test]
fn certificates_are_deterministic_checkable_and_match_the_monolith() {
    let dir = scratch("cert-gen");
    write_scope_submission(&dir);
    let x = r#"{"op":"var","id":"x"}"#;
    let not_y = r#"{"op":"not","a":{"op":"var","id":"y"}}"#;
    let claim = format!(r#""members":[{x},{not_y}],"claim":"exclusive","target":null"#);
    let queries = [
        format!(r#"{{"qid":"p_cert","family":"partition",{claim},"certificate":true}}"#),
        format!(r#"{{"qid":"p_scoped","family":"partition",{claim},"certificate":true,"evidence":["R1","R2","AX","R3"]}}"#),
        format!(r#"{{"qid":"p_plain","family":"partition",{claim}}}"#),
        format!(r#"{{"qid":"p_false","family":"partition","members":[{x},{not_y}],"claim":"partition","target":null,"certificate":true}}"#),
        format!(r#"{{"qid":"f_cert","family":"feasibility","assume":[{x}],"certificate":true}}"#),
    ];
    let q = dir.join("q.jsonl");
    std::fs::write(&q, queries.join("\n")).unwrap();
    let a = dir.join("a.jsonl");
    assert_eq!(run_eval(&dir, &q, &a).status.code(), Some(0));
    let text = std::fs::read_to_string(&a).unwrap();
    let got: Vec<&str> = text.lines().collect();
    // The certificate is the claim's own refs: the axiom, and the two outcomes it reaches, sorted.
    assert_eq!(got[0], r#"{"qid":"p_cert","decision":"true","counterexample":null,"missing":[],"evidence":["AX","R1","R2"]}"#);
    assert_eq!(got[1], r#"{"qid":"p_scoped","decision":"true","counterexample":null,"missing":[],"evidence":["AX","R1","R2"]}"#);
    // Without the flag, and for a claim that is not true, the answer has no certificate.
    assert!(!got[2].contains("evidence") && got[2].contains(r#""decision":"true""#), "{}", got[2]);
    assert!(!got[3].contains("evidence") && got[3].contains(r#""decision":"false""#), "{}", got[3]);
    assert!(!got[4].contains("evidence"), "{}", got[4]);
    // Same bytes on a second run and under the monolithic engine.
    let a2 = dir.join("a2.jsonl");
    assert_eq!(run_eval(&dir, &q, &a2).status.code(), Some(0));
    assert_eq!(fixture_bytes(&a2), fixture_bytes(&a));
    let a3 = dir.join("a3.jsonl");
    let o = bin().env("MB_MONOLITH", "1").args(["eval", dir.to_str().unwrap(), q.to_str().unwrap(), a3.to_str().unwrap()]).output().unwrap();
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert!(fixture_bytes(&a3) == fixture_bytes(&a), "monolith differs on certificate queries");
    // Every certificate checks against the same gold; certificate-free answers keep the old check.
    let v = dir.join("v.jsonl");
    assert_eq!(run_check(&dir, &q, &a, &v).status.code(), Some(0));
    let verdicts = std::fs::read_to_string(&v).unwrap();
    assert!(verdicts.contains(r#"{"qid":"p_cert","cert_valid":true,"reason":"ok"}"#), "{verdicts}");
    assert!(verdicts.contains(r#"{"qid":"p_scoped","cert_valid":true,"reason":"ok"}"#), "{verdicts}");
    assert!(verdicts.contains(r#"{"qid":"p_plain","cert_valid":true,"reason":"ok"}"#), "{verdicts}");
}

#[test]
fn certificate_size_cap_comes_from_the_profile_in_eval_and_check() {
    let dir = scratch("cert-cap");
    write_scope_submission(&dir);
    let x = r#"{"op":"var","id":"x"}"#;
    let not_y = r#"{"op":"not","a":{"op":"var","id":"y"}}"#;
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!(r#"{{"qid":"p","family":"partition","members":[{x},{not_y}],"claim":"exclusive","target":null,"certificate":true}}"#)).unwrap();
    let a = dir.join("a.jsonl");
    assert_eq!(run_eval(&dir, &q, &a).status.code(), Some(0));
    assert!(std::fs::read_to_string(&a).unwrap().contains(r#""evidence":["AX","R1","R2"]"#));
    // A cap of 2 refs: eval cannot produce the three-ref certificate, and check refuses the one it has.
    std::fs::write(dir.join("profile.json"), r#"{"max_certificate_refs":2}"#).unwrap();
    let capped = dir.join("capped.jsonl");
    assert_eq!(run_eval(&dir, &q, &capped).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&capped).unwrap().trim(), r#"{"qid":"p","error":"certificate_too_large"}"#);
    let v = dir.join("v.jsonl");
    assert_eq!(run_check(&dir, &q, &a, &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"p","cert_valid":false,"reason":"certificate_too_large"}"#);
    // Zero is not a cap: the profile is invalid.
    std::fs::write(dir.join("profile.json"), r#"{"max_certificate_refs":0}"#).unwrap();
    let bad = dir.join("bad.jsonl");
    let o = run_eval(&dir, &q, &bad);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(stderr(&o).contains("profile_invalid"), "{}", stderr(&o));
}

#[test]
fn certificate_null_is_a_bad_query_line() {
    let dir = fresh_sub("certnull");
    let q = dir.join("q.jsonl");
    std::fs::write(
        &q,
        "{\"qid\":\"n\",\"family\":\"partition\",\"members\":[],\"claim\":\"exclusive\",\"target\":null,\"certificate\":null}\n",
    )
    .unwrap();
    let out = outside(&dir, "a.jsonl");
    assert_eq!(run_eval(&dir, &q, &out).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&out).unwrap().trim(), r#"{"qid":"n","error":"bad_query"}"#);
}

#[test]
fn check_flag_sets_the_certificate_cap_and_guards_its_output() {
    let dir = scratch("cert-flag");
    write_scope_submission(&dir);
    let x = r#"{"op":"var","id":"x"}"#;
    let not_y = r#"{"op":"not","a":{"op":"var","id":"y"}}"#;
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!(r#"{{"qid":"p","family":"partition","members":[{x},{not_y}],"claim":"exclusive","target":null,"certificate":true}}"#)).unwrap();
    let a = dir.join("a.jsonl");
    assert_eq!(run_eval(&dir, &q, &a).status.code(), Some(0));
    assert!(std::fs::read_to_string(&a).unwrap().contains(r#""evidence":["AX","R1","R2"]"#));
    let v = outside(&dir, "v.jsonl");
    let check = |cap: &str, out: &Path| {
        bin()
            .args(["check", dir.to_str().unwrap(), q.to_str().unwrap(), a.to_str().unwrap(), out.to_str().unwrap(), "--max-certificate-refs", cap])
            .output()
            .unwrap()
    };
    // The flag overrides the gold profile: profile 3 with flag 2 refuses the three-ref certificate ...
    std::fs::write(dir.join("profile.json"), r#"{"max_certificate_refs":3}"#).unwrap();
    let o = check("2", &v);
    assert_eq!(o.status.code(), Some(0), "{}", stderr(&o));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"p","cert_valid":false,"reason":"certificate_too_large"}"#);
    // ... and profile 2 with flag 3 accepts it.
    std::fs::write(dir.join("profile.json"), r#"{"max_certificate_refs":2}"#).unwrap();
    assert_eq!(check("3", &v).status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&v).unwrap().trim(), r#"{"qid":"p","cert_valid":true,"reason":"ok"}"#);
    // A cap must be an integer of at least 1.
    assert_eq!(check("0", &v).status.code(), Some(2));
    assert_eq!(check("many", &v).status.code(), Some(2));
    // An output that names the answers file is refused before anything runs, and the answers survive.
    let before = fixture_bytes(&a);
    let o = bin()
        .args(["check", dir.to_str().unwrap(), q.to_str().unwrap(), a.to_str().unwrap(), a.to_str().unwrap(), "--max-certificate-refs", "3"])
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(fixture_bytes(&a) == before, "the answers file was overwritten");
}

const DIGEST_THEORY: &str = r#"{"interpretations":["base"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[]}"#;
const DIGEST_QUERY_C: &str = r#"{"qid":"c","family":"closure","hyp":[]}"#;
const DIGEST_QUERY_D: &str = r#"{"qid":"d","family":"closure","hyp":[]}"#;

/// Runs `eval` and returns its stdout and the answers bytes; the run must succeed.
fn eval_run(queries: &Path, dir: &Path, answers: &Path) -> (String, Vec<u8>) {
    let st = bin()
        .args(["eval", dir.to_str().unwrap(), queries.to_str().unwrap(), answers.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(st.status.code(), Some(0), "stderr: {}", String::from_utf8_lossy(&st.stderr));
    (String::from_utf8(st.stdout).unwrap(), std::fs::read(answers).unwrap())
}

fn sha_of(path: &Path) -> String {
    mb_ir::sha256::sha256_hex(&std::fs::read(path).unwrap())
}

#[test]
fn eval_stdout_is_exactly_one_digest_line_with_the_file_digests() {
    let dir = scratch("digest-line");
    write_submission(&dir, DIGEST_THEORY);
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!("{DIGEST_QUERY_C}\n")).unwrap();
    let (stdout, answers) = eval_run(&q, &dir, &dir.join("a.jsonl"));
    let want = format!(
        "{{\"mb_engine_digests\":{{\"theory.json\":\"{}\",\"bindings.jsonl\":\"{}\",\"provenance.jsonl\":\"{}\",\"queries\":\"{}\",\"answers\":\"{}\"}},\"version\":\"{}\"}}\n",
        sha_of(&dir.join("theory.json")),
        sha_of(&dir.join("bindings.jsonl")),
        sha_of(&dir.join("provenance.jsonl")),
        sha_of(&q),
        mb_ir::sha256::sha256_hex(&answers),
        env!("CARGO_PKG_VERSION"),
    );
    assert_eq!(stdout, want);
}

#[test]
fn profile_json_is_digested_when_it_is_used() {
    let dir = scratch("digest-profile");
    write_submission(&dir, DIGEST_THEORY);
    std::fs::write(dir.join("profile.json"), "{}").unwrap();
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!("{DIGEST_QUERY_C}\n")).unwrap();
    let (stdout, answers) = eval_run(&q, &dir, &dir.join("a.jsonl"));
    assert_eq!(stdout.matches('\n').count(), 1, "{stdout:?}");
    let want = format!(
        "{{\"mb_engine_digests\":{{\"theory.json\":\"{}\",\"bindings.jsonl\":\"{}\",\"provenance.jsonl\":\"{}\",\"profile.json\":\"{}\",\"queries\":\"{}\",\"answers\":\"{}\"}},\"version\":\"{}\"}}\n",
        sha_of(&dir.join("theory.json")),
        sha_of(&dir.join("bindings.jsonl")),
        sha_of(&dir.join("provenance.jsonl")),
        sha_of(&dir.join("profile.json")),
        sha_of(&q),
        mb_ir::sha256::sha256_hex(&answers),
        env!("CARGO_PKG_VERSION"),
    );
    assert_eq!(stdout, want);
}

#[test]
fn queries_digest_covers_the_raw_bytes_with_crlf_and_no_final_newline() {
    let dir = scratch("digest-queries");
    write_submission(&dir, DIGEST_THEORY);
    let lf = dir.join("lf.jsonl");
    let crlf = dir.join("crlf.jsonl");
    std::fs::write(&lf, format!("{DIGEST_QUERY_C}\n{DIGEST_QUERY_D}\n")).unwrap();
    std::fs::write(&crlf, format!("{DIGEST_QUERY_C}\r\n{DIGEST_QUERY_D}")).unwrap();
    let (_, answers_lf) = eval_run(&lf, &dir, &dir.join("a_lf.jsonl"));
    let (stdout, answers_crlf) = eval_run(&crlf, &dir, &dir.join("a_crlf.jsonl"));
    assert_eq!(answers_crlf, answers_lf, "CRLF and a missing final newline must not change the answers");
    let v: serde_json::Value = serde_json::from_str(stdout.trim_end()).unwrap();
    assert_eq!(v["mb_engine_digests"]["queries"], sha_of(&crlf));
    assert_ne!(v["mb_engine_digests"]["queries"], sha_of(&lf));
}

#[test]
fn eval_and_check_error_paths_print_nothing_on_stdout() {
    // Exit 1 (inconsistent base) from eval and from check, then exit 2 (missing directory, duplicate qid).
    let dir = scratch("stdout-errors");
    write_submission(
        &dir,
        r#"{"interpretations":["base","alt"],"aliases":{"A":{"op":"var","id":"x"}},"constraints":[
            {"id":"C1","expr":{"op":"var","id":"x"},"interps":null,"provenance":["R1"]},
            {"id":"C2","expr":{"op":"not","a":{"op":"var","id":"x"}},"interps":["alt"],"provenance":["R1"]}]}"#,
    );
    let q = dir.join("q.jsonl");
    std::fs::write(&q, format!("{DIGEST_QUERY_C}\n")).unwrap();
    let (a, v) = (dir.join("a.jsonl"), dir.join("v.jsonl"));
    let o = run_eval(&dir, &q, &a);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(o.stdout.is_empty(), "eval exit 1 wrote stdout: {:?}", String::from_utf8_lossy(&o.stdout));
    let o = run_check(&dir, &q, &a, &v);
    assert_eq!(o.status.code(), Some(1), "{}", stderr(&o));
    assert!(o.stdout.is_empty(), "check exit 1 wrote stdout: {:?}", String::from_utf8_lossy(&o.stdout));
    let o = run_eval(&dir.join("missing"), &q, &a);
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(o.stdout.is_empty(), "eval exit 2 wrote stdout: {:?}", String::from_utf8_lossy(&o.stdout));

    let ok = scratch("stdout-duplicate");
    write_submission(&ok, DIGEST_THEORY);
    let dup = ok.join("dup.jsonl");
    std::fs::write(&dup, format!("{DIGEST_QUERY_C}\n{DIGEST_QUERY_C}\n")).unwrap();
    let o = run_eval(&ok, &dup, &ok.join("a.jsonl"));
    assert_eq!(o.status.code(), Some(2), "{}", stderr(&o));
    assert!(o.stdout.is_empty(), "eval duplicate qid wrote stdout: {:?}", String::from_utf8_lossy(&o.stdout));
}
