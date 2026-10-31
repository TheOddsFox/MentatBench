//! Query, answer and verdict wire types (docs/ir.md, "Queries", "Answers").

use crate::ir::{from_json, Expr};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Relation {
    Equiv,
    Implies,
    ImpliedBy,
    Excludes,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Claim {
    Exclusive,
    Exhaustive,
    Partition,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "family", rename_all = "lowercase")]
pub enum QueryBody {
    Closure { hyp: Vec<Expr> },
    Selection { relation: Relation, target: Expr },
    Feasibility { assume: Vec<Expr> },
    Partition {
        members: Vec<Expr>,
        claim: Claim,
        target: Option<Expr>,
    },
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Query {
    pub qid: String,
    #[serde(default)]
    pub universe: Option<Vec<String>>,
    /// Evidence scope: only constraints and bindings whose refs all lie inside it take part.
    #[serde(default)]
    pub evidence: Option<Vec<String>>,
    /// `eval` only: attach an evidence certificate to a `true` partition answer (docs/ir.md).
    #[serde(default)]
    pub certificate: bool,
    #[serde(flatten)]
    pub body: QueryBody,
}

impl Query {
    /// Deepest expression of the query (0 when it has none).
    pub fn max_depth(&self) -> u64 {
        let exprs: Vec<&Expr> = match &self.body {
            QueryBody::Closure { hyp } => hyp.iter().collect(),
            QueryBody::Selection { target, .. } => vec![target],
            QueryBody::Feasibility { assume } => assume.iter().collect(),
            QueryBody::Partition { members, target, .. } => members.iter().chain(target.iter()).collect(),
        };
        exprs.into_iter().map(Expr::depth).max().unwrap_or(0)
    }

    /// Parses one `queries.jsonl` line. serde cannot deny unknown keys next to the flattened body,
    /// so keys outside the family's field set are rejected here.
    pub fn from_line(line: &str) -> Result<Query, String> {
        // Typed parse first: serde rejects duplicate keys here, which a Value would merge silently.
        let q: Query = from_json(line)?;
        let v: serde_json::Value = from_json(line)?;
        let body: &[&str] = match v.get("family").and_then(|f| f.as_str()) {
            Some("closure") => &["hyp"],
            Some("selection") => &["relation", "target"],
            Some("feasibility") => &["assume"],
            Some("partition") => &["members", "claim", "target"],
            _ => &[],
        };
        if let Some(obj) = v.as_object() {
            // `null` would deserialize to "no scope", silently widening a scoped query: refuse it.
            if obj.get("evidence").is_some_and(serde_json::Value::is_null) {
                return Err("evidence must be a list of refs, not null".into());
            }
            if let Some(k) = obj.keys().find(|k| !["qid", "family", "universe", "evidence", "certificate"].contains(&k.as_str()) && !body.contains(&k.as_str())) {
                return Err(format!("unknown field {k}"));
            }
        }
        Ok(q)
    }

    /// Queries use source IDs and operators only; alias references are rejected.
    pub fn uses_ref(&self) -> bool {
        match &self.body {
            QueryBody::Closure { hyp } => hyp.iter().any(Expr::contains_ref),
            QueryBody::Selection { target, .. } => target.contains_ref(),
            QueryBody::Feasibility { assume } => assume.iter().any(Expr::contains_ref),
            QueryBody::Partition {
                members, target, ..
            } => {
                members.iter().any(Expr::contains_ref)
                    || target.as_ref().is_some_and(Expr::contains_ref)
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Labels {
    #[serde(rename = "ENTAILED")]
    pub entailed: Vec<String>,
    #[serde(rename = "REFUTED")]
    pub refuted: Vec<String>,
    #[serde(rename = "CONTINGENT")]
    pub contingent: Vec<String>,
    #[serde(rename = "UNRESOLVED")]
    pub unresolved: Vec<String>,
}

/// Engine output, one per query. Field sets match the contract exactly.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum AnswerOut {
    Closure {
        qid: String,
        labels: Option<Labels>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    Selection {
        qid: String,
        ids: Vec<String>,
    },
    Feasibility {
        qid: String,
        decision: String,
        witness: Option<BTreeMap<String, bool>>,
        core: Option<Vec<usize>>,
        missing: Vec<String>,
    },
    Partition {
        qid: String,
        decision: String,
        counterexample: Option<BTreeMap<String, bool>>,
        missing: Vec<String>,
        /// Certificate of a `true` answer (only when the query asked for one).
        #[serde(skip_serializing_if = "Option::is_none")]
        evidence: Option<Vec<String>>,
    },
    /// Query-level failure (bad reference, unknown universe ID, ...).
    Error {
        qid: String,
        error: String,
    },
}

/// Reader for answers given to `check`; only the certificate fields are used.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnswerIn {
    pub qid: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub decision: Option<String>,
    #[serde(default)]
    pub witness: Option<BTreeMap<String, bool>>,
    #[serde(default)]
    pub core: Option<Vec<usize>>,
    #[serde(default)]
    pub counterexample: Option<BTreeMap<String, bool>>,
    #[serde(default)]
    /// Gold-accepted qualifier codes claimed for UNRESOLVED; `null` and absent both mean none.
    pub missing: Option<Vec<String>>,
    /// Closure and selection answers as `eval` writes them; not used for certificates.
    #[serde(default)]
    pub labels: Option<serde::de::IgnoredAny>,
    #[serde(default)]
    pub ids: Option<serde::de::IgnoredAny>,
    /// Certificate of a `true` partition answer: the refs of the evidence scope it is checked in.
    /// `null` and absent both mean no certificate.
    #[serde(default)]
    pub evidence: Option<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Verdict {
    pub qid: String,
    pub cert_valid: bool,
    pub reason: String,
}
