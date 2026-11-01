//! Semantic checks on a loaded submission (docs/ir.md, SPEC.md sections 5 and 8).

use crate::ir::{expr_depth_cap, Expr, Profile, Status, Submission};
use serde::Serialize;
use std::collections::{BTreeMap, HashMap, HashSet};

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ValidationError {
    pub code: String,
    pub message: String,
}

fn err(code: &str, message: impl Into<String>) -> ValidationError {
    ValidationError {
        code: code.into(),
        message: message.into(),
    }
}

/// Weight sums are kept within 2^62 so that every engine total fits its machine integers.
const MAX_WEIGHT_SUM: i128 = 1i128 << 62;

/// Returns every violation found; an empty vector means the submission is valid.
///
/// Rules: no alias definition cycles; every `var`/`ref`/interpretation/
/// provenance reference resolves; bound bindings name an existing alias and cite evidence;
/// nontrivial constraints (any expr other than `true`) cite provenance; caps from `profile` when
/// given. Expression caps: `max_expr_nodes` bounds the total node count of all alias definitions
/// and constraints (each node once; a `ref` node is one node); `max_expr_depth` (clamped to
/// `MAX_EXPR_DEPTH`) bounds nesting; `max_weight_bits` bounds every weight and every cardinality
/// or weighted bound; the absolute weight sum of each `wsum` is at most 2^62.
pub fn validate(sub: &Submission, profile: Option<&Profile>) -> Vec<ValidationError> {
    let p = profile.cloned().unwrap_or_default();
    let th = &sub.theory;
    let mut errs = Vec::new();

    if let Some(t) = p.query_timeout_s {
        if !t.is_finite() || t <= 0.0 || t > 1e9 {
            errs.push(err("profile_invalid", format!("query_timeout_s must be finite, positive and at most 1e9 (got {t})")));
        }
    }
    if p.max_certificate_refs == Some(0) {
        errs.push(err("profile_invalid", "max_certificate_refs must be at least 1"));
    }
    if th.interpretations.is_empty() {
        errs.push(err("no_interpretations", "theory has no interpretations"));
    }
    let mut interps = HashSet::new();
    for name in &th.interpretations {
        if !interps.insert(name.as_str()) {
            errs.push(err("duplicate_interpretation", format!("interpretation {name} repeated")));
        }
    }
    if let Some(m) = p.max_interpretations {
        if th.interpretations.len() as u64 > m {
            errs.push(err("cap_interpretations", format!("{} > {m}", th.interpretations.len())));
        }
    }
    if let Some(m) = p.max_bytes {
        if sub.bytes > m {
            errs.push(err("cap_bytes", format!("{} bytes > {m}", sub.bytes)));
        }
    }
    for name in &th.duplicate_aliases {
        errs.push(err("duplicate_alias", format!("alias {name} defined more than once")));
    }

    let mut ids = HashSet::new();
    for b in &sub.bindings {
        if !ids.insert(b.id.as_str()) {
            errs.push(err("duplicate_id", format!("binding {} repeated", b.id)));
        }
    }
    let mut refs = HashSet::new();
    for pr in &sub.provenance {
        if !refs.insert(pr.reference.as_str()) {
            errs.push(err("duplicate_ref", format!("provenance ref {} repeated", pr.reference)));
        }
    }

    for b in &sub.bindings {
        match (b.status, &b.class) {
            (Status::Bound, None) => {
                errs.push(err("bound_without_class", format!("binding {} is bound but has no class", b.id)))
            }
            (Status::Bound, Some(c)) if !th.aliases.contains_key(c) => {
                errs.push(err("unknown_alias", format!("binding {} names missing class {c}", b.id)))
            }
            (Status::Unresolved, Some(_)) => errs.push(err(
                "unresolved_with_class",
                format!("binding {} is unresolved but names a class", b.id),
            )),
            _ => {}
        }
        if b.status == Status::Bound && b.evidence.is_empty() {
            errs.push(err("missing_provenance", format!("bound binding {} cites no evidence", b.id)));
        }
        for r in &b.evidence {
            if !refs.contains(r.as_str()) {
                errs.push(err("unknown_ref", format!("binding {} cites unknown ref {r}", b.id)));
            }
        }
    }

    let max_depth = expr_depth_cap(Some(&p));
    let mut total = 0u64;
    for (name, e) in &th.aliases {
        total += scan_expr(e, &format!("alias {name}"), &ids, &th.aliases, &p, max_depth, &mut errs);
    }
    check_alias_cycles(sub, &mut errs);

    let mut cids = HashSet::new();
    for c in &th.constraints {
        let at = format!("constraint {}", c.id);
        if !cids.insert(c.id.as_str()) {
            errs.push(err("duplicate_constraint", format!("constraint {} repeated", c.id)));
        }
        if let Some(list) = &c.interps {
            if list.is_empty() {
                errs.push(err("empty_interps", format!("{at} lists no interpretations")));
            }
            for n in list {
                if !interps.contains(n.as_str()) {
                    errs.push(err("unknown_interpretation", format!("{at} names unknown interpretation {n}")));
                }
            }
        }
        for r in &c.provenance {
            if !refs.contains(r.as_str()) {
                errs.push(err("unknown_ref", format!("{at} cites unknown ref {r}")));
            }
        }
        if c.expr != Expr::True && c.provenance.is_empty() {
            errs.push(err("missing_provenance", format!("{at} is nontrivial but cites no provenance")));
        }
        total += scan_expr(&c.expr, &at, &ids, &th.aliases, &p, max_depth, &mut errs);
    }
    if let Some(m) = p.max_expr_nodes {
        if total > m {
            errs.push(err("cap_expr_nodes", format!("{total} expression nodes > {m}")));
        }
    }
    errs
}

/// One iterative pass over an expression: unknown ids and refs, weight caps, sums and depth.
/// Returns the node count (alias refs are leaves).
fn scan_expr(
    e: &Expr,
    at: &str,
    ids: &HashSet<&str>,
    aliases: &BTreeMap<String, Expr>,
    p: &Profile,
    max_depth: u64,
    errs: &mut Vec<ValidationError>,
) -> u64 {
    let mut count = 0u64;
    let mut depth_reported = false;
    let lim: Option<i128> = p.max_weight_bits.filter(|&b| b < 63).map(|b| 1i128 << b);
    let too_big = |v: i64| lim.is_some_and(|l| (v as i128).abs() >= l);
    let mut stack = vec![(e, 1u64)];
    while let Some((n, depth)) = stack.pop() {
        count += 1;
        if depth > max_depth && !depth_reported {
            depth_reported = true;
            errs.push(err("cap_depth", format!("{at} nests deeper than {max_depth}")));
        }
        match n {
            Expr::Var { id } if !ids.contains(id.as_str()) => {
                errs.push(err("unknown_id", format!("{at} uses unknown outcome id {id}")))
            }
            Expr::Ref { name } if !aliases.contains_key(name) => {
                errs.push(err("unknown_alias", format!("{at} uses unknown alias {name}")))
            }
            Expr::Atmost { k, .. } | Expr::Atleast { k, .. } | Expr::Exactly { k, .. } if too_big(*k) => {
                errs.push(err("cap_weight_bits", format!("{at} has a cardinality bound beyond max_weight_bits")))
            }
            Expr::Wsum { k, terms, .. } => {
                if too_big(*k) || terms.iter().any(|t| too_big(t.w)) {
                    errs.push(err("cap_weight_bits", format!("{at} has weight or bound beyond max_weight_bits")));
                }
                let sum: i128 = terms.iter().map(|t| (t.w as i128).abs()).sum();
                if sum > MAX_WEIGHT_SUM {
                    errs.push(err("cap_weight_sum", format!("{at} has absolute weights summing above 2^62")));
                }
            }
            _ => {}
        }
        stack.extend(n.children().into_iter().map(|c| (c, depth + 1)));
    }
    count
}

/// A bare literal definition: `var(id)` or `not(var(id))`. Bindings may name such an alias (the
/// shipped gold encodes each class as the literal of its representative outcome).
fn is_literal(e: &Expr) -> bool {
    match e {
        Expr::Var { .. } => true,
        Expr::Not { a } => matches!(a.as_ref(), Expr::Var { .. }),
        _ => false,
    }
}

/// Alias names in dependency order (definitions before their users), computed iteratively so
/// that long alias chains cannot overflow the stack. Err names an alias on a definition cycle.
pub fn alias_order(aliases: &BTreeMap<String, Expr>) -> Result<Vec<&str>, String> {
    fn deps(e: &Expr) -> Vec<&str> {
        let mut out = Vec::new();
        let mut stack = vec![e];
        while let Some(n) = stack.pop() {
            if let Expr::Ref { name } = n {
                out.push(name.as_str());
            }
            stack.extend(n.children());
        }
        out
    }
    // false = on the current DFS path, true = finished.
    let mut state: HashMap<&str, bool> = HashMap::new();
    let mut order = Vec::with_capacity(aliases.len());
    for root in aliases.keys() {
        if state.contains_key(root.as_str()) {
            continue;
        }
        state.insert(root.as_str(), false);
        // (name, its refs, index of next ref to visit)
        let mut stack: Vec<(&str, Vec<&str>, usize)> = vec![(root.as_str(), deps(&aliases[root]), 0)];
        while let Some(top) = stack.len().checked_sub(1) {
            let (name, next) = (stack[top].0, stack[top].2);
            if next < stack[top].1.len() {
                let d = stack[top].1[next];
                stack[top].2 += 1;
                if !aliases.contains_key(d) {
                    continue; // unknown refs are reported by the expression checks
                }
                match state.get(d) {
                    Some(false) => {
                        return Err(format!("alias {name} refers back to {d} through a definition cycle"))
                    }
                    Some(true) => {}
                    None => {
                        state.insert(d, false);
                        stack.push((d, deps(&aliases[d]), 0));
                    }
                }
            } else {
                stack.pop();
                state.insert(name, true);
                order.push(name);
            }
        }
    }
    Ok(order)
}

/// Expression-definition cycles are rejected: aliases referring to each other through `ref`
/// (`alias_cycle`), and cycles through bindings that involve a non-literal definition
/// (`alias_binding_cycle`, see `binding_cycle_culprit`). Implication cycles are not affected.
fn check_alias_cycles(sub: &Submission, errs: &mut Vec<ValidationError>) {
    if let Err(msg) = alias_order(&sub.theory.aliases) {
        errs.push(err("alias_cycle", msg));
        return;
    }
    if let Some(name) = binding_cycle_culprit(sub) {
        errs.push(err(
            "alias_binding_cycle",
            format!("alias {name} is a non-literal definition on a cycle through its own bound variables"),
        ));
    }
}

/// Tarjan over the alias graph whose edges are `ref`s and, through each variable, the alias that
/// variable is bound to. A cycle is a multi-node strongly connected component or a self-loop. A
/// literal alias may sit on such a cycle (equivalent bound literals); a non-literal one may not.
/// Returns the first non-literal alias found on a cycle. Iterative, so long chains are safe.
fn binding_cycle_culprit(sub: &Submission) -> Option<String> {
    let entries: Vec<(&str, &Expr)> = sub.theory.aliases.iter().map(|(k, v)| (k.as_str(), v)).collect();
    let index_of: HashMap<&str, usize> = entries.iter().enumerate().map(|(i, (k, _))| (*k, i)).collect();
    let bound_to: HashMap<&str, usize> = sub
        .bindings
        .iter()
        .filter(|b| b.status == Status::Bound)
        .filter_map(|b| Some((b.id.as_str(), *index_of.get(b.class.as_deref()?)?)))
        .collect();
    let n = entries.len();
    let mut literal = Vec::with_capacity(n);
    let mut edges: Vec<Vec<usize>> = Vec::with_capacity(n);
    for (_, def) in &entries {
        literal.push(is_literal(def));
        let mut out = Vec::new();
        let mut stack = vec![*def];
        while let Some(e) = stack.pop() {
            match e {
                Expr::Ref { name } => out.extend(index_of.get(name.as_str()).copied()),
                Expr::Var { id } => out.extend(bound_to.get(id.as_str()).copied()),
                _ => {}
            }
            stack.extend(e.children());
        }
        edges.push(out);
    }
    const NONE: usize = usize::MAX;
    let mut index = vec![NONE; n];
    let mut low = vec![0usize; n];
    let mut on_stack = vec![false; n];
    let mut scc: Vec<usize> = Vec::new();
    let mut counter = 0usize;
    for root in 0..n {
        if index[root] != NONE {
            continue;
        }
        // Call frames: (node, next edge to visit).
        let mut call: Vec<(usize, usize)> = vec![(root, 0)];
        index[root] = counter;
        low[root] = counter;
        counter += 1;
        scc.push(root);
        on_stack[root] = true;
        while let Some(&(v, next)) = call.last() {
            if next < edges[v].len() {
                call.last_mut().unwrap().1 += 1;
                let w = edges[v][next];
                if index[w] == NONE {
                    index[w] = counter;
                    low[w] = counter;
                    counter += 1;
                    scc.push(w);
                    on_stack[w] = true;
                    call.push((w, 0));
                } else if on_stack[w] {
                    low[v] = low[v].min(index[w]);
                }
            } else {
                call.pop();
                if let Some(&(p, _)) = call.last() {
                    low[p] = low[p].min(low[v]);
                }
                if low[v] == index[v] {
                    let mut comp = Vec::new();
                    loop {
                        let w = scc.pop().unwrap();
                        on_stack[w] = false;
                        comp.push(w);
                        if w == v {
                            break;
                        }
                    }
                    let cyclic = comp.len() > 1 || edges[v].contains(&v);
                    if cyclic {
                        if let Some(&w) = comp.iter().find(|&&w| !literal[w]) {
                            return Some(entries[w].0.to_string());
                        }
                    }
                }
            }
        }
    }
    None
}
