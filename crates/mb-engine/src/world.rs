//! Component-partitioned evaluation.
//!
//! Outcome IDs, alias classes and constraints form a graph (a constraint links every ID and alias it
//! mentions; a bound binding links to its class alias). Connected components are logically
//! independent, so a query only needs the components its expressions touch: each is answered on a
//! small `Engine` over just those components (cached by component set), instead of one solver over
//! the whole theory where every SAT answer costs O(all variables).
//!
//! Everything outside the touched components is read from a cached per-interpretation base
//! classification (one sweep over the components): `p` is satisfiable with polarity `s` in
//! interpretation `i` exactly when it is in its own component, and cross-component conjunctions are
//! satisfiable iff each side is. Constraints that mention no ID or alias are added to every component.

use crate::engine::{set_deadline, Engine};
use crate::families::{cls_of, rel_pairs, Cls};
use mb_ir::{AnswerOut, Binding, Expr, Labels, Query, QueryBody, Relation, Status, Submission, Theory};
use varisat::Lit;
use std::cell::Cell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::time::{Duration, Instant};

/// Engines kept for reuse. A cached engine's sub-submission is leaked (engines are 'static), so the
/// cache is capped by count and by total cached items (bindings plus constraints, `CACHE_MAX_ITEMS`;
/// aliases are not counted). Those caps bound the cached base items only: each engine's solver grows
/// with every query's permanent gates and clauses, and nothing bounds that. Past either cap an
/// engine is one-off and its sub-submission is freed when the query is done. Only a world created by
/// `World::new` caches; scoped worlds never do (`World::scoped_view`).
const CACHE_MAX_ENGINES: usize = 32;
const CACHE_MAX_ITEMS: usize = 200_000;

thread_local! {
    /// Engine sub-submissions leaked on this thread (see `with_engine`).
    static LEAKED: Cell<usize> = const { Cell::new(0) };
}

#[cfg(test)]
thread_local! {
    /// Closures that returned hyp_inconsistent before building every group engine (tests read it).
    pub(crate) static EARLY_RETURNS: Cell<usize> = const { Cell::new(0) };
}

/// Number of engine sub-submissions this thread has leaked so far (tests check that it stays flat).
pub fn leaked_engines() -> usize {
    LEAKED.with(|c| c.get())
}

pub struct World<'a> {
    pub(crate) sub: &'a Submission,
    pos: HashMap<&'a str, usize>,
    /// Component of each binding (by position).
    comp: Vec<u32>,
    comp_bindings: Vec<Vec<u32>>,
    comp_aliases: Vec<Vec<&'a str>>,
    comp_cons: Vec<Vec<u32>>,
    global_cons: Vec<u32>,
    engines: HashMap<Vec<u32>, Engine<'static>>,
    /// Bindings plus constraints held by the cached engines' (leaked) sub-submissions.
    cache_items: usize,
    /// `[interp][binding pos]`: bit0 = can be true, bit1 = can be false (no hypothesis).
    base: Option<Vec<Vec<u8>>>,
    timeout: Option<Duration>,
    /// Evidence inverted index, built on the first scoped query (never touches the caches above).
    ev_index: Option<EvIndex<'a>>,
    /// Scoped queries use one monolithic engine instead of components (MB_MONOLITH differential).
    monolith: bool,
    /// Whether engines may be cached (and their sub-submissions leaked). False for scoped worlds.
    cache: bool,
    /// Restricted submissions of recent evidence scopes (`with_scope`).
    scopes: Vec<Scope>,
    /// Certificate size cap (profile `max_certificate_refs`), read by `check::certify` and `check`.
    pub(crate) max_cert_refs: Option<u64>,
    /// Entailment tests a certificate search may spend (see `check::minimal_certificate`).
    pub(crate) cert_trials: usize,
    /// Outcome -> constraint positions mentioning it directly; built on the first reach search.
    var_cons: Option<HashMap<&'a str, Vec<usize>>>,
}

/// Inverted index from evidence refs to binding and constraint positions, ascending. Built lazily;
/// scoping then costs O(|evidence| + candidates) instead of a scan of the theory.
struct EvIndex<'a> {
    /// Provenance refs (the only refs evidence may name).
    known: HashSet<&'a str>,
    bind_by_ref: HashMap<&'a str, Vec<usize>>,
    /// Bindings with no evidence: inside every scope (the "all refs inside" test is vacuous).
    bind_free: Vec<usize>,
    cons_by_ref: HashMap<&'a str, Vec<usize>>,
    /// Constraints with no provenance: inside every scope.
    cons_free: Vec<usize>,
}

impl<'a> EvIndex<'a> {
    fn new(sub: &'a Submission) -> Self {
        let mut ix = EvIndex {
            known: sub.provenance.iter().map(|p| p.reference.as_str()).collect(),
            bind_by_ref: HashMap::new(),
            bind_free: Vec::new(),
            cons_by_ref: HashMap::new(),
            cons_free: Vec::new(),
        };
        for (p, b) in sub.bindings.iter().enumerate() {
            if b.evidence.is_empty() {
                ix.bind_free.push(p);
            }
            for r in &b.evidence {
                ix.bind_by_ref.entry(r.as_str()).or_default().push(p);
            }
        }
        for (i, c) in sub.theory.constraints.iter().enumerate() {
            if c.provenance.is_empty() {
                ix.cons_free.push(i);
            }
            for r in &c.provenance {
                ix.cons_by_ref.entry(r.as_str()).or_default().push(i);
            }
        }
        ix
    }
}

/// Outcome IDs and alias names mentioned by `e` (alias definitions are not followed).
fn mentions<'e>(e: &'e Expr, vars: &mut Vec<&'e str>, names: &mut Vec<&'e str>) {
    let mut stack = vec![e];
    while let Some(n) = stack.pop() {
        match n {
            Expr::Var { id } => vars.push(id),
            Expr::Ref { name } => names.push(name),
            _ => stack.extend(n.children()),
        }
    }
}

/// A restricted submission, owned by the world it was built for (see `World::with_scope`).
struct Scope {
    /// Sorted evidence and out-of-scope universe positions: the cache key.
    key: (Vec<String>, Vec<usize>),
    sub: Submission,
    /// Default universe of the scope: its in-scope bindings, in file order.
    in_scope: Vec<String>,
    /// Base classification of `sub`, once a closure or selection query has needed it.
    base: Option<Vec<Vec<u8>>>,
}

/// Scopes kept per world (oldest evicted). Each one owns a restricted copy of the submission.
const SCOPE_CACHE: usize = 8;

/// Whether every outcome reached from alias `root` (through `ref`s, transitively) is in scope.
/// Iterative, since alias chains can be long; `memo` is per scope build. Aliases are acyclic through
/// `ref`, so the walk terminates. Unknown aliases and outcomes are passed on, and the engine reports them.
fn alias_in_scope<'s>(
    sub: &'s Submission,
    pos: &HashMap<&str, usize>,
    inside: &dyn Fn(usize) -> bool,
    memo: &mut HashMap<&'s str, bool>,
    root: &'s str,
) -> bool {
    let mut stack: Vec<(&'s str, bool)> = vec![(root, false)];
    while let Some((name, expanded)) = stack.pop() {
        if memo.contains_key(name) {
            continue;
        }
        let Some(def) = sub.theory.aliases.get(name) else {
            memo.insert(name, true);
            continue;
        };
        let (mut vars, mut refs) = (Vec::new(), Vec::new());
        mentions(def, &mut vars, &mut refs);
        if expanded {
            let ok = vars.iter().all(|v| pos.get(v).map_or(true, |&p| inside(p)))
                && refs.iter().all(|r| memo.get(r).copied().unwrap_or(true));
            memo.insert(name, ok);
        } else {
            stack.push((name, true));
            stack.extend(refs.into_iter().filter(|r| !memo.contains_key(r)).map(|r| (r, false)));
        }
    }
    memo.get(root).copied().unwrap_or(true)
}

/// The restricted submission for evidence `ev` (sorted, deduplicated) and out-of-scope universe
/// positions `extras`. A query over the scope is answered as if the theory were exactly this one.
///
/// Rules. Outcome `p` is in scope iff all its evidence refs are in `ev` (an outcome with no evidence is in
/// every scope, so unresolved outcomes with no evidence sit in every default universe).
/// - A constraint is kept iff all its provenance refs are in `ev` and every outcome it reaches (directly,
///   or through the alias definitions it uses, transitively) is in scope. Constraints with no provenance
///   pass the first test; the second still applies. The validator does not check that provenance covers
///   the outcomes a constraint reaches: an under-citing builder loses those constraints here, which is
///   the builder's risk, not a validation error.
/// - A bound in-scope outcome keeps its class only if that class reaches in-scope outcomes alone.
///   Otherwise it becomes unresolved (class null, `out_of_scope` appended to its missing codes).
/// - Out-of-scope outcomes appear only as `extras` (explicit universe entries), unresolved and unnamed by
///   any kept constraint. Aliases are kept only when reached.
///
/// Soundness: the kept constraints are a subset of the full ones, and dropped equivalences leave their
/// atoms free, so a consistent full base implies a consistent scoped base. Dropping constraints is NOT
/// monotone for feasibility and partition answers: UNSAT can become SAT, and a `true` partition can become
/// `false`. Outside evidence can therefore change an answer, but only by removing the constraints it
/// needs, never by introducing a free outcome into a kept constraint.
fn build_scope(
    sub: &Submission,
    pos: &HashMap<&str, usize>,
    idx: &EvIndex,
    ev: &[&str],
    key: (Vec<String>, Vec<usize>),
    keep: Option<(&HashSet<usize>, &HashSet<usize>)>,
) -> Scope {
    let inside_p = |p: usize| sub.bindings[p].evidence.iter().all(|r| ev.binary_search(&r.as_str()).is_ok());
    let inside_refs = |refs: &[String]| refs.iter().all(|r| ev.binary_search(&r.as_str()).is_ok());
    // Candidates from the index (an item with evidence cites a ref in `ev`, one without is always a
    // candidate), then the exact test. Sorted and deduplicated, so nothing depends on hash order.
    let mut cb: Vec<usize> = idx.bind_free.clone();
    let mut cc: Vec<usize> = idx.cons_free.clone();
    for r in ev {
        cb.extend(idx.bind_by_ref.get(r).into_iter().flatten());
        cc.extend(idx.cons_by_ref.get(r).into_iter().flatten());
    }
    cb.sort_unstable();
    cb.dedup();
    cc.sort_unstable();
    cc.dedup();
    // Certificate scopes keep only the claim's components (`with_evidence`); query scopes keep everything.
    if let Some((kb, kc)) = keep {
        cb.retain(|p| kb.contains(p));
        cc.retain(|i| kc.contains(i));
    }
    let in_scope: Vec<usize> = cb.into_iter().filter(|&p| inside_p(p)).collect();
    let extras = &key.1;
    let mut memo: HashMap<&str, bool> = HashMap::new();
    let kept: Vec<usize> = cc
        .into_iter()
        .filter(|&i| {
            let c = &sub.theory.constraints[i];
            if !inside_refs(&c.provenance) {
                return false;
            }
            let (mut vars, mut refs) = (Vec::new(), Vec::new());
            mentions(&c.expr, &mut vars, &mut refs);
            vars.iter().all(|v| pos.get(v).map_or(true, |&p| inside_p(p)))
                && refs.iter().all(|r| alias_in_scope(sub, pos, &inside_p, &mut memo, r))
        })
        .collect();
    let mut cut: BTreeSet<usize> = BTreeSet::new();
    for &p in &in_scope {
        if let (Status::Bound, Some(c)) = (sub.bindings[p].status, &sub.bindings[p].class) {
            if !alias_in_scope(sub, pos, &inside_p, &mut memo, c.as_str()) {
                cut.insert(p);
            }
        }
    }
    // Aliases reached from the kept constraints and from the classes that were kept.
    let mut names: Vec<&str> = Vec::new();
    for &i in &kept {
        let (mut vars, mut refs) = (Vec::new(), Vec::new());
        mentions(&sub.theory.constraints[i].expr, &mut vars, &mut refs);
        names.extend(refs);
    }
    for &p in &in_scope {
        if let (Status::Bound, Some(c)) = (sub.bindings[p].status, &sub.bindings[p].class) {
            if !cut.contains(&p) {
                names.push(c.as_str());
            }
        }
    }
    let mut need: BTreeSet<&str> = BTreeSet::new();
    while let Some(name) = names.pop() {
        if need.insert(name) {
            if let Some(def) = sub.theory.aliases.get(name) {
                let (mut vars, mut refs) = (Vec::new(), Vec::new());
                mentions(def, &mut vars, &mut refs);
                names.extend(refs);
            }
        }
    }
    let mut bpos: Vec<usize> = in_scope.iter().copied().chain(extras.iter().copied()).collect();
    bpos.sort_unstable();
    bpos.dedup();
    let bindings: Vec<Binding> = bpos
        .iter()
        .map(|&p| {
            let b = &sub.bindings[p];
            if inside_p(p) && !cut.contains(&p) {
                return b.clone();
            }
            let mut missing = b.missing.clone();
            missing.push("out_of_scope".into());
            Binding { id: b.id.clone(), status: Status::Unresolved, class: None, negated: false, missing, evidence: b.evidence.clone() }
        })
        .collect();
    let aliases: BTreeMap<String, Expr> = need
        .iter()
        .filter_map(|&k| sub.theory.aliases.get(k).map(|d| (k.to_string(), d.clone())))
        .collect();
    let in_ids: Vec<String> = in_scope.iter().map(|&p| sub.bindings[p].id.clone()).collect();
    let theory = Theory {
        interpretations: sub.theory.interpretations.clone(),
        aliases,
        constraints: kept.iter().map(|&i| sub.theory.constraints[i].clone()).collect(),
        duplicate_aliases: Vec::new(),
    };
    Scope {
        key,
        sub: Submission { bindings, theory, provenance: Vec::new(), bytes: 0 },
        in_scope: in_ids,
        base: None,
    }
}

struct Dsu(Vec<u32>);

impl Dsu {
    fn find(&mut self, mut x: u32) -> u32 {
        while self.0[x as usize] != x {
            let p = self.0[x as usize];
            self.0[x as usize] = self.0[p as usize];
            x = self.0[x as usize];
        }
        x
    }
    fn union(&mut self, a: u32, b: u32) {
        let (a, b) = (self.find(a), self.find(b));
        if a != b {
            self.0[a as usize] = b;
        }
    }
}

/// Outcome IDs mentioned in `exprs` (alias refs are not followed; queries contain none).
pub(crate) fn vars_of<'e>(exprs: &[&'e Expr]) -> Vec<&'e str> {
    let mut out = Vec::new();
    let mut stack: Vec<&Expr> = exprs.to_vec();
    while let Some(e) = stack.pop() {
        if let Expr::Var { id } = e {
            out.push(id.as_str());
        }
        stack.extend(e.children());
    }
    out
}

fn has_wsum(e: &Expr) -> bool {
    matches!(e, Expr::Wsum { .. }) || e.children().into_iter().any(has_wsum)
}

fn query_exprs(q: &Query) -> Vec<&Expr> {
    match &q.body {
        QueryBody::Closure { hyp } => hyp.iter().collect(),
        QueryBody::Selection { target, .. } => vec![target],
        QueryBody::Feasibility { assume } => assume.iter().collect(),
        QueryBody::Partition { members, target, .. } => members.iter().chain(target.iter()).collect(),
    }
}

impl<'a> World<'a> {
    pub fn new(sub: &'a Submission) -> Self {
        let n = sub.bindings.len();
        let pos: HashMap<&str, usize> = sub.bindings.iter().enumerate().map(|(i, b)| (b.id.as_str(), i)).collect();
        let alias_ix: HashMap<&str, usize> = sub.theory.aliases.keys().enumerate().map(|(i, k)| (k.as_str(), i)).collect();
        let mut dsu = Dsu((0..(n + alias_ix.len()) as u32).collect());
        // Nodes mentioned by an expression: IDs, plus aliases through `ref`.
        let nodes = |e: &Expr, out: &mut Vec<u32>| {
            let mut stack = vec![e];
            while let Some(e) = stack.pop() {
                match e {
                    Expr::Var { id } => out.extend(pos.get(id.as_str()).map(|&p| p as u32)),
                    Expr::Ref { name } => out.extend(alias_ix.get(name.as_str()).map(|&a| (n + a) as u32)),
                    _ => stack.extend(e.children()),
                }
            }
        };
        let mut buf = Vec::new();
        for (name, def) in &sub.theory.aliases {
            buf.clear();
            nodes(def, &mut buf);
            let a = (n + alias_ix[name.as_str()]) as u32;
            for &x in &buf {
                dsu.union(a, x);
            }
        }
        for (p, b) in sub.bindings.iter().enumerate() {
            if let Some(&a) = b.class.as_deref().filter(|_| b.status == Status::Bound).and_then(|c| alias_ix.get(c)) {
                dsu.union(p as u32, (n + a) as u32);
            }
        }
        let mut cons_root: Vec<Option<u32>> = Vec::with_capacity(sub.theory.constraints.len());
        for c in &sub.theory.constraints {
            buf.clear();
            nodes(&c.expr, &mut buf);
            for w in buf.windows(2) {
                dsu.union(w[0], w[1]);
            }
            cons_root.push(buf.first().copied());
        }
        let mut comp_of_root: HashMap<u32, u32> = HashMap::new();
        let mut comp = vec![0u32; n];
        let mut comp_bindings: Vec<Vec<u32>> = Vec::new();
        let mut comp_aliases: Vec<Vec<&str>> = Vec::new();
        let mut comp_cons: Vec<Vec<u32>> = Vec::new();
        let mut cid = |dsu: &mut Dsu, x: u32, cb: &mut Vec<Vec<u32>>, ca: &mut Vec<Vec<&'a str>>, cc: &mut Vec<Vec<u32>>| {
            let r = dsu.find(x);
            *comp_of_root.entry(r).or_insert_with(|| {
                cb.push(Vec::new());
                ca.push(Vec::new());
                cc.push(Vec::new());
                (cb.len() - 1) as u32
            })
        };
        for p in 0..n {
            let c = cid(&mut dsu, p as u32, &mut comp_bindings, &mut comp_aliases, &mut comp_cons);
            comp[p] = c;
            comp_bindings[c as usize].push(p as u32);
        }
        // Sorted alias order (not HashMap order) keeps component numbering, and so engine choice, stable.
        for (a, name) in sub.theory.aliases.keys().enumerate() {
            let c = cid(&mut dsu, (n + a) as u32, &mut comp_bindings, &mut comp_aliases, &mut comp_cons);
            comp_aliases[c as usize].push(name.as_str());
        }
        let mut global_cons = Vec::new();
        for (i, r) in cons_root.iter().enumerate() {
            match r {
                None => global_cons.push(i as u32),
                Some(x) => {
                    let c = cid(&mut dsu, *x, &mut comp_bindings, &mut comp_aliases, &mut comp_cons);
                    comp_cons[c as usize].push(i as u32);
                }
            }
        }
        World {
            sub,
            pos,
            comp,
            comp_bindings,
            comp_aliases,
            comp_cons,
            global_cons,
            engines: HashMap::new(),
            cache_items: 0,
            base: None,
            timeout: None,
            ev_index: None,
            monolith: false,
            cache: true,
            scopes: Vec::new(),
            max_cert_refs: None,
            cert_trials: crate::check::CERT_MAX_TRIALS,
            var_cons: None,
        }
    }

    /// Answers scoped queries with one monolithic engine (see `answer_scoped`).
    pub fn set_monolith(&mut self, on: bool) {
        self.monolith = on;
    }

    pub fn set_max_certificate_refs(&mut self, m: Option<u64>) {
        self.max_cert_refs = m;
    }

    /// Entailment tests one certificate search may spend (default `check::CERT_MAX_TRIALS`).
    pub fn set_certificate_trials(&mut self, n: usize) {
        self.cert_trials = n;
    }

    /// Per-query wall-clock limit; a query that exceeds it answers `error: "timeout"`.
    pub fn set_query_timeout(&mut self, t: Option<Duration>) {
        self.timeout = t;
    }

    /// Deadline for one query starting now (`None` = unlimited).
    pub fn query_deadline(&self) -> Option<Instant> {
        self.timeout.map(|t| Instant::now() + t)
    }

    pub fn cached_engines(&self) -> usize {
        self.engines.len()
    }

    /// Restricted submissions currently cached by this world.
    pub fn cached_scopes(&self) -> usize {
        self.scopes.len()
    }

    pub fn components(&self) -> usize {
        self.comp_bindings.len()
    }

    /// Sub-submission over the components `comps` (plus constraints that mention nothing).
    fn sub_for(&self, comps: &[u32]) -> Submission {
        self.sub_build(comps, true)
    }

    /// Sub-submission over `comps`; `globals` adds the constraints that mention no outcome.
    fn sub_build(&self, comps: &[u32], globals: bool) -> Submission {
        let mut bpos: Vec<u32> = comps.iter().flat_map(|&c| self.comp_bindings[c as usize].iter().copied()).collect();
        bpos.sort_unstable();
        let mut cons: Vec<u32> = comps.iter().flat_map(|&c| self.comp_cons[c as usize].iter().copied()).collect();
        if globals {
            cons.extend_from_slice(&self.global_cons);
        }
        cons.sort_unstable();
        let aliases: BTreeMap<String, Expr> = comps
            .iter()
            .flat_map(|&c| self.comp_aliases[c as usize].iter())
            .map(|&k| (k.to_string(), self.sub.theory.aliases[k].clone()))
            .collect();
        Submission {
            bindings: bpos.iter().map(|&p| self.sub.bindings[p as usize].clone()).collect(),
            theory: Theory {
                interpretations: self.sub.theory.interpretations.clone(),
                aliases,
                constraints: cons.iter().map(|&i| self.sub.theory.constraints[i as usize].clone()).collect(),
                duplicate_aliases: Vec::new(),
            },
            provenance: Vec::new(),
            bytes: 0,
        }
    }

    fn comps_of<'s>(&self, ids: impl IntoIterator<Item = &'s str>) -> Vec<u32> {
        let mut cs: Vec<u32> = ids.into_iter().filter_map(|id| self.pos.get(id)).map(|&p| self.comp[p]).collect();
        cs.sort_unstable();
        cs.dedup();
        cs
    }

    /// Bindings plus constraints of the sub-submission over `comps`.
    fn items_of(&self, comps: &[u32]) -> usize {
        comps.iter().map(|&c| self.comp_bindings[c as usize].len() + self.comp_cons[c as usize].len()).sum::<usize>()
            + self.global_cons.len()
    }

    /// Runs `f` on an engine covering `comps` (and the global constraints). A cached engine whose
    /// component set contains every component of `comps` is reused, smallest first and ties to the
    /// least key (the extra components are independent of the query and base-consistent, so answers
    /// do not change). Component sets that differ are not covered by one another: a new engine is
    /// built, cached while the caps allow it, and otherwise used once and dropped with its data.
    fn with_engine<R>(
        &mut self,
        comps: Vec<u32>,
        f: impl FnOnce(&World<'a>, &mut Engine<'_>, &[u32]) -> R,
    ) -> Result<R, String> {
        let cover = self
            .engines
            .keys()
            .filter(|k| !comps.is_empty() && comps.iter().all(|c| k.binary_search(c).is_ok()))
            .min_by_key(|k| (k.len(), (*k).clone()))
            .cloned();
        if let Some(key) = cover {
            let mut e = self.engines.remove(&key).expect("cover key is cached");
            let r = f(&*self, &mut e, &key);
            self.engines.insert(key, e);
            return Ok(r);
        }
        let items = self.items_of(&comps);
        // The empty set is never cached: all such queries would share one key and leak one copy each.
        if self.cache && !comps.is_empty() && self.engines.len() < CACHE_MAX_ENGINES && self.cache_items + items <= CACHE_MAX_ITEMS {
            let sub: &'static Submission = Box::leak(Box::new(self.sub_for(&comps)));
            LEAKED.with(|c| c.set(c.get() + 1));
            let mut e = Engine::new(sub)?;
            self.cache_items += items;
            let r = f(&*self, &mut e, &comps);
            self.engines.insert(comps, e);
            return Ok(r);
        }
        let sub = self.sub_for(&comps);
        let mut e = Engine::new(&sub)?;
        Ok(f(&*self, &mut e, &comps))
    }

    /// Runs `f` on the engine covering the components of `ids`.
    pub(crate) fn with_ids<R>(&mut self, ids: Vec<&str>, f: impl FnOnce(&mut Engine<'_>) -> R) -> Result<R, String> {
        let comps = self.comps_of(ids);
        self.with_engine(comps, |_, e, _| f(e))
    }

    /// Components of everything a query (and optionally a certificate) mentions.
    pub(crate) fn query_ids<'q>(q: &'q Query, extra: impl IntoIterator<Item = &'q str>) -> Vec<&'q str> {
        let mut ids = vars_of(&query_exprs(q));
        ids.extend(extra);
        ids
    }

    /// One sweep over every component: base consistency per interpretation and, if `classify`,
    /// the base classification of every ID. Returns the first inconsistent interpretation.
    pub fn inconsistent_interpretation(&mut self, classify: bool) -> Result<Option<String>, String> {
        let k = self.sub.theory.interpretations.len();
        let mut base = vec![vec![0u8; self.sub.bindings.len()]; if classify { k } else { 0 }];
        let mut bad: Option<usize> = None;
        let none: Vec<u32> = Vec::new();
        for c in std::iter::once(None).chain((0..self.components() as u32).map(Some)) {
            let comps = c.map_or(none.clone(), |c| vec![c]);
            let sub = self.sub_for(&comps);
            let mut e = Engine::new(&sub)?;
            let uv: Vec<_> = sub.bindings.iter().map(|b| e.ids[b.id.as_str()]).collect();
            for i in 0..k {
                let a = e.interp_assumptions(i);
                if !e.sat(&a)? {
                    bad = Some(bad.map_or(i, |b| b.min(i)));
                } else if classify && !uv.is_empty() {
                    let (st, sf) = e.classify_seen(&a, &uv)?;
                    for (j, b) in sub.bindings.iter().enumerate() {
                        base[i][self.pos[b.id.as_str()]] = st[j] as u8 | (sf[j] as u8) << 1;
                    }
                }
            }
        }
        if classify && bad.is_none() {
            self.base = Some(base);
        }
        Ok(bad.map(|i| self.sub.theory.interpretations[i].clone()))
    }

    fn ensure_base(&mut self) -> Result<(), String> {
        if self.base.is_none() && self.inconsistent_interpretation(true)?.is_some() {
            return Err("inconsistent_base".into());
        }
        Ok(())
    }

    /// Answers one query. Query-level failures (including `timeout`) become `AnswerOut::Error`.
    pub fn answer(&mut self, q: &Query) -> AnswerOut {
        // The deadline is armed first, so the base classification is bounded too.
        set_deadline(self.query_deadline());
        let a = self.answer_in_deadline(q);
        set_deadline(None);
        a
    }

    /// Answers `q` under the deadline the caller has armed (`eval` arms one per query and reuses it for the
    /// certificate, so the two share a single budget).
    pub fn answer_in_deadline(&mut self, q: &Query) -> AnswerOut {
        let qid = q.qid.clone();
        if q.uses_ref() {
            return AnswerOut::Error { qid, error: "ref_in_query".into() };
        }
        let r = if q.evidence.is_some() { self.answer_scoped(q, &qid) } else { self.answer_timed(q, &qid) };
        r.unwrap_or_else(|error| AnswerOut::Error { qid, error })
    }

    /// A query with evidence: answered on a `World` over its restricted submission (`with_scope`), so
    /// the solver only sees the components the query touches. Monolithic mode uses one `Engine` over the
    /// same restricted submission instead.
    fn answer_scoped(&mut self, q: &Query, qid: &str) -> Result<AnswerOut, String> {
        let mono = self.monolith;
        self.with_scope(q, |w, sq| if mono { Engine::new(w.sub).map(|mut e| e.answer(sq)) } else { w.answer_timed(sq, qid) })
            .and_then(|r| r)
    }

    /// Runs `f` on a `World` over the restricted submission of `q`'s evidence scope (`q` must carry
    /// `evidence`), with `sq` = `q` whose default universe is the scope's in-scope bindings. Scopes are
    /// owned by this world, cached up to `SCOPE_CACHE` (oldest evicted), and keep their base
    /// classification. The scoped world never keeps engines, so a scoped query leaks nothing.
    pub fn with_scope<R>(&mut self, q: &Query, f: impl FnOnce(&mut World<'_>, &Query) -> R) -> Result<R, String> {
        let i = self.scope_index(q)?;
        let mut sq = q.clone();
        sq.universe.get_or_insert_with(|| self.scopes[i].in_scope.clone());
        Ok(self.run_scope(i, |w| f(w, &sq)))
    }

    /// Runs `f` on a one-off `World` over gold restricted to the evidence `ev` (any refs, no query checks) and to
    /// the components of `focus`, the claim's outcomes: an entailment over `focus` depends on nothing else.
    /// Nothing is cached, so certificate trials leave the query scope cache and the engine caches alone.
    pub fn with_evidence<R>(&mut self, ev: &[String], focus: &[&str], f: impl FnOnce(&mut World<'_>) -> R) -> R {
        let mut ev: Vec<&str> = ev.iter().map(String::as_str).collect();
        ev.sort_unstable();
        ev.dedup();
        let comps = self.comps_of(focus.iter().copied());
        let keep_b: HashSet<usize> = comps.iter().flat_map(|&c| self.comp_bindings[c as usize].iter().map(|&p| p as usize)).collect();
        let keep_c: HashSet<usize> = comps.iter().flat_map(|&c| self.comp_cons[c as usize].iter().map(|&i| i as usize)).collect();
        let sub = self.sub;
        let idx = self.ev_index.get_or_insert_with(|| EvIndex::new(sub));
        let key = (ev.iter().map(|r| r.to_string()).collect::<Vec<String>>(), Vec::new());
        let scope = build_scope(sub, &self.pos, idx, &ev, key, Some((&keep_b, &keep_c)));
        let mut w = World::scoped_view(&scope.sub, None);
        f(&mut w)
    }

    fn run_scope<R>(&mut self, i: usize, f: impl FnOnce(&mut World<'_>) -> R) -> R {
        let s = &mut self.scopes[i];
        let mut w = World::scoped_view(&s.sub, s.base.take());
        let r = f(&mut w);
        s.base = w.base.take();
        r
    }

    /// A world over `sub` that keeps no engines (one-off ones only), with a known base classification.
    fn scoped_view(sub: &'a Submission, base: Option<Vec<Vec<u8>>>) -> Self {
        let mut w = World::new(sub);
        w.cache = false;
        w.base = base;
        w
    }

    /// Whether `id` is a binding of this world (a scoped world holds only its in-scope bindings).
    pub(crate) fn has_binding(&self, id: &str) -> bool {
        self.pos.contains_key(id)
    }

    /// Refs of the components that hold `ids`: the evidence of their bindings and the provenance of their
    /// constraints. The claim's proof can only use these, so they start a certificate search.
    pub(crate) fn touched_refs(&self, ids: &[&str]) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for c in self.comps_of(ids.iter().copied()) {
            for &p in &self.comp_bindings[c as usize] {
                out.extend(self.sub.bindings[p as usize].evidence.iter().cloned());
            }
            for &i in &self.comp_cons[c as usize] {
                out.extend(self.sub.theory.constraints[i as usize].provenance.iter().cloned());
            }
        }
        out
    }

    /// Whether `r` is a provenance ref of gold (the evidence index, built once per world).
    pub(crate) fn is_gold_ref(&mut self, r: &str) -> bool {
        let sub = self.sub;
        self.ev_index.get_or_insert_with(|| EvIndex::new(sub)).known.contains(r)
    }

    /// Refs a claim's proof can reach from its outcomes: the evidence of each reached outcome and of its class's
    /// first outcome, and the provenance of every constraint that mentions a reached outcome, transitively.
    /// Constraints that reach outcomes only through an alias `ref` are not followed, so the set may miss refs;
    /// the certificate search validates whatever it starts from. A subset of `touched_refs`.
    pub(crate) fn reach_refs(&mut self, ids: &[&str]) -> BTreeSet<String> {
        let sub = self.sub;
        if self.var_cons.is_none() {
            let mut by_var: HashMap<&'a str, Vec<usize>> = HashMap::new();
            for (i, c) in sub.theory.constraints.iter().enumerate() {
                for v in vars_of(&[&c.expr]) {
                    by_var.entry(v).or_default().push(i);
                }
            }
            self.var_cons = Some(by_var);
        }
        let by_var = self.var_cons.as_ref().expect("built above");
        let mut out = BTreeSet::new();
        let mut seen_vars: HashSet<&str> = HashSet::new();
        let mut seen_cons: HashSet<usize> = HashSet::new();
        let mut stack: Vec<&str> = ids.to_vec();
        while let Some(v) = stack.pop() {
            if !seen_vars.insert(v) {
                continue;
            }
            let Some(&p) = self.pos.get(v) else {
                continue;
            };
            let b = &sub.bindings[p];
            out.extend(b.evidence.iter().cloned());
            if let Some(def) = b.class.as_deref().filter(|_| b.status == Status::Bound).and_then(|c| sub.theory.aliases.get(c)) {
                stack.extend(vars_of(&[def]));
            }
            for &i in by_var.get(v).into_iter().flatten() {
                if seen_cons.insert(i) {
                    let c = &sub.theory.constraints[i];
                    out.extend(c.provenance.iter().cloned());
                    stack.extend(vars_of(&[&c.expr]));
                }
            }
        }
        out
    }

    /// Index into `self.scopes` of the scope of `q`'s evidence, building and caching it when absent.
    /// Errors with `out_of_scope` for an empty evidence set, a ref that provenance does not define, or
    /// a query expression over a binding outside the scope. The cache key is the sorted, deduplicated
    /// evidence plus the out-of-scope universe entries, which the scope must name.
    fn scope_index(&mut self, q: &Query) -> Result<usize, String> {
        let sub = self.sub;
        let mut ev: Vec<&str> = q.evidence.iter().flatten().map(String::as_str).collect();
        ev.sort_unstable();
        ev.dedup();
        let idx = self.ev_index.get_or_insert_with(|| EvIndex::new(sub));
        if ev.is_empty() || ev.iter().any(|r| !idx.known.contains(r)) {
            return Err("out_of_scope".into());
        }
        let inside = |p: usize| sub.bindings[p].evidence.iter().all(|r| ev.binary_search(&r.as_str()).is_ok());
        for id in vars_of(&query_exprs(q)) {
            if self.pos.get(id).is_some_and(|&p| !inside(p)) {
                return Err("out_of_scope".into());
            }
        }
        let mut extras: Vec<usize> = q
            .universe
            .iter()
            .flatten()
            .filter_map(|id| self.pos.get(id.as_str()).copied())
            .filter(|&p| !inside(p))
            .collect();
        extras.sort_unstable();
        extras.dedup();
        Ok(self.scope_for(ev, extras))
    }

    /// Index of the scope for the sorted, deduplicated evidence `ev` and out-of-scope universe positions
    /// `extras`, building and caching it when absent (the oldest scope is evicted at `SCOPE_CACHE`).
    fn scope_for(&mut self, ev: Vec<&str>, extras: Vec<usize>) -> usize {
        let sub = self.sub;
        let key = (ev.iter().map(|r| r.to_string()).collect::<Vec<String>>(), extras);
        if let Some(i) = self.scopes.iter().position(|s| s.key == key) {
            return i;
        }
        let idx = self.ev_index.get_or_insert_with(|| EvIndex::new(sub));
        let scope = build_scope(sub, &self.pos, idx, &ev, key, None);
        if self.scopes.len() == SCOPE_CACHE {
            self.scopes.remove(0);
        }
        self.scopes.push(scope);
        self.scopes.len() - 1
    }

    /// One query under the thread's deadline (armed by `answer`).
    fn answer_timed(&mut self, q: &Query, qid: &str) -> Result<AnswerOut, String> {
        if matches!(q.body, QueryBody::Closure { .. } | QueryBody::Selection { .. }) {
            self.ensure_base()?;
        }
        match &q.body {
            QueryBody::Closure { hyp } => self.closure(qid, q.universe.as_deref(), hyp),
            QueryBody::Selection { relation, target } => self.selection(qid, q.universe.as_deref(), *relation, target),
            QueryBody::Feasibility { assume } => {
                let ids = vars_of(&assume.iter().collect::<Vec<_>>());
                self.with_ids(ids, |e| e.feasibility(qid, assume))?
            }
            QueryBody::Partition { members, claim, target } => {
                let ids = vars_of(&query_exprs(q));
                self.with_ids(ids, |e| e.partition(qid, members, *claim, target.as_ref()))?
            }
        }
    }

    /// Resolves a query universe to binding positions (default: every binding, in file order).
    fn universe(&self, given: Option<&[String]>) -> Result<Vec<usize>, String> {
        let Some(list) = given else {
            return Ok((0..self.sub.bindings.len()).collect());
        };
        let mut seen = std::collections::HashSet::new();
        let mut out = Vec::new();
        for id in list {
            let &p = self.pos.get(id.as_str()).ok_or_else(|| format!("unknown_id {id} in universe"))?;
            if seen.insert(p) {
                out.push(p);
            }
        }
        Ok(out)
    }

    fn bound(&self, p: usize) -> bool {
        self.sub.bindings[p].status == Status::Bound
    }

    fn closure(&mut self, qid: &str, universe: Option<&[String]>, hyp: &[Expr]) -> Result<AnswerOut, String> {
        let uni = self.universe(universe)?;
        let Some(groups) = self.hyp_groups(hyp) else {
            // Unknown IDs and weighted sums: the single engine reports their errors exactly as before.
            let touched = self.comps_of(vars_of(&hyp.iter().collect::<Vec<_>>()));
            return self.with_engine(touched, |w, e, key| w.closure_in(e, key, qid, &uni, hyp))?;
        };
        // The grouped path builds its group engines per query (no with_engine cache). A workload-verify composite is one query per
        // process; multi-query evals (the exact-scale base, 16,811 queries at 100k, and the rounds) measured no slowdown.
        // Each group is solved on its own engine. Under an interpretation's activation literal the constraints of
        // different groups share no variable, so an interpretation survives only if it is satisfiable in every group,
        // and a group's classes depend on that group alone. Group engines omit the global constraints: base
        // classification proved SAT(act_i & globals) for every interpretation, and globals share no variable with a
        // group, so they cannot change any group's answer.
        let base = self.base.as_ref().ok_or("base classification missing")?;
        let k = base.len();
        let mut grp_of: HashMap<u32, usize> = HashMap::new();
        for (g, (comps, _)) in groups.iter().enumerate() {
            grp_of.extend(comps.iter().map(|&c| (c, g)));
        }
        let mut uni_in: Vec<Vec<usize>> = vec![Vec::new(); groups.len()];
        for (j, &p) in uni.iter().enumerate() {
            if let Some(&g) = grp_of.get(&self.comp[p]) {
                uni_in[g].push(j);
            }
        }
        // Pass one: survival of every (group, interpretation) pair, skipping interpretations already dead. Group
        // engines are built as their group is reached, so an early return builds no further engines. Submissions are
        // built up front (a copy of the group's bindings and constraints); the engine encoding is the costly part.
        let subs: Vec<Submission> = groups.iter().map(|(comps, _)| self.sub_build(comps, false)).collect();
        let mut engines: Vec<Engine<'_>> = Vec::with_capacity(subs.len());
        let mut alive = vec![true; k];
        let mut assumps: Vec<Vec<Vec<Lit>>> = Vec::with_capacity(groups.len());
        for (g, (_, items)) in groups.iter().enumerate() {
            engines.push(Engine::new(&subs[g])?);
            let e = &mut engines[g];
            let mut hl = Vec::with_capacity(items.len());
            for x in items.iter() {
                hl.push(e.lit(x)?);
            }
            let mut per = Vec::with_capacity(k);
            for (i, alive_i) in alive.iter_mut().enumerate() {
                let mut a = e.interp_assumptions(i);
                a.extend_from_slice(&hl);
                if *alive_i {
                    *alive_i = e.sat(&a)?;
                }
                per.push(a);
            }
            assumps.push(per);
            if !alive.iter().any(|&x| x) {
                // The early answer is determinate and equals the old path's hyp_inconsistent answer. The two paths
                // can differ only where a later group would have hit the deadline (the old path then times out).
                // No byte-identity claim is made under timeouts.
                #[cfg(test)]
                EARLY_RETURNS.with(|c| c.set(c.get() + 1));
                return Ok(self.closure_answer(qid, &uni, &[]));
            }
        }
        // Pass two: classes of the surviving interpretations only, on the same engines.
        let mut cls: Vec<Vec<Option<Vec<Cls>>>> = vec![vec![None; k]; groups.len()];
        for g in 0..groups.len() {
            let ins = &uni_in[g];
            let uv: Vec<Lit> = ins.iter().map(|&j| engines[g].ids[self.sub.bindings[uni[j]].id.as_str()]).collect();
            let bound_in: Vec<bool> = ins.iter().map(|&j| self.bound(uni[j])).collect();
            for i in 0..k {
                if !alive[i] {
                    continue;
                }
                let a = &assumps[g][i];
                if !engines[g].sat(a)? {
                    return Err("internal: a surviving interpretation lost its model".into());
                }
                let (st, sf) = engines[g].classify_seen(a, &uv)?;
                cls[g][i] = Some(cls_of(&st, &sf, &bound_in));
            }
        }
        let mut per_interp: Vec<Vec<Cls>> = Vec::new();
        for i in 0..k {
            if !alive[i] {
                continue;
            }
            // Universe IDs outside the hypothesis's components keep their base class.
            let mut c: Vec<Cls> = uni
                .iter()
                .map(|&p| {
                    let b = base[i][p];
                    cls_of(&[b & 1 != 0], &[b & 2 != 0], &[self.bound(p)])[0]
                })
                .collect();
            for (g, per_g) in cls.iter().enumerate() {
                if let Some(gc) = &per_g[i] {
                    for (&j, &x) in uni_in[g].iter().zip(gc) {
                        c[j] = x;
                    }
                }
            }
            per_interp.push(c);
        }
        Ok(self.closure_answer(qid, &uni, &per_interp))
    }

    /// Hypothesis groups: the components of `hyp`, joined when one hypothesis item mentions both, with the
    /// items of each group (ascending components). Items that mention no component form a group of their
    /// own with no components (its engine has the global constraints only). None when an item names an
    /// unknown ID or holds a weighted sum, whose errors the single engine reports.
    pub(crate) fn hyp_groups<'h>(&self, hyp: &'h [Expr]) -> Option<Vec<(Vec<u32>, Vec<&'h Expr>)>> {
        if hyp.iter().any(has_wsum) || vars_of(&hyp.iter().collect::<Vec<_>>()).iter().any(|id| !self.pos.contains_key(id)) {
            return None;
        }
        let touched = self.comps_of(vars_of(&hyp.iter().collect::<Vec<_>>()));
        let local = |c: u32| touched.binary_search(&c).expect("hypothesis component is touched");
        let mut dsu = Dsu((0..touched.len() as u32).collect());
        let item_comps: Vec<Vec<u32>> = hyp.iter().map(|e| self.comps_of(vars_of(&[e]))).collect();
        for cs in &item_comps {
            for w in cs.windows(2) {
                dsu.union(local(w[0]) as u32, local(w[1]) as u32);
            }
        }
        let mut by_root: BTreeMap<u32, (Vec<u32>, Vec<&'h Expr>)> = BTreeMap::new();
        for &c in &touched {
            by_root.entry(dsu.find(local(c) as u32)).or_default().0.push(c);
        }
        let mut consts: Vec<&'h Expr> = Vec::new();
        for (e, cs) in hyp.iter().zip(&item_comps) {
            match cs.first() {
                None => consts.push(e),
                Some(&c) => by_root.entry(dsu.find(local(c) as u32)).or_default().1.push(e),
            }
        }
        let mut groups: Vec<(Vec<u32>, Vec<&'h Expr>)> = by_root.into_values().collect();
        if !consts.is_empty() || groups.is_empty() {
            groups.push((Vec::new(), consts));
        }
        Some(groups)
    }

    /// Bindings whose component no hypothesis group touches (their classes come from the base).
    #[cfg(test)]
    pub(crate) fn untouched_count(&self, hyp: &[Expr]) -> usize {
        let touched: HashSet<u32> = self.hyp_groups(hyp).unwrap_or_default().iter().flat_map(|(c, _)| c.iter().copied()).collect();
        self.comp.iter().filter(|c| !touched.contains(c)).count()
    }

    fn closure_in(
        &self,
        e: &mut Engine<'_>,
        key: &[u32],
        qid: &str,
        uni: &[usize],
        hyp: &[Expr],
    ) -> Result<AnswerOut, String> {
        let hl = e.lits(hyp)?;
        let inside = |p: usize| key.binary_search(&self.comp[p]).is_ok();
        let in_idx: Vec<usize> = (0..uni.len()).filter(|&j| inside(uni[j])).collect();
        let uv: Vec<_> = in_idx.iter().map(|&j| e.ids[self.sub.bindings[uni[j]].id.as_str()]).collect();
        let bound_in: Vec<bool> = in_idx.iter().map(|&j| self.bound(uni[j])).collect();
        let base = self.base.as_ref().ok_or("base classification missing")?;
        // Per surviving interpretation: class of every universe ID.
        let mut per_interp: Vec<Vec<Cls>> = Vec::new();
        for i in 0..e.n_interps() {
            let mut a = e.interp_assumptions(i);
            a.extend_from_slice(&hl);
            if !e.sat(&a)? {
                continue;
            }
            let (st, sf) = e.classify_seen(&a, &uv)?;
            let mut cls: Vec<Cls> = uni
                .iter()
                .map(|&p| {
                    let b = base[i][p];
                    cls_of(&[b & 1 != 0], &[b & 2 != 0], &[self.bound(p)])[0]
                })
                .collect();
            for (&j, c) in in_idx.iter().zip(cls_of(&st, &sf, &bound_in)) {
                cls[j] = c;
            }
            per_interp.push(cls);
        }
        Ok(self.closure_answer(qid, uni, &per_interp))
    }

    /// The closure answer from each surviving interpretation's class per universe ID: an ID gets the class
    /// all of them agree on, else unresolved. No survivor means the hypothesis is inconsistent.
    fn closure_answer(&self, qid: &str, uni: &[usize], per_interp: &[Vec<Cls>]) -> AnswerOut {
        if per_interp.is_empty() {
            return AnswerOut::Closure { qid: qid.into(), labels: None, error: Some("hyp_inconsistent".into()) };
        }
        let mut labels = Labels { entailed: vec![], refuted: vec![], contingent: vec![], unresolved: vec![] };
        for (j, &p) in uni.iter().enumerate() {
            let first = per_interp[0][j];
            let cls = if per_interp.iter().all(|c| c[j] == first) { first } else { Cls::Unr };
            let id = self.sub.bindings[p].id.clone();
            match cls {
                Cls::Ent => labels.entailed.push(id),
                Cls::Ref => labels.refuted.push(id),
                Cls::Cont => labels.contingent.push(id),
                Cls::Unr => labels.unresolved.push(id),
            }
        }
        AnswerOut::Closure { qid: qid.into(), labels: Some(labels), error: None }
    }

    fn selection(
        &mut self,
        qid: &str,
        universe: Option<&[String]>,
        rel: Relation,
        target: &Expr,
    ) -> Result<AnswerOut, String> {
        let uni = self.universe(universe)?;
        let touched = self.comps_of(vars_of(&[target]));
        let ids = self.with_engine(touched, |w, e, key| w.selection_in(e, key, &uni, rel, target))??;
        Ok(AnswerOut::Selection { qid: qid.into(), ids })
    }

    fn selection_in(
        &self,
        e: &mut Engine<'_>,
        key: &[u32],
        uni: &[usize],
        rel: Relation,
        target: &Expr,
    ) -> Result<Vec<String>, String> {
        let t = e.lit(target)?;
        // Per interpretation: can the target be true / false.
        let mut tsat = Vec::new();
        for i in 0..e.n_interps() {
            let mut a = e.interp_assumptions(i);
            a.push(t);
            let can_t = e.sat(&a)?;
            *a.last_mut().unwrap() = !t;
            tsat.push((can_t, e.sat(&a)?));
        }
        let base = self.base.as_ref().ok_or("base classification missing")?;
        let mut ids = Vec::new();
        for &p in uni {
            if !self.bound(p) {
                continue; // meaning must be established
            }
            let id = self.sub.bindings[p].id.as_str();
            let ok = if key.binary_search(&self.comp[p]).is_ok() {
                e.selects(id, rel, t)?
            } else {
                // Independent of the target: a conjunction is satisfiable iff both sides are.
                (0..tsat.len()).all(|i| {
                    rel_pairs(rel).iter().all(|&(ps, ts)| {
                        let p_ok = base[i][p] & if ps { 1 } else { 2 } != 0;
                        let t_ok = if ts { tsat[i].0 } else { tsat[i].1 };
                        !(p_ok && t_ok)
                    })
                })
            };
            if ok {
                ids.push(id.to_string());
            }
        }
        Ok(ids)
    }
}
