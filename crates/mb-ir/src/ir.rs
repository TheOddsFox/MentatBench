//! Submission-side wire types (docs/ir.md): Expr, bindings, theory, provenance, profile.

use crate::sha256::sha256_hex;
use crate::validate::ValidationError;
use serde::de::{DeserializeOwned, MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

/// Hard ceiling on expression depth (also the default of the `max_expr_depth` profile key).
pub const MAX_EXPR_DEPTH: u64 = 100_000;

/// Effective expression depth cap: the profile's `max_expr_depth`, clamped to `MAX_EXPR_DEPTH`.
pub fn expr_depth_cap(p: Option<&Profile>) -> u64 {
    p.and_then(|p| p.max_expr_depth).unwrap_or(MAX_EXPR_DEPTH).min(MAX_EXPR_DEPTH)
}

/// Nesting limit for any JSON document read by this crate. Measured on the 512 MiB worker, parsing
/// and dropping a deep expression takes about 0.7 to 1 KiB of stack per nesting level. 250_000 levels
/// therefore fit a 256 MiB stack (checked), a 2x margin on the production worker. Expressions nest at
/// most three JSON levels per expression level, so wsum chains reach fewer expression levels.
pub const MAX_JSON_NESTING: usize = 250_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Cmp {
    Le,
    Ge,
    Eq,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase", deny_unknown_fields)]
pub enum Expr {
    True,
    False,
    Var { id: String },
    Ref { name: String },
    Not { a: Box<Expr> },
    And { args: Vec<Expr> },
    Or { args: Vec<Expr> },
    Implies { a: Box<Expr>, b: Box<Expr> },
    Iff { a: Box<Expr>, b: Box<Expr> },
    Atmost { k: i64, args: Vec<Expr> },
    Atleast { k: i64, args: Vec<Expr> },
    Exactly { k: i64, args: Vec<Expr> },
    Wsum { cmp: Cmp, k: i64, terms: Vec<Term> },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Term {
    pub w: i64,
    pub e: Expr,
}

impl Expr {
    /// True if any node is an alias reference (not allowed inside queries).
    pub fn contains_ref(&self) -> bool {
        let mut stack = vec![self];
        while let Some(n) = stack.pop() {
            if matches!(n, Expr::Ref { .. }) {
                return true;
            }
            stack.extend(n.children());
        }
        false
    }

    /// Direct sub-expressions (alias refs are leaves; their definitions live in the theory).
    pub fn children(&self) -> Vec<&Expr> {
        match self {
            Expr::True | Expr::False | Expr::Var { .. } | Expr::Ref { .. } => vec![],
            Expr::Not { a } => vec![&**a],
            Expr::And { args } | Expr::Or { args } => args.iter().collect(),
            Expr::Atmost { args, .. } | Expr::Atleast { args, .. } | Expr::Exactly { args, .. } => {
                args.iter().collect()
            }
            Expr::Implies { a, b } | Expr::Iff { a, b } => vec![&**a, &**b],
            Expr::Wsum { terms, .. } => terms.iter().map(|t| &t.e).collect(),
        }
    }

    /// Longest root-to-leaf path (a leaf has depth 1), computed without recursion.
    pub fn depth(&self) -> u64 {
        let mut max = 0;
        let mut stack = vec![(self, 1u64)];
        while let Some((n, d)) = stack.pop() {
            max = max.max(d);
            stack.extend(n.children().into_iter().map(|c| (c, d + 1)));
        }
        max
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Bound,
    Unresolved,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub id: String,
    pub status: Status,
    pub class: Option<String>,
    #[serde(default)]
    pub negated: bool,
    #[serde(default)]
    pub missing: Vec<String>,
    #[serde(default)]
    pub evidence: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Constraint {
    pub id: String,
    pub expr: Expr,
    /// `None` means the constraint holds in every interpretation.
    pub interps: Option<Vec<String>>,
    #[serde(default)]
    pub provenance: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Theory {
    pub interpretations: Vec<String>,
    pub aliases: BTreeMap<String, Expr>,
    pub constraints: Vec<Constraint>,
    /// Alias names given more than once in `theory.json` (kept out of `aliases`; validation rejects).
    #[serde(skip)]
    pub duplicate_aliases: Vec<String>,
}

/// Alias object as a list of entries, so duplicate names are seen before a map collapses them.
struct AliasEntries(Vec<(String, Expr)>);

impl<'de> Deserialize<'de> for AliasEntries {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = AliasEntries;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("an object of alias definitions")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<AliasEntries, A::Error> {
                let mut out = Vec::new();
                while let Some(entry) = m.next_entry::<String, Expr>()? {
                    out.push(entry);
                }
                Ok(AliasEntries(out))
            }
        }
        d.deserialize_map(V)
    }
}

impl<'de> Deserialize<'de> for Theory {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct Raw {
            interpretations: Vec<String>,
            aliases: AliasEntries,
            constraints: Vec<Constraint>,
        }
        let raw = Raw::deserialize(d)?;
        let mut aliases = BTreeMap::new();
        let mut duplicate_aliases = Vec::new();
        for (name, def) in raw.aliases.0 {
            if let Some(_previous) = aliases.insert(name.clone(), def) {
                duplicate_aliases.push(name);
            }
        }
        Ok(Theory {
            interpretations: raw.interpretations,
            aliases,
            constraints: raw.constraints,
            duplicate_aliases,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Source,
    Axiom,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    #[serde(rename = "ref")]
    pub reference: String,
    pub kind: Kind,
    pub record: String,
    pub field: String,
}

/// Resource caps from `profile.json`. Absent fields are not enforced. Unknown keys are ignored
/// (the builder's profile carries keys that only the harness reads).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Profile {
    pub max_expr_nodes: Option<u64>,
    pub max_bytes: Option<u64>,
    pub max_interpretations: Option<u64>,
    pub max_weight_bits: Option<u32>,
    /// Per-query wall clock for `mb-engine eval` and `check` (read from the submission's
    /// `profile.json`); a query over the limit answers `{"error":"timeout"}` and scores 0.
    pub query_timeout_s: Option<f64>,
    /// Expression depth cap; values above `MAX_EXPR_DEPTH` are clamped to it.
    pub max_expr_depth: Option<u64>,
    /// Largest certificate (`evidence` on a `true` partition answer) that `check` accepts, in refs.
    pub max_certificate_refs: Option<u64>,
}

/// A parsed submission directory (`bindings.jsonl`, `theory.json`, `provenance.jsonl`).
#[derive(Debug, Clone, PartialEq)]
pub struct Submission {
    pub bindings: Vec<Binding>,
    pub theory: Theory,
    pub provenance: Vec<Provenance>,
    /// Total bytes of the three artifact files, for `max_bytes`.
    pub bytes: u64,
}

fn parse_err(file: &str, msg: impl fmt::Display) -> ValidationError {
    ValidationError {
        code: "parse_error".into(),
        message: format!("{file}: {msg}"),
    }
}

/// Removes a leading UTF-8 byte order mark, if present.
pub fn strip_bom(text: &str) -> &str {
    text.strip_prefix('\u{feff}').unwrap_or(text)
}

/// Rejects text whose bracket nesting exceeds `MAX_JSON_NESTING` (strings and escapes skipped).
fn json_nesting_ok(text: &str) -> bool {
    let (mut depth, mut in_str, mut esc) = (0usize, false, false);
    for b in text.bytes() {
        if in_str {
            if esc {
                esc = false;
            } else if b == b'\\' {
                esc = true;
            } else if b == b'"' {
                in_str = false;
            }
            continue;
        }
        match b {
            b'"' => in_str = true,
            b'[' | b'{' => {
                depth += 1;
                if depth > MAX_JSON_NESTING {
                    return false;
                }
            }
            b']' | b'}' => depth = depth.saturating_sub(1),
            _ => {}
        }
    }
    true
}

/// Parses one JSON document without serde_json's 128-level limit (nesting bounded by
/// `MAX_JSON_NESTING`). A leading BOM is tolerated. Errors are plain messages.
pub fn from_json<T: DeserializeOwned>(text: &str) -> Result<T, String> {
    let text = strip_bom(text);
    if !json_nesting_ok(text) {
        return Err(format!("nesting deeper than {MAX_JSON_NESTING} levels"));
    }
    let mut de = serde_json::Deserializer::from_str(text);
    de.disable_recursion_limit();
    let v = T::deserialize(&mut de).map_err(|e| e.to_string())?;
    de.end().map_err(|e| e.to_string())?;
    Ok(v)
}

fn parse_lines<T: DeserializeOwned>(text: &str, file: &str) -> Result<Vec<T>, Vec<ValidationError>> {
    let mut out = Vec::new();
    let mut errs = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        match from_json(line) {
            Ok(v) => out.push(v),
            Err(e) => errs.push(parse_err(file, format!("line {}: {e}", i + 1))),
        }
    }
    if errs.is_empty() {
        Ok(out)
    } else {
        Err(errs)
    }
}

impl Submission {
    /// Load `bindings.jsonl`, `theory.json` and `provenance.jsonl` from `dir`.
    /// Parse failures only; semantic checks are in [`crate::validate::validate`].
    pub fn load(dir: &Path) -> Result<Submission, Vec<ValidationError>> {
        let [b, t, p] = read_texts(dir)?;
        Self::parse(&b, &t, &p)
    }

    /// As [`Submission::load`], plus the SHA-256 (lowercase hex) of each file's raw bytes, BOM included, in the
    /// order `theory.json`, `bindings.jsonl`, `provenance.jsonl`.
    pub fn load_digested(dir: &Path) -> Result<(Submission, [String; 3]), Vec<ValidationError>> {
        let [b, t, p] = read_texts(dir)?;
        // Hashing runs beside the parse, so the digests add little wall time.
        std::thread::scope(|s| {
            let [t_hash, b_hash, p_hash] = [&t, &b, &p].map(|text| s.spawn(move || sha256_hex(text.as_bytes())));
            let sub = Self::parse(&b, &t, &p)?;
            Ok((sub, [t_hash, b_hash, p_hash].map(|h| h.join().expect("digest thread"))))
        })
    }

    /// Parses the three file texts (each may carry a BOM, which is dropped here), in the order bindings, theory,
    /// provenance. The first failing file in that order is reported.
    fn parse(b: &str, t: &str, p: &str) -> Result<Submission, Vec<ValidationError>> {
        let (b, t, p) = (strip_bom(b), strip_bom(t), strip_bom(p));
        let bytes = (b.len() + t.len() + p.len()) as u64;
        let bindings = parse_lines(b, "bindings.jsonl")?;
        let theory: Theory = from_json(t).map_err(|e| vec![parse_err("theory.json", e)])?;
        let provenance = parse_lines(p, "provenance.jsonl")?;
        Ok(Submission {
            bindings,
            theory,
            provenance,
            bytes,
        })
    }
}

/// The three submission files as UTF-8 text, in the order bindings, theory, provenance. All three are read before
/// any is parsed; parsing follows the same order (see `Submission::parse`). Each read and UTF-8 check stops at the
/// first failing file in that order. The text is the raw file contents (no copy): hashing it hashes the file bytes.
fn read_texts(dir: &Path) -> Result<[String; 3], Vec<ValidationError>> {
    let read = |name: &str| -> Result<String, Vec<ValidationError>> {
        let raw = std::fs::read(dir.join(name)).map_err(|e| vec![parse_err(name, format!("cannot read: {e}"))])?;
        String::from_utf8(raw).map_err(|_| vec![parse_err(name, "cannot read: stream did not contain valid UTF-8")])
    };
    Ok([read("bindings.jsonl")?, read("theory.json")?, read("provenance.jsonl")?])
}
