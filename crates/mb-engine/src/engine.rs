//! CNF encoding of a submission into one shared varisat solver.
//!
//! Every gate is a full (reified) Tseitin definition, so auxiliary variables are functions of
//! the outcome IDs. Interpretation-specific constraints are gated by activation literals:
//! interpretation `i` is checked by assuming `act[i]` and `!act[j]` for all other `j`.
//! Aliases are encoded once and memoised, so shared sub-DAGs are not re-expanded.

use mb_ir::{Binding, Cmp, Expr, Status, Submission};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::cell::Cell;
use std::time::Instant;
use varisat::{ExtendFormula, Lit, Solver};

thread_local! {
    static DEADLINE: Cell<Option<Instant>> = const { Cell::new(None) };
}

/// Wall-clock limit for every `sat` call on this thread until reset (`None` = unlimited).
pub fn set_deadline(d: Option<Instant>) {
    DEADLINE.with(|c| c.set(d));
}

pub struct Engine<'a> {
    pub(crate) sub: &'a Submission,
    s: Solver<'a>,
    tru: Lit,
    pub(crate) ids: HashMap<&'a str, Lit>,
    pub(crate) binding: HashMap<&'a str, &'a Binding>,
    /// Binding IDs in file order (default universe).
    pub(crate) all_ids: Vec<&'a str>,
    interp_idx: HashMap<&'a str, usize>,
    /// Position of each binding ID in `all_ids`.
    pos: HashMap<&'a str, usize>,
    alias_memo: HashMap<&'a str, Lit>,
    /// Alias names, definitions before their users (see `encode_aliases`).
    alias_order: Vec<&'a str>,
    act: Vec<Lit>,
}

/// At-most-one over up to this many literals is pairwise; larger ones use the adder encoding.
const AMO_DIRECT_MAX: usize = 12;

fn bit(k: u64, i: usize) -> bool {
    i < 64 && (k >> i) & 1 == 1
}

impl<'a> Engine<'a> {
    /// Encodes bindings (permanent equivalences) and constraints. The submission must be valid.
    pub fn new(sub: &'a Submission) -> Result<Self, String> {
        let mut s: Solver<'a> = Solver::new();
        let tru = s.new_lit();
        s.add_clause(&[tru]);
        let mut e = Engine {
            sub,
            s,
            tru,
            ids: HashMap::new(),
            binding: HashMap::new(),
            all_ids: Vec::new(),
            interp_idx: HashMap::new(),
            pos: HashMap::new(),
            alias_memo: HashMap::new(),
            alias_order: Vec::new(),
            act: Vec::new(),
        };
        for b in &sub.bindings {
            let l = e.fresh();
            e.ids.insert(b.id.as_str(), l);
            e.binding.insert(b.id.as_str(), b);
            e.pos.insert(b.id.as_str(), e.all_ids.len());
            e.all_ids.push(b.id.as_str());
        }
        for (i, name) in sub.theory.interpretations.iter().enumerate() {
            let a = e.fresh();
            e.act.push(a);
            e.interp_idx.insert(name.as_str(), i);
        }
        e.alias_order = mb_ir::alias_order(&sub.theory.aliases)?;
        e.encode_aliases()?;
        e.encode_bindings()?;
        for c in &sub.theory.constraints {
            match &c.interps {
                None => e.assert_top(&c.expr, &[])?,
                Some(list) => {
                    for n in list {
                        let i = *e
                            .interp_idx
                            .get(n.as_str())
                            .ok_or_else(|| format!("unknown interpretation {n}"))?;
                        let a = e.act[i];
                        e.assert_top(&c.expr, &[!a])?;
                    }
                }
            }
        }
        Ok(e)
    }

    /// Asserts `e` under clause prefix `pre` (`[!act]` for an interpretation, empty if global).
    /// Top-level and/or/implies and small at-most-one / at-least-one use direct clauses; any other
    /// expression is encoded reified and asserted as a unit.
    fn assert_top(&mut self, e: &Expr, pre: &[Lit]) -> Result<(), String> {
        let with = |c: &[Lit]| -> Vec<Lit> { pre.iter().copied().chain(c.iter().copied()).collect() };
        match e {
            Expr::And { args } => {
                for a in args {
                    self.assert_top(a, pre)?;
                }
            }
            Expr::Or { args } if !args.is_empty() => {
                let ls = self.lits(args)?;
                self.clause(&with(&ls));
            }
            Expr::Implies { a, b } => {
                let x = self.lit(a)?;
                let y = self.lit(b)?;
                self.clause(&with(&[!x, y]));
            }
            Expr::Atmost { k: 1, args } if args.len() <= AMO_DIRECT_MAX => {
                let ls = self.lits(args)?;
                self.at_most_one(&ls, pre);
            }
            Expr::Atleast { k: 1, args } if !args.is_empty() => {
                let ls = self.lits(args)?;
                self.clause(&with(&ls));
            }
            Expr::Exactly { k: 1, args } if !args.is_empty() && args.len() <= AMO_DIRECT_MAX => {
                let ls = self.lits(args)?;
                self.at_most_one(&ls, pre);
                self.clause(&with(&ls));
            }
            _ => {
                let r = self.lit(e)?;
                self.clause(&with(&[r]));
            }
        }
        Ok(())
    }

    fn at_most_one(&mut self, ls: &[Lit], pre: &[Lit]) {
        for i in 0..ls.len() {
            for j in i + 1..ls.len() {
                let c: Vec<Lit> = pre.iter().copied().chain([!ls[i], !ls[j]]).collect();
                self.clause(&c);
            }
        }
    }

    pub(crate) fn fresh(&mut self) -> Lit {
        self.s.new_lit()
    }

    pub(crate) fn clause(&mut self, c: &[Lit]) {
        self.s.add_clause(c);
    }

    /// Bound bindings are permanent equivalences `var <-> class` (negated if flagged).
    fn encode_bindings(&mut self) -> Result<(), String> {
        let sub = self.sub;
        for b in &sub.bindings {
            if b.status == Status::Bound {
                let class = b.class.as_deref().ok_or("bound binding without class")?;
                let c = self.alias(class)?;
                let c = if b.negated { !c } else { c };
                let v = self.ids[b.id.as_str()];
                self.clause(&[!v, c]);
                self.clause(&[v, !c]);
            }
        }
        Ok(())
    }

    /// Maximal partial witness over `support` (D-A). Each ID in turn takes the first polarity that
    /// keeps `qlits` satisfiable in every interpretation, and is left out when neither does. A
    /// polarity refused for a smaller assignment is refused for every larger one, so no
    /// unassigned ID can be added afterwards. Requires `qlits` to be satisfiable in every
    /// interpretation (callers check this first).
    pub(crate) fn joint_model(
        &mut self,
        support: &BTreeSet<String>,
        qlits: &[Lit],
    ) -> Result<BTreeMap<String, bool>, String> {
        let mut fixed: Vec<Lit> = Vec::new();
        let mut out = BTreeMap::new();
        for id in support {
            let l = self.ids[id.as_str()];
            for v in [true, false] {
                fixed.push(if v { l } else { !l });
                if self.extends_everywhere(&fixed, qlits)? {
                    out.insert(id.clone(), v);
                    break;
                }
                fixed.pop();
            }
        }
        Ok(out)
    }

    /// Whether `fixed` and `qlits` are satisfiable in each interpretation separately.
    pub(crate) fn extends_everywhere(&mut self, fixed: &[Lit], qlits: &[Lit]) -> Result<bool, String> {
        for i in 0..self.n_interps() {
            let mut a = self.interp_assumptions(i);
            a.extend_from_slice(fixed);
            a.extend_from_slice(qlits);
            if !self.sat(&a)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    pub(crate) fn konst(&self, v: bool) -> Lit {
        if v {
            self.tru
        } else {
            !self.tru
        }
    }

    pub(crate) fn n_interps(&self) -> usize {
        self.act.len()
    }

    /// Encodes every alias in dependency order, so alias recursion depth is bounded by
    /// expression nesting rather than by alias-chain length.
    fn encode_aliases(&mut self) -> Result<(), String> {
        for name in self.alias_order.clone() {
            self.alias(name)?;
        }
        Ok(())
    }

    /// Name of the first interpretation whose base theory (bindings plus its own constraints,
    /// no hypothesis) is unsatisfiable. A `Some` makes the whole submission invalid.
    pub fn inconsistent_interpretation(&mut self) -> Result<Option<String>, String> {
        let sub = self.sub;
        for i in 0..self.n_interps() {
            let a = self.interp_assumptions(i);
            if !self.sat(&a)? {
                return Ok(Some(sub.theory.interpretations[i].clone()));
            }
        }
        Ok(None)
    }

    /// Assumptions selecting interpretation `i` alone.
    pub(crate) fn interp_assumptions(&self, i: usize) -> Vec<Lit> {
        self.act
            .iter()
            .enumerate()
            .map(|(j, &a)| if j == i { a } else { !a })
            .collect()
    }

    pub(crate) fn is_bound(&self, id: &str) -> bool {
        self.binding
            .get(id)
            .is_some_and(|b| b.status == Status::Bound)
    }

    /// SAT check under assumptions. No model is extracted; read values with `val` (O(1) each).
    /// Fails with `timeout` once the thread's query deadline (see `set_deadline`) has passed.
    pub(crate) fn sat(&mut self, assumps: &[Lit]) -> Result<bool, String> {
        let dl = DEADLINE.with(|d| d.get());
        if dl.is_some_and(|d| Instant::now() > d) {
            return Err("timeout".into());
        }
        self.s.set_deadline(dl);
        self.s.assume(assumps);
        self.s.solve().map_err(|e| match e {
            varisat::solver::SolverError::Interrupted => "timeout".to_string(),
            e => format!("solver error: {e:?}"),
        })
    }

    /// Value of literal `l` in the last SAT model.
    pub(crate) fn val(&self, l: Lit) -> bool {
        self.s.model_value(l.var()).unwrap_or(false) == l.is_positive()
    }

    /// Assumption literals responsible for the last UNSAT answer.
    pub(crate) fn core(&self) -> Vec<Lit> {
        self.s.failed_core().map(|c| c.to_vec()).unwrap_or_default()
    }

    /// Per-interpretation SAT answers under extra assumptions `base`.
    pub(crate) fn per_interp_sat(&mut self, base: &[Lit]) -> Result<Vec<bool>, String> {
        let mut out = Vec::with_capacity(self.n_interps());
        for i in 0..self.n_interps() {
            let mut a = self.interp_assumptions(i);
            a.extend_from_slice(base);
            out.push(self.sat(&a)?);
        }
        Ok(out)
    }

    /// Alias memo: each definition is encoded once; acyclicity is guaranteed by validation.
    fn alias(&mut self, name: &str) -> Result<Lit, String> {
        if let Some(&l) = self.alias_memo.get(name) {
            return Ok(l);
        }
        let sub = self.sub;
        let (k, def) = sub
            .theory
            .aliases
            .get_key_value(name)
            .ok_or_else(|| format!("unknown_alias {name}"))?;
        let l = self.lit(def)?;
        self.alias_memo.insert(k.as_str(), l);
        Ok(l)
    }

    pub(crate) fn lits(&mut self, es: &[Expr]) -> Result<Vec<Lit>, String> {
        es.iter().map(|e| self.lit(e)).collect()
    }

    /// Literal equivalent to `e` (definitional, so adding it permanently is safe).
    pub(crate) fn lit(&mut self, e: &Expr) -> Result<Lit, String> {
        Ok(match e {
            Expr::True => self.tru,
            Expr::False => !self.tru,
            Expr::Var { id } => *self
                .ids
                .get(id.as_str())
                .ok_or_else(|| format!("unknown_id {id}"))?,
            Expr::Ref { name } => self.alias(name)?,
            Expr::Not { a } => !self.lit(a)?,
            Expr::And { args } => {
                let ls = self.lits(args)?;
                self.and(&ls)
            }
            Expr::Or { args } => {
                let ls = self.lits(args)?;
                self.or(&ls)
            }
            Expr::Implies { a, b } => {
                let x = self.lit(a)?;
                let y = self.lit(b)?;
                self.or(&[!x, y])
            }
            Expr::Iff { a, b } => {
                let x = self.lit(a)?;
                let y = self.lit(b)?;
                !self.xor(x, y)
            }
            Expr::Atmost { k, args } => self.card(args, Cmp::Le, *k)?,
            Expr::Atleast { k, args } => self.card(args, Cmp::Ge, *k)?,
            Expr::Exactly { k, args } => self.card(args, Cmp::Eq, *k)?,
            Expr::Wsum { cmp, k, terms } => {
                let mut ts = Vec::with_capacity(terms.len());
                for t in terms {
                    ts.push((t.w, self.lit(&t.e)?));
                }
                self.pb(&ts, *cmp, *k)?
            }
        })
    }

    fn card(&mut self, args: &[Expr], cmp: Cmp, k: i64) -> Result<Lit, String> {
        let ts: Vec<(i64, Lit)> = self.lits(args)?.into_iter().map(|l| (1, l)).collect();
        self.pb(&ts, cmp, k)
    }

    // ---- Tseitin gates -------------------------------------------------------------------

    fn and(&mut self, xs: &[Lit]) -> Lit {
        match xs.len() {
            0 => self.tru,
            1 => xs[0],
            _ => {
                let y = self.fresh();
                for &x in xs {
                    self.clause(&[!y, x]);
                }
                let mut c: Vec<Lit> = xs.iter().map(|&x| !x).collect();
                c.push(y);
                self.clause(&c);
                y
            }
        }
    }

    fn or(&mut self, xs: &[Lit]) -> Lit {
        let n: Vec<Lit> = xs.iter().map(|&x| !x).collect();
        !self.and(&n)
    }

    fn xor(&mut self, a: Lit, b: Lit) -> Lit {
        let y = self.fresh();
        self.clause(&[!y, a, b]);
        self.clause(&[!y, !a, !b]);
        self.clause(&[y, !a, b]);
        self.clause(&[y, a, !b]);
        y
    }

    fn maj(&mut self, a: Lit, b: Lit, c: Lit) -> Lit {
        let y = self.fresh();
        self.clause(&[!a, !b, y]);
        self.clause(&[!a, !c, y]);
        self.clause(&[!b, !c, y]);
        self.clause(&[a, b, !y]);
        self.clause(&[a, c, !y]);
        self.clause(&[b, c, !y]);
        y
    }

    // ---- Pseudo-Boolean constraints -------------------------------------------------------

    /// Reified `sum(w_i * l_i) cmp k`. Negative weights are rewritten onto negated literals.
    /// The sum is built as a binary number by a full/half-adder column reduction, then compared
    /// with a constant by a reified MSB-first comparator.
    pub(crate) fn pb(&mut self, terms: &[(i64, Lit)], cmp: Cmp, k: i64) -> Result<Lit, String> {
        let mut cst: i128 = 0;
        let mut items: Vec<(u64, Lit)> = Vec::new();
        for &(w, l) in terms {
            match w.signum() {
                0 => {}
                1 => items.push((w as u64, l)),
                _ => {
                    // w*x = w + |w|*(not x)
                    cst += w as i128;
                    items.push((w.unsigned_abs(), !l));
                }
            }
        }
        let total: i128 = items.iter().map(|&(a, _)| a as i128).sum();
        if total > (1i128 << 62) {
            return Err("weight_overflow".into());
        }
        let kk = k as i128 - cst;
        if kk < 0 || kk > total {
            let v = match (cmp, kk < 0) {
                (Cmp::Le, true) | (Cmp::Ge, false) => false,
                (Cmp::Le, false) => true,
                (Cmp::Ge, true) => true,
                (Cmp::Eq, _) => false,
            };
            return Ok(self.konst(v));
        }
        let bits = self.sum_bits(&items, total as u64);
        let (gt, lt) = self.cmp_const(&bits, kk as u64);
        Ok(match cmp {
            Cmp::Le => !gt,
            Cmp::Ge => !lt,
            Cmp::Eq => self.and(&[!gt, !lt]),
        })
    }

    /// Binary representation (LSB first) of sum(a_i * l_i), width covering `total`.
    fn sum_bits(&mut self, items: &[(u64, Lit)], total: u64) -> Vec<Lit> {
        let width = (64 - total.leading_zeros()) as usize;
        let mut cols: Vec<Vec<Lit>> = vec![Vec::new(); width];
        for &(a, l) in items {
            let mut b = 0;
            while (a >> b) != 0 {
                if (a >> b) & 1 == 1 {
                    if cols.len() <= b {
                        cols.resize(b + 1, Vec::new());
                    }
                    cols[b].push(l);
                }
                b += 1;
            }
        }
        let mut out = Vec::new();
        let mut b = 0;
        while b < cols.len() {
            while cols[b].len() >= 2 {
                let x = cols[b].pop().unwrap();
                let y = cols[b].pop().unwrap();
                let (s, c) = match cols[b].pop() {
                    Some(z) => {
                        let t = self.xor(x, y);
                        (self.xor(t, z), self.maj(x, y, z))
                    }
                    None => (self.xor(x, y), self.and(&[x, y])),
                };
                cols[b].push(s);
                if cols.len() == b + 1 {
                    cols.push(Vec::new());
                }
                cols[b + 1].push(c);
            }
            out.push(cols[b].pop().unwrap_or(self.konst(false)));
            b += 1;
        }
        while out.len() < width {
            out.push(self.konst(false));
        }
        out
    }

    /// Reified `N > k` (first) and `N < k` (second) for an unsigned binary N and constant k.
    fn cmp_const(&mut self, bits: &[Lit], k: u64) -> (Lit, Lit) {
        let mut gt = self.konst(false);
        let mut lt = self.konst(false);
        let mut eq = self.konst(true);
        for i in (0..bits.len()).rev() {
            let n = bits[i];
            if bit(k, i) {
                let t = self.and(&[!n, eq]);
                lt = self.or(&[lt, t]);
                eq = self.and(&[eq, n]);
            } else {
                let t = self.and(&[n, eq]);
                gt = self.or(&[gt, t]);
                eq = self.and(&[eq, !n]);
            }
        }
        (gt, lt)
    }

    // ---- Helpers for query families ------------------------------------------------------

    /// Outcome IDs reachable from `exprs`, following alias references.
    pub(crate) fn support(&self, exprs: &[&Expr]) -> BTreeSet<String> {
        let sub = self.sub;
        let mut out = BTreeSet::new();
        let mut seen: HashSet<&str> = HashSet::new();
        let mut stack: Vec<&Expr> = exprs.to_vec();
        while let Some(e) = stack.pop() {
            match e {
                Expr::Var { id } => {
                    out.insert(id.clone());
                }
                Expr::Ref { name } => {
                    if seen.insert(name.as_str()) {
                        if let Some(d) = sub.theory.aliases.get(name) {
                            stack.push(d);
                        }
                    }
                }
                _ => stack.extend(e.children()),
            }
        }
        out
    }

    /// Qualifier codes of unresolved bindings among `ids`.
    pub(crate) fn missing_codes(&self, ids: &BTreeSet<String>) -> Vec<String> {
        let mut codes = BTreeSet::new();
        for id in ids {
            if let Some(b) = self.binding.get(id.as_str()) {
                if b.status == Status::Unresolved {
                    codes.extend(b.missing.iter().cloned());
                }
            }
        }
        codes.into_iter().collect()
    }

    /// Resolves a query universe (default: every binding ID), dropping duplicates in order.
    pub(crate) fn universe(&self, given: Option<&[String]>) -> Result<Vec<String>, String> {
        let Some(list) = given else {
            return Ok(self.all_ids.iter().map(|s| s.to_string()).collect());
        };
        let mut seen = HashSet::new();
        let mut out = Vec::new();
        for id in list {
            if !self.ids.contains_key(id.as_str()) {
                return Err(format!("unknown_id {id} in universe"));
            }
            if seen.insert(id.as_str()) {
                out.push(id.clone());
            }
        }
        Ok(out)
    }

    /// Reified claim violation for partition queries.
    pub(crate) fn violation(
        &mut self,
        members: &[Lit],
        target: Lit,
        claim: mb_ir::Claim,
    ) -> Result<Lit, String> {
        use mb_ir::Claim::*;
        let mut parts = Vec::new();
        if matches!(claim, Exclusive | Partition) {
            // Some pair overlaps <=> at least two members hold.
            let ts: Vec<(i64, Lit)> = members.iter().map(|&l| (1, l)).collect();
            parts.push(self.pb(&ts, Cmp::Ge, 2)?);
        }
        if matches!(claim, Exhaustive | Partition) {
            let any = self.or(members);
            parts.push(self.and(&[target, !any]));
        }
        if claim == Partition {
            for &m in members {
                parts.push(self.and(&[m, !target]));
            }
        }
        Ok(self.or(&parts))
    }
}
