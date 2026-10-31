//! Certificate verification against a gold submission (`mb-engine check`).
//!
//! Feasibility SAT: the witness, with the assumptions, must extend to a model in every gold
//! interpretation, and be maximal: no other query-support ID can be added (either polarity) while
//! still extending. UNSAT: the core must be unsatisfiable in every gold interpretation. Partition
//! true: the violation must be unsatisfiable in every interpretation. Partition false: the
//! counterexample plus the violation must extend in every interpretation and be maximal. UNRESOLVED
//! is certified when the gold interpretations disagree and the claimed `missing` codes overlap the
//! gold codes of the query support (or both are empty). Closure and selection carry no certificate
//! in v0.1 and are reported as not certified.
//!
//! A `true` partition answer may carry `evidence`, a certificate: the claim must be entailed in every
//! gold interpretation restricted to those refs (`World::with_evidence`). Refs that are not gold provenance, or
//! lie outside the query's own scope, are dropped before the check (see `usable_refs`). Without `evidence` the claim is checked against the full gold (or the
//! query's scope), as before. `certify` produces certificates for `eval` (`"certificate": true`).
//!
//! A query with `evidence` is verified against gold restricted to that scope (`World::with_scope`).
//! Gold-independent decisions come first: missing answers, answer errors, closure and selection, and
//! malformed answer shapes. They never depend on scope membership. A well-formed answer to a query
//! outside its scope is `query_invalid`. That leaves one residual bit: whether a well-formed answer
//! was out of scope. Gold never decides a malformed answer's reason.
//!
//! Reasons are coarse codes: they never name interpretations, IDs or gold-derived values.

use crate::engine::{set_deadline, Engine};
use crate::world::{vars_of, World};
use mb_ir::{AnswerIn, AnswerOut, Claim, Expr, Query, QueryBody, Verdict};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use varisat::Lit;

type Outcome = Result<(bool, &'static str), String>;

/// Entailment tests per certificate in `certify` (default budget, see `World::set_certificate_trials`).
/// shortcut: when the budget runs out, a certificate still above `max_certificate_refs` is `certificate_error`;
/// otherwise the valid but less minimal set found so far is kept. Raise it for claims with huge components.
pub const CERT_MAX_TRIALS: usize = 2048;

pub fn check(world: &mut World, queries: &[Query], answers: &HashMap<String, AnswerIn>) -> Vec<Verdict> {
    queries
        .iter()
        .map(|q| {
            set_deadline(world.query_deadline());
            let r = verify(world, q, answers.get(&q.qid));
            set_deadline(None);
            let (cert_valid, reason) = match r {
                Ok(r) => r,
                Err(e) => (false, error_reason(&e)),
            };
            Verdict {
                qid: q.qid.clone(),
                cert_valid,
                reason: reason.into(),
            }
        })
        .collect()
}

/// Reason for an engine error raised while verifying a query.
pub fn error_reason(e: &str) -> &'static str {
    if e == "timeout" {
        "timeout"
    } else if e == "weight_overflow" || e == "out_of_scope" || e.starts_with("unknown_id") || e.starts_with("unknown_alias") {
        "query_invalid"
    } else {
        "engine_error"
    }
}

fn verify(world: &mut World, q: &Query, ans: Option<&AnswerIn>) -> Outcome {
    if q.uses_ref() {
        return Ok((false, "query_invalid"));
    }
    let Some(a) = ans else {
        return Ok((false, "missing_answer"));
    };
    if matches!(q.body, QueryBody::Closure { .. } | QueryBody::Selection { .. }) {
        return Ok((false, "family_not_certified"));
    }
    if a.error.is_some() {
        return Ok((false, "answer_error"));
    }
    if let Some(reason) = shape_reason(q, a) {
        return Ok((false, reason));
    }
    // A query that asks for a certificate gets no credit for a `true` answer without one.
    if q.certificate && a.decision.as_deref() == Some("true") && a.evidence.is_none() {
        return Ok((false, "certificate_missing"));
    }
    if let (QueryBody::Partition { members, claim, target }, Some("true"), Some(cert)) = (&q.body, a.decision.as_deref(), a.evidence.as_deref()) {
        return verify_certificate(world, q, members, *claim, target.as_ref(), cert);
    }
    if q.evidence.is_some() {
        return world.with_scope(q, |sw, _| verify_gold(sw, q, a)).and_then(|r| r);
    }
    verify_gold(world, q, a)
}

/// Checks a `true` partition certificate. The size cap (on the distinct refs as written) comes first, then the
/// claim's outcomes must exist. Refs that are not gold provenance or lie outside a scoped query's scope are
/// dropped silently: the restricted theory only shrinks, so the claim must be entailed by the rest, else
/// `claim_not_entailed` (the certificate is too small, or names nothing useful). A claim outcome outside the
/// certificate scope is not entailed.
fn verify_certificate(world: &mut World, q: &Query, members: &[Expr], claim: Claim, target: Option<&Expr>, cert: &[String]) -> Outcome {
    let distinct: BTreeSet<&str> = cert.iter().map(String::as_str).collect();
    if world.max_cert_refs.is_some_and(|m| distinct.len() as u64 > m) {
        return Ok((false, "certificate_too_large"));
    }
    if q.evidence.is_some() {
        world.with_scope(q, |_, _| ())?;
    }
    let vars = claim_vars(members, target);
    if let Some(v) = vars.iter().find(|v| !world.has_binding(v)) {
        return Err(format!("unknown_id {v}"));
    }
    let refs = usable_refs(world, q, cert);
    let entailed = holds_on(world, &refs, &vars, members, claim, target)?;
    Ok(if entailed { (true, "ok") } else { (false, "claim_not_entailed") })
}

/// The certificate's refs the check may use: gold provenance refs inside the query's scope, sorted and
/// deduplicated. The query's evidence goes into a set once per call.
fn usable_refs(world: &mut World, q: &Query, cert: &[String]) -> Vec<String> {
    let scope: Option<HashSet<&str>> = q.evidence.as_ref().map(|ev| ev.iter().map(String::as_str).collect());
    let mut out: Vec<String> = cert
        .iter()
        .filter(|r| scope.as_ref().map_or(true, |s| s.contains(r.as_str())) && world.is_gold_ref(r))
        .cloned()
        .collect();
    out.sort();
    out.dedup();
    out
}

/// Outcome IDs of a partition's members and target.
fn claim_vars<'e>(members: &'e [Expr], target: Option<&'e Expr>) -> Vec<&'e str> {
    let exprs: Vec<&Expr> = members.iter().chain(target).collect();
    vars_of(&exprs)
}

/// Whether the claim holds in every interpretation of gold restricted to the evidence `refs` (and to the claim's
/// own components, which is all its entailment depends on).
pub(crate) fn holds_on(world: &mut World, refs: &[String], vars: &[&str], members: &[Expr], claim: Claim, target: Option<&Expr>) -> Result<bool, String> {
    world.with_evidence(refs, vars, |cw| {
        if !vars.iter().all(|v| cw.has_binding(v)) {
            return Ok(false);
        }
        cw.with_ids(vars.to_vec(), |eng| claim_holds(eng, members, claim, target))?
    })
}

/// `holds_on` for one candidate, counted against the trial budget. `None` once the budget is spent.
fn holds_within(
    world: &mut World,
    refs: &[String],
    vars: &[&str],
    members: &[Expr],
    claim: Claim,
    target: Option<&Expr>,
    left: &mut usize,
) -> Result<Option<bool>, String> {
    if *left == 0 {
        return Ok(None);
    }
    *left -= 1;
    holds_on(world, refs, vars, members, claim, target).map(Some)
}

/// The claim's violation is unsatisfiable in every interpretation of the engine's theory.
pub(crate) fn claim_holds(eng: &mut Engine, members: &[Expr], claim: Claim, target: Option<&Expr>) -> Result<bool, String> {
    let ml = eng.lits(members)?;
    let t = match target {
        Some(e) => eng.lit(e)?,
        None => eng.konst(true),
    };
    let v = eng.violation(&ml, t, claim)?;
    Ok(!eng.per_interp_sat(&[v])?.iter().any(|&r| r))
}

/// `eval` with `"certificate": true` on a `true` partition answer: attaches `evidence`, refs whose scope still
/// entails the claim. Other answers pass through. The caller armed the query's deadline, so the search spends the
/// same budget as the answer. A certificate over `max_certificate_refs` is `certificate_too_large`, or
/// `certificate_error` when the trial budget ran out before it was known to be minimal. Other failures are
/// `certificate_error` (a timeout stays `timeout`).
pub fn certify(world: &mut World, q: &Query, mut ans: AnswerOut) -> AnswerOut {
    let QueryBody::Partition { members, claim, target } = &q.body else {
        return ans;
    };
    let AnswerOut::Partition { decision, evidence, .. } = &mut ans else {
        return ans;
    };
    if !q.certificate || decision.as_str() != "true" {
        return ans;
    }
    let cap = world.max_cert_refs;
    match minimal_certificate(world, q, members, *claim, target.as_ref()) {
        Ok((refs, exhausted)) => {
            let over = cap.is_some_and(|m| refs.len() as u64 > m);
            if over && exhausted {
                AnswerOut::Error { qid: q.qid.clone(), error: "certificate_error".into() }
            } else if over {
                AnswerOut::Error { qid: q.qid.clone(), error: "certificate_too_large".into() }
            } else {
                *evidence = Some(refs);
                ans
            }
        }
        Err(e) => AnswerOut::Error {
            qid: q.qid.clone(),
            error: if e == "timeout" { e } else { "certificate_error".into() },
        },
    }
}

/// The certificate search. The start is the refs the claim can reach from its outcomes (else every ref of its
/// components), then `ddmin` deletes chunks of it. Every set is entailment-tested on gold restricted to it, so the
/// result always entails the claim. Returns the set and whether the budget ran out before it was known minimal.
/// A scoped query searches inside its own scope.
fn minimal_certificate(world: &mut World, q: &Query, members: &[Expr], claim: Claim, target: Option<&Expr>) -> Result<(Vec<String>, bool), String> {
    let vars = claim_vars(members, target);
    if let Some(v) = vars.iter().find(|v| !world.has_binding(v)) {
        return Err(format!("unknown_id {v}"));
    }
    let (reach, touched) = match &q.evidence {
        Some(_) => world.with_scope(q, |sw, _| (sw.reach_refs(&vars), sw.touched_refs(&vars)))?,
        None => (world.reach_refs(&vars), world.touched_refs(&vars)),
    };
    let scope: Option<HashSet<&str>> = q.evidence.as_ref().map(|ev| ev.iter().map(String::as_str).collect());
    let inside = |set: BTreeSet<String>| -> Vec<String> {
        set.into_iter().filter(|r| scope.as_ref().map_or(true, |s| s.contains(r.as_str()))).collect()
    };
    let (seed, full) = (inside(reach), inside(touched));
    let mut left = world.cert_trials;
    let mut keeps = |refs: &[String]| holds_within(world, refs, &vars, members, claim, target, &mut left);
    let start = match keeps(&seed)? {
        Some(true) => seed,
        Some(false) => match keeps(&full)? {
            Some(true) => full,
            Some(false) => return Err("certificate_error: the claim's components do not entail it".into()),
            None => return Err("certificate_error: trial budget exhausted".into()),
        },
        None => return Err("certificate_error: trial budget exhausted".into()),
    };
    ddmin(start, &mut keeps)
}

/// Zeller's delta debugging over `cur`, a list that `keeps`. Returns a kept list and whether the budget ran out
/// first (then the list is not known to be 1-minimal). `keeps` is monotone: a superset of a kept list is kept.
/// That holds for entailment because the restricted theory of a larger ref set has every constraint and
/// equivalence of a smaller one, so complements may be tested. The test count is O(k log n) for k needed refs.
fn ddmin(mut cur: Vec<String>, keeps: &mut dyn FnMut(&[String]) -> Result<Option<bool>, String>) -> Result<(Vec<String>, bool), String> {
    let mut n = 2;
    while cur.len() >= 2 {
        let parts = chunks(cur.len(), n);
        let mut subset = None;
        for r in &parts {
            match keeps(&cur[r.clone()])? {
                None => return Ok((cur, true)),
                Some(true) => {
                    subset = Some(cur[r.clone()].to_vec());
                    break;
                }
                Some(false) => {}
            }
        }
        if let Some(s) = subset {
            cur = s;
            n = 2;
            continue;
        }
        let mut complement = None;
        for r in &parts {
            let rest: Vec<String> = cur.iter().enumerate().filter(|(i, _)| !r.contains(i)).map(|(_, s)| s.clone()).collect();
            match keeps(&rest)? {
                None => return Ok((cur, true)),
                Some(true) => {
                    complement = Some(rest);
                    break;
                }
                Some(false) => {}
            }
        }
        if let Some(c) = complement {
            cur = c;
            n = (n - 1).max(2);
            continue;
        }
        if n >= cur.len() {
            break;
        }
        n = (2 * n).min(cur.len());
    }
    if cur.len() == 1 {
        match keeps(&[])? {
            None => return Ok((cur, true)),
            Some(true) => cur.clear(),
            Some(false) => {}
        }
    }
    Ok((cur, false))
}

/// Index ranges splitting `len` items into `n` near-equal chunks.
fn chunks(len: usize, n: usize) -> Vec<std::ops::Range<usize>> {
    (0..n).map(|i| i * len / n..(i + 1) * len / n).filter(|r| !r.is_empty()).collect()
}

/// Reason for an answer whose shape is wrong for its query, judged without gold: such an answer gets
/// this reason in every scope.
fn shape_reason(q: &Query, a: &AnswerIn) -> Option<&'static str> {
    let valid: &[&str] = match q.body {
        QueryBody::Feasibility { .. } => &["SAT", "UNSAT", "UNRESOLVED"],
        QueryBody::Partition { .. } => &["true", "false", "UNRESOLVED"],
        _ => return None,
    };
    let Some(decision) = a.decision.as_deref().filter(|d| valid.contains(d)) else {
        return Some("decision_invalid");
    };
    match (&q.body, decision) {
        (QueryBody::Feasibility { .. }, "SAT") => a.witness.is_none().then_some("witness_invalid"),
        (QueryBody::Feasibility { assume }, "UNSAT") => match &a.core {
            None => Some("core_invalid"),
            Some(core) if core.iter().any(|&k| k >= assume.len()) => Some("core_invalid"),
            Some(_) => None,
        },
        (QueryBody::Partition { .. }, "false") => a.counterexample.is_none().then_some("counterexample_invalid"),
        (_, "UNRESOLVED") => missing_shape(a),
        _ => None,
    }
}

/// Claimed `missing` codes: at most 8, unique, non-empty; `null` or absent means none.
fn missing_shape(a: &AnswerIn) -> Option<&'static str> {
    let claimed = a.missing.as_deref().unwrap_or(&[]);
    let unique: HashSet<&str> = claimed.iter().map(String::as_str).collect();
    let well_formed = claimed.len() <= 8 && unique.len() == claimed.len() && claimed.iter().all(|m| !m.trim().is_empty());
    (!well_formed).then_some("missing_invalid")
}

/// The gold-dependent certificate check, against the world it is given (a scoped one for evidence).
fn verify_gold(world: &mut World, q: &Query, a: &AnswerIn) -> Outcome {
    // The engine covers the components of the query and of any certificate assignment.
    let cert = a.witness.iter().chain(a.counterexample.iter()).flat_map(|m| m.keys().map(String::as_str));
    let ids = World::query_ids(q, cert);
    match &q.body {
        QueryBody::Feasibility { assume } => world.with_ids(ids, |eng| feasibility(eng, assume, a))?,
        QueryBody::Partition {
            members,
            claim,
            target,
        } => world.with_ids(ids, |eng| partition(eng, members, *claim, target.as_ref(), a))?,
        _ => Ok((false, "family_not_certified")),
    }
}

/// Maps witness/counterexample keys to literals; unknown keys are invalid, not reported.
fn to_lits(eng: &Engine, w: &BTreeMap<String, bool>) -> Option<Vec<Lit>> {
    w.iter()
        .map(|(id, &val)| {
            let &l = eng.ids.get(id.as_str())?;
            Some(if val { l } else { !l })
        })
        .collect()
}

fn mixed(res: &[bool]) -> bool {
    res.iter().any(|&r| r) && res.iter().any(|&r| !r)
}

/// Claimed `missing` codes must overlap the gold codes of the support (or both sides are empty).
fn missing_matches(eng: &Engine, support: &BTreeSet<String>, claimed: &[String]) -> bool {
    let gold = eng.missing_codes(support);
    (gold.is_empty() && claimed.is_empty()) || claimed.iter().any(|m| gold.contains(m))
}

/// D-A maximality: no ID of `support` outside `assigned` can be added, in either polarity, to the
/// certificate `fixed` (plus `qlits`) while it still extends in every interpretation.
fn is_maximal(
    eng: &mut Engine,
    support: &BTreeSet<String>,
    assigned: &BTreeMap<String, bool>,
    fixed: &[Lit],
    qlits: &[Lit],
) -> Result<bool, String> {
    for id in support {
        if assigned.contains_key(id) {
            continue;
        }
        let l = eng.ids[id.as_str()];
        for v in [l, !l] {
            let mut f = fixed.to_vec();
            f.push(v);
            if eng.extends_everywhere(&f, qlits)? {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

fn feasibility(eng: &mut Engine, assume: &[mb_ir::Expr], a: &AnswerIn) -> Outcome {
    let al = eng.lits(assume)?;
    let refs: Vec<&mb_ir::Expr> = assume.iter().collect();
    let support = eng.support(&refs);
    match a.decision.as_deref() {
        Some("SAT") => {
            let Some(w) = a.witness.as_ref() else {
                return Ok((false, "witness_invalid"));
            };
            let Some(wl) = to_lits(eng, w) else {
                return Ok((false, "witness_invalid"));
            };
            // Certificates speak only about the query support (an out-of-support ID could hide a
            // non-maximal witness behind a value that only some interpretations accept).
            if w.keys().any(|k| !support.contains(k)) {
                return Ok((false, "witness_invalid"));
            }
            if !eng.extends_everywhere(&wl, &al)? {
                return Ok((false, "witness_invalid"));
            }
            if !is_maximal(eng, &support, w, &wl, &al)? {
                return Ok((false, "witness_not_maximal"));
            }
            Ok((true, "ok"))
        }
        Some("UNSAT") => {
            let Some(core) = &a.core else {
                return Ok((false, "core_invalid"));
            };
            if core.iter().any(|&k| k >= al.len()) {
                return Ok((false, "core_invalid"));
            }
            let cl: Vec<Lit> = core.iter().map(|&k| al[k]).collect();
            for i in 0..eng.n_interps() {
                let mut asm = eng.interp_assumptions(i);
                asm.extend_from_slice(&cl);
                if eng.sat(&asm)? {
                    return Ok((false, "core_not_unsat"));
                }
            }
            Ok((true, "ok"))
        }
        Some("UNRESOLVED") => {
            let res = eng.per_interp_sat(&al)?;
            if !mixed(&res) {
                return Ok((false, "decision_not_unresolved"));
            }
            Ok(missing_or_invalid(eng, &support, a))
        }
        _ => Ok((false, "decision_invalid")),
    }
}

/// Claimed codes: at most 8, unique, non-empty. `null` or absent means none.
fn missing_or_invalid(eng: &Engine, support: &BTreeSet<String>, a: &AnswerIn) -> (bool, &'static str) {
    let claimed = a.missing.as_deref().unwrap_or(&[]);
    let unique: HashSet<&str> = claimed.iter().map(String::as_str).collect();
    let well_formed = claimed.len() <= 8 && unique.len() == claimed.len() && claimed.iter().all(|m| !m.trim().is_empty());
    if well_formed && missing_matches(eng, support, claimed) {
        (true, "ok")
    } else {
        (false, "missing_invalid")
    }
}

fn partition(
    eng: &mut Engine,
    members: &[mb_ir::Expr],
    claim: mb_ir::Claim,
    target: Option<&mb_ir::Expr>,
    a: &AnswerIn,
) -> Outcome {
    let ml = eng.lits(members)?;
    let t = match target {
        Some(e) => eng.lit(e)?,
        None => eng.konst(true),
    };
    let v = eng.violation(&ml, t, claim)?;
    let mut refs: Vec<&mb_ir::Expr> = members.iter().collect();
    refs.extend(target);
    let support = eng.support(&refs);
    match a.decision.as_deref() {
        Some("true") => {
            let res = eng.per_interp_sat(&[v])?;
            Ok(if res.iter().any(|&r| r) {
                (false, "claim_not_entailed")
            } else {
                (true, "ok")
            })
        }
        Some("false") => {
            let Some(c) = a.counterexample.as_ref() else {
                return Ok((false, "counterexample_invalid"));
            };
            let Some(cl) = to_lits(eng, c) else {
                return Ok((false, "counterexample_invalid"));
            };
            if c.keys().any(|k| !support.contains(k)) {
                return Ok((false, "counterexample_invalid"));
            }
            if !eng.extends_everywhere(&cl, &[v])? {
                return Ok((false, "claim_not_violated"));
            }
            if !is_maximal(eng, &support, c, &cl, &[v])? {
                return Ok((false, "counterexample_not_maximal"));
            }
            Ok((true, "ok"))
        }
        Some("UNRESOLVED") => {
            let res = eng.per_interp_sat(&[v])?;
            if !mixed(&res) {
                return Ok((false, "decision_not_unresolved"));
            }
            Ok(missing_or_invalid(eng, &support, a))
        }
        _ => Ok((false, "decision_invalid")),
    }
}
