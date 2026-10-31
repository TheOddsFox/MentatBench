//! Query families (docs/ir.md "Queries" and "Answers"; mentat-bench-v0.1 section 6).
//!
//! Interpretation semantics: an interpretation is checked alone via its activation assumptions.
//! Closure keeps only interpretations consistent with the hypothesis, then merges labels across
//! survivors. Feasibility and partition answers that every interpretation agrees on carry a
//! maximal partial witness or counterexample (see `Engine::joint_model`, docs/ir.md).

use crate::engine::Engine;
use mb_ir::{AnswerOut, Claim, Expr, Labels, Query, QueryBody, Relation};
use std::collections::{BTreeMap, BTreeSet};
use varisat::Lit;

#[derive(Clone, Copy, PartialEq, Debug)]
pub(crate) enum Cls {
    Ent,
    Ref,
    Cont,
    Unr,
}

impl<'a> Engine<'a> {
    /// Answers one query. Query-level failures become `AnswerOut::Error`.
    pub fn answer(&mut self, q: &Query) -> AnswerOut {
        let qid = q.qid.clone();
        if q.uses_ref() {
            return AnswerOut::Error {
                qid,
                error: "ref_in_query".into(),
            };
        }
        let r = match &q.body {
            QueryBody::Closure { hyp } => self.closure(&qid, q.universe.as_deref(), hyp),
            QueryBody::Selection { relation, target } => {
                self.selection(&qid, q.universe.as_deref(), *relation, target)
            }
            QueryBody::Feasibility { assume } => self.feasibility(&qid, assume),
            QueryBody::Partition {
                members,
                claim,
                target,
            } => self.partition(&qid, members, *claim, target.as_ref()),
        };
        r.unwrap_or_else(|error| AnswerOut::Error { qid, error })
    }

    fn closure(
        &mut self,
        qid: &str,
        universe: Option<&[String]>,
        hyp: &[Expr],
    ) -> Result<AnswerOut, String> {
        let uni = self.universe(universe)?;
        let uv: Vec<Lit> = uni.iter().map(|u| self.ids[u.as_str()]).collect();
        let bound: Vec<bool> = uni.iter().map(|u| self.is_bound(u)).collect();
        let hl = self.lits(hyp)?;
        let mut per_interp: Vec<Vec<Cls>> = Vec::new();
        for i in 0..self.n_interps() {
            let mut base = self.interp_assumptions(i);
            base.extend_from_slice(&hl);
            if self.sat(&base)? {
                per_interp.push(self.classify(&base, &uv, &bound)?);
            }
        }
        if per_interp.is_empty() {
            return Ok(AnswerOut::Closure {
                qid: qid.into(),
                labels: None,
                error: Some("hyp_inconsistent".into()),
            });
        }
        let mut labels = Labels {
            entailed: vec![],
            refuted: vec![],
            contingent: vec![],
            unresolved: vec![],
        };
        for (j, u) in uni.iter().enumerate() {
            let first = per_interp[0][j];
            let cls = if per_interp.iter().all(|c| c[j] == first) {
                first
            } else {
                Cls::Unr
            };
            match cls {
                Cls::Ent => labels.entailed.push(u.clone()),
                Cls::Ref => labels.refuted.push(u.clone()),
                Cls::Cont => labels.contingent.push(u.clone()),
                Cls::Unr => labels.unresolved.push(u.clone()),
            }
        }
        Ok(AnswerOut::Closure {
            qid: qid.into(),
            labels: Some(labels),
            error: None,
        })
    }

    /// Per-universe-ID status under one surviving interpretation. Every SAT model marks the
    /// polarities it witnesses, so most IDs need no solve of their own. Requires the last `sat`
    /// to have been a model of `base`. Models are scanned only over IDs not yet seen in both
    /// polarities (`open`), so a SAT answer costs O(open) rather than O(|U|).
    fn classify(&mut self, base: &[Lit], uv: &[Lit], bound: &[bool]) -> Result<Vec<Cls>, String> {
        let (seen_t, seen_f) = self.classify_seen(base, uv)?;
        Ok(cls_of(&seen_t, &seen_f, bound))
    }

    /// Per-ID polarities attainable in models of `base` (never-true = refuted, never-false =
    /// entailed). Requires the last `sat` to have been a model of `base`.
    pub(crate) fn classify_seen(&mut self, base: &[Lit], uv: &[Lit]) -> Result<(Vec<bool>, Vec<bool>), String> {
        let n = uv.len();
        let mut seen_t = vec![false; n];
        let mut seen_f = vec![false; n];
        let mut open: Vec<usize> = (0..n).collect();
        self.absorb(uv, &mut open, &mut seen_t, &mut seen_f);
        for j in 0..n {
            if !seen_t[j] {
                let mut a = base.to_vec();
                a.push(uv[j]);
                if self.sat(&a)? {
                    self.absorb(uv, &mut open, &mut seen_t, &mut seen_f);
                }
            }
            if seen_t[j] && !seen_f[j] {
                let mut a = base.to_vec();
                a.push(!uv[j]);
                if self.sat(&a)? {
                    self.absorb(uv, &mut open, &mut seen_t, &mut seen_f);
                }
            }
        }
        Ok((seen_t, seen_f))
    }

    /// Records the last model's polarities for the still-open IDs, dropping completed ones.
    fn absorb(&self, uv: &[Lit], open: &mut Vec<usize>, seen_t: &mut [bool], seen_f: &mut [bool]) {
        open.retain(|&j| {
            if self.val(uv[j]) {
                seen_t[j] = true;
            } else {
                seen_f[j] = true;
            }
            !(seen_t[j] && seen_f[j])
        });
    }

    fn selection(
        &mut self,
        qid: &str,
        universe: Option<&[String]>,
        rel: Relation,
        target: &Expr,
    ) -> Result<AnswerOut, String> {
        let uni = self.universe(universe)?;
        let t = self.lit(target)?;
        let mut ids = Vec::new();
        for u in &uni {
            if self.is_bound(u) && self.selects(u, rel, t)? {
                ids.push(u.clone());
            }
        }
        Ok(AnswerOut::Selection {
            qid: qid.into(),
            ids,
        })
    }

    /// Whether `u` stands in `rel` to the target literal `t` in every interpretation: each
    /// refuting literal set must be jointly UNSAT with the theory (a valid consequence).
    pub(crate) fn selects(&mut self, u: &str, rel: Relation, t: Lit) -> Result<bool, String> {
        let p = self.ids[u];
        for i in 0..self.n_interps() {
            for s in rel_sets(rel, p, t) {
                let mut a = self.interp_assumptions(i);
                a.extend_from_slice(&s);
                if self.sat(&a)? {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    pub(crate) fn feasibility(&mut self, qid: &str, assume: &[Expr]) -> Result<AnswerOut, String> {
        let al = self.lits(assume)?;
        let refs: Vec<&Expr> = assume.iter().collect();
        let rel = self.support(&refs);
        let mut sats = Vec::new();
        let mut core_idx = BTreeSet::new();
        for i in 0..self.n_interps() {
            let mut a = self.interp_assumptions(i);
            a.extend_from_slice(&al);
            let s = self.sat(&a)?;
            if !s {
                // failed_core is unreliable after incremental solves: re-verify it, else keep all.
                let core = self.core();
                let mut cidx: Vec<usize> = (0..al.len()).filter(|&k| core.contains(&al[k])).collect();
                let mut check = self.interp_assumptions(i);
                check.extend(cidx.iter().map(|&k| al[k]));
                if self.sat(&check)? {
                    cidx = (0..al.len()).collect();
                }
                core_idx.extend(cidx);
            }
            sats.push(s);
        }
        if sats.iter().all(|&s| s) {
            let witness = self.joint_model(&rel, &al)?;
            return Ok(AnswerOut::Feasibility {
                qid: qid.into(),
                decision: "SAT".into(),
                witness: Some(witness),
                core: None,
                missing: vec![],
            });
        } else if sats.iter().all(|&s| !s) {
            // Union of per-interpretation cores is unsatisfiable in each interpretation.
            return Ok(AnswerOut::Feasibility {
                qid: qid.into(),
                decision: "UNSAT".into(),
                witness: None,
                core: Some(core_idx.into_iter().collect()),
                missing: vec![],
            });
        }
        Ok(AnswerOut::Feasibility {
            qid: qid.into(),
            decision: "UNRESOLVED".into(),
            witness: None,
            core: None,
            missing: self.missing_codes(&rel),
        })
    }

    pub(crate) fn partition(
        &mut self,
        qid: &str,
        members: &[Expr],
        claim: Claim,
        target: Option<&Expr>,
    ) -> Result<AnswerOut, String> {
        let ml = self.lits(members)?;
        let t = match target {
            Some(e) => self.lit(e)?,
            None => self.konst(true),
        };
        let v = self.violation(&ml, t, claim)?;
        let mut refs: Vec<&Expr> = members.iter().collect();
        refs.extend(target);
        let rel = self.support(&refs);
        // A claim "holds" in an interpretation when its violation is unsatisfiable there.
        let fails = self.per_interp_sat(&[v])?;
        if fails.iter().all(|&f| !f) {
            return Ok(partition_answer(qid, "true", None, vec![]));
        }
        if fails.iter().all(|&f| f) {
            let ce = self.joint_model(&rel, &[v])?;
            return Ok(partition_answer(qid, "false", Some(ce), vec![]));
        }
        Ok(partition_answer(qid, "UNRESOLVED", None, self.missing_codes(&rel)))
    }
}

/// Sign pairs `(p positive, t positive)` whose joint unsatisfiability with the theory makes
/// `p rel t` a consequence.
pub(crate) fn rel_pairs(rel: Relation) -> &'static [(bool, bool)] {
    match rel {
        Relation::Equiv => &[(true, false), (false, true)],
        Relation::Implies => &[(true, false)],
        Relation::ImpliedBy => &[(false, true)],
        Relation::Excludes => &[(true, true)],
    }
}

fn rel_sets(rel: Relation, p: Lit, t: Lit) -> Vec<Vec<Lit>> {
    let sg = |l: Lit, pos: bool| if pos { l } else { !l };
    rel_pairs(rel).iter().map(|&(a, b)| vec![sg(p, a), sg(t, b)]).collect()
}

pub(crate) fn cls_of(seen_t: &[bool], seen_f: &[bool], bound: &[bool]) -> Vec<Cls> {
    (0..seen_t.len())
        .map(|j| match (seen_t[j], seen_f[j]) {
            (false, _) => Cls::Ref, // K and H force not-v
            (true, false) => Cls::Ent, // K and H force v
            _ if bound[j] => Cls::Cont,
            _ => Cls::Unr,
        })
        .collect()
}

fn partition_answer(
    qid: &str,
    decision: &str,
    counterexample: Option<BTreeMap<String, bool>>,
    missing: Vec<String>,
) -> AnswerOut {
    AnswerOut::Partition {
        qid: qid.into(),
        decision: decision.into(),
        counterexample,
        missing,
        evidence: None,
    }
}

