//! `mb-engine` CLI: `validate`, `eval`, `check` (docs/ir.md).
//!
//! Exit codes: 0 success; 1 invalid submission, profile, or inconsistent base (errors as JSON);
//! 2 usage, IO, or internal failure (message on stderr).

use mb_engine::check::{certify, check};
use mb_engine::engine::{set_deadline, Engine};
use mb_engine::world::World;
use mb_ir::sha256::{sha256_hex, Sha256};
use mb_ir::{expr_depth_cap, from_json, validate, AnswerIn, AnswerOut, Profile, Query, QueryBody, Submission, ValidationError, Verdict};
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

const USAGE: &str = "usage:
  mb-engine validate <submission_dir> [--profile profile.json]
  mb-engine eval <submission_dir> <queries.jsonl> <answers.jsonl>
  mb-engine check <gold_dir> <queries.jsonl> <answers.jsonl> <verdicts.jsonl> [--max-certificate-refs N]
exit codes: 0 ok, 1 invalid submission, profile or inconsistent base (JSON errors), 2 usage, IO or internal failure";

/// Stack of the worker thread. Parsing and recursion are sized for it (see `ir::MAX_JSON_NESTING`).
const WORKER_STACK: usize = 512 << 20;

/// Early stop of a command: a usage or IO problem (exit 2), or invalid input already reported (exit 1).
enum Stop {
    Usage(String),
    Invalid,
}

impl From<String> for Stop {
    fn from(m: String) -> Stop {
        Stop::Usage(m)
    }
}

type Res<T> = Result<T, Stop>;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // Output must never name an input. This runs before any directory or file is loaded.
    if let Err(msg) = refuse_overlaps(&args) {
        eprintln!("mb-engine: {msg}");
        return ExitCode::from(2);
    }
    let outputs = output_paths(&args);
    // Outputs are cleared before any work, so a run that dies without cleanup (stack overflow)
    // leaves no stale answers behind.
    for o in &outputs {
        if let Err(msg) = clear(o) {
            eprintln!("mb-engine: {msg}");
            return ExitCode::from(2);
        }
    }
    let worker = match std::thread::Builder::new().stack_size(WORKER_STACK).spawn(move || run(&args)) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("mb-engine: cannot start worker thread: {e}");
            remove_outputs(&outputs);
            return ExitCode::from(2);
        }
    };
    // A panic in the worker is an internal failure (exit 2).
    let code = worker.join().unwrap_or_else(|_| {
        eprintln!("mb-engine: internal failure (worker panicked)");
        2
    });
    if code != 0 {
        remove_outputs(&outputs);
    }
    ExitCode::from(code)
}

fn run(args: &[String]) -> u8 {
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.first().copied() {
        Some("validate") => cmd_validate(&args[1..]),
        Some("eval") => cmd_eval(&args[1..]),
        Some("check") => cmd_check(&args[1..]),
        _ => Err(Stop::Usage(USAGE.into())),
    };
    match result {
        Ok(()) => 0,
        Err(Stop::Invalid) => 1,
        Err(Stop::Usage(msg)) => {
            eprintln!("mb-engine: {msg}");
            2
        }
    }
}

fn json<T: serde::Serialize>(v: &T) -> Result<String, String> {
    serde_json::to_string(v).map_err(|e| e.to_string())
}

/// Output file of `eval` / `check` (empty for other commands or a wrong argument count).
fn output_paths(args: &[String]) -> Vec<String> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["eval", _, _, a] => vec![a.to_string()],
        ["check", _, _, _, v] | ["check", _, _, _, v, "--max-certificate-refs", _] => vec![v.to_string()],
        _ => vec![],
    }
}

/// Removes an output file; a file that is not there is fine.
fn clear(path: &str) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("cannot remove {path}: {e}")),
    }
}

fn remove_outputs(outputs: &[String]) {
    for o in outputs {
        if let Err(msg) = clear(o) {
            eprintln!("mb-engine: {msg}");
        }
    }
}

/// A file's identity: canonical location, plus (device, file index) so that hard links to one
/// file compare equal. A missing path is its canonical parent joined with its name.
#[derive(PartialEq)]
struct FileId {
    canon: PathBuf,
    handle: Option<(u64, u64)>,
}

fn file_id(path: &Path) -> Option<FileId> {
    let canon = match std::fs::canonicalize(path) {
        Ok(c) => c,
        Err(_) => {
            let name = path.file_name()?;
            let parent = path.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
            std::fs::canonicalize(parent).ok()?.join(name)
        }
    };
    Some(FileId {
        handle: handle_of(&canon),
        canon,
    })
}

#[cfg(unix)]
fn handle_of(p: &Path) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::metadata(p).ok()?;
    Some((m.dev(), m.ino()))
}

#[cfg(windows)]
fn handle_of(p: &Path) -> Option<(u64, u64)> {
    // BY_HANDLE_FILE_INFORMATION (winbase.h): the volume serial and file index identify a file
    // across hard links. Path-based metadata does not expose them, so the file is opened.
    #[repr(C)]
    #[derive(Default)]
    struct ByHandleInfo {
        attributes: u32,
        times: [u32; 6],
        volume: u32,
        size: [u32; 2],
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    extern "system" {
        fn GetFileInformationByHandle(handle: *mut std::ffi::c_void, info: *mut ByHandleInfo) -> i32;
    }
    use std::os::windows::io::AsRawHandle;
    let f = File::open(p).ok()?;
    let mut info = ByHandleInfo::default();
    // SAFETY: the handle is a live file handle owned by `f`; `info` is a correctly laid out out-parameter.
    let ok = unsafe { GetFileInformationByHandle(f.as_raw_handle(), &mut info) };
    (ok != 0).then(|| (u64::from(info.volume), (u64::from(info.index_high) << 32) | u64::from(info.index_low)))
}

#[cfg(not(any(unix, windows)))]
fn handle_of(_: &Path) -> Option<(u64, u64)> {
    None
}

/// Every file under `dir`, recursively. Symbolic links are listed, not followed.
fn walk_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for e in rd.flatten() {
        let p = e.path();
        match e.file_type() {
            Ok(t) if t.is_dir() => walk_files(&p, out),
            Ok(_) => out.push(p),
            Err(_) => {}
        }
    }
}

/// Refuses an output that is an input: the queries file, the answers file (check), the submission
/// or gold directory itself, or any file inside it, compared by canonical path and by file identity.
fn refuse_overlaps(args: &[String]) -> Result<(), String> {
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let (dir, inputs, outs): (&str, Vec<&str>, Vec<&str>) = match args.as_slice() {
        ["eval", d, q, a] => (*d, vec![*q], vec![*a]),
        ["check", d, q, a, v] | ["check", d, q, a, v, "--max-certificate-refs", _] => (*d, vec![*q, *a], vec![*v]),
        _ => return Ok(()),
    };
    let mut ins: Vec<FileId> = inputs.iter().filter_map(|p| file_id(Path::new(p))).collect();
    ins.extend(file_id(Path::new(dir)));
    let mut files = Vec::new();
    walk_files(Path::new(dir), &mut files);
    ins.extend(files.iter().filter_map(|p| file_id(p)));
    for o in outs {
        let Some(oid) = file_id(Path::new(o)) else {
            continue;
        };
        if ins.iter().any(|i| *i == oid || (i.handle.is_some() && i.handle == oid.handle)) {
            return Err(format!("{o} is an input file; refusing to write output over it"));
        }
    }
    Ok(())
}

/// Reports invalid input as JSON on stderr.
fn report(errs: &[ValidationError]) -> Stop {
    eprintln!("{}", json(&errs).unwrap_or_default());
    Stop::Invalid
}

/// Reports invalid gold as codes only: gold IDs, aliases and messages stay out of `check` output.
fn report_codes(errs: &[ValidationError]) -> Stop {
    let codes: Vec<serde_json::Value> = errs.iter().map(|e| serde_json::json!({ "code": e.code })).collect();
    eprintln!("{}", json(&codes).unwrap_or_default());
    Stop::Invalid
}

/// An unsatisfiable base theory invalidates the whole run (exit 1, JSON error list on stderr).
fn inconsistent_base(message: &str) -> Stop {
    report(&[ValidationError {
        code: "inconsistent_base".into(),
        message: message.into(),
    }])
}

/// Reads a profile, and its SHA-256 when `digest` is set. A parse or value error is an invalid profile (exit 1);
/// IO errors are usage (exit 2).
fn read_profile(path: &Path, digest: bool) -> Result<(Result<Profile, Vec<ValidationError>>, Option<String>), String> {
    let raw = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let hash = digest.then(|| sha256_hex(&raw));
    let text = String::from_utf8(raw).map_err(|_| format!("{}: stream did not contain valid UTF-8", path.display()))?;
    let parsed = from_json(&text).map_err(|e| {
        vec![ValidationError {
            code: "profile_invalid".into(),
            message: format!("{}: {e}", path.display()),
        }]
    });
    Ok((parsed, hash))
}

/// Digests of the files `eval` reads, as (name, lowercase hex SHA-256), in the order of the eval line.
type Digests = Vec<(&'static str, String)>;

/// Loads and validates a submission with its caps: the `--profile` file, else `<dir>/profile.json`
/// when present. Invalid content comes back as `Err(errors)`; bad arguments or IO as `Err` of the outer result.
/// With `hash` set, also returns the digests of the submission files and of `profile.json` when it is used;
/// without it nothing is hashed.
#[allow(clippy::type_complexity)]
fn load(dir: &str, profile_arg: Option<&str>, hash: bool) -> Result<Result<(Submission, Option<Profile>, Digests), Vec<ValidationError>>, String> {
    if !Path::new(dir).is_dir() {
        return Err(format!("{dir}: not a submission directory"));
    }
    let profile_path = match profile_arg {
        Some(p) => Some(PathBuf::from(p)),
        None => Some(Path::new(dir).join("profile.json")).filter(|p| p.is_file()),
    };
    let (profile, profile_dig) = match profile_path {
        None => (None, None),
        Some(p) => match read_profile(&p, hash)? {
            (Ok(pr), dig) => (Some(pr), dig),
            (Err(errs), _) => return Ok(Err(errs)),
        },
    };
    let t0 = std::time::Instant::now();
    let (sub, mut digests) = if hash {
        match Submission::load_digested(Path::new(dir)) {
            Ok((s, [t, b, p])) => (s, vec![("theory.json", t), ("bindings.jsonl", b), ("provenance.jsonl", p)]),
            Err(errs) => return Ok(Err(errs)),
        }
    } else {
        match Submission::load(Path::new(dir)) {
            Ok(s) => (s, Vec::new()),
            Err(errs) => return Ok(Err(errs)),
        }
    };
    digests.extend(profile_dig.map(|d| ("profile.json", d)));
    stage("load", t0.elapsed());
    let t0 = std::time::Instant::now();
    let errs = validate(&sub, profile.as_ref());
    stage("validate", t0.elapsed());
    Ok(if errs.is_empty() { Ok((sub, profile, digests)) } else { Err(errs) })
}

/// Under `MB_TIMING`, one stage's wall clock on stderr (`stage<TAB>name<TAB>seconds`).
fn stage(name: &str, d: Duration) {
    if std::env::var_os("MB_TIMING").is_some() {
        eprintln!("stage\t{name}\t{:.3}s", d.as_secs_f64());
    }
}

/// Per-query wall clock from the profile (absent = unlimited; validated positive and finite).
fn query_timeout(profile: Option<&Profile>) -> Option<Duration> {
    profile.and_then(|p| p.query_timeout_s).map(Duration::from_secs_f64)
}

/// Non-blank lines with their 1-based physical line numbers. With `hash` set, also the SHA-256 (hex) of the raw bytes
/// read. Invalid UTF-8 is replaced and a leading byte order mark is dropped.
fn read_lines(path: &str, hash: bool) -> Res<(Vec<(usize, String)>, Option<String>)> {
    let f = File::open(path).map_err(|e| format!("{path}: {e}"))?;
    let mut rd = BufReader::new(f);
    let mut out = Vec::new();
    let mut sha = hash.then(Sha256::new);
    let mut buf = Vec::new();
    let mut n = 0usize;
    loop {
        buf.clear();
        if rd.read_until(b'\n', &mut buf).map_err(|e| format!("{path}: {e}"))? == 0 {
            break;
        }
        if let Some(sha) = sha.as_mut() {
            sha.update(&buf);
        }
        n += 1;
        let text = String::from_utf8_lossy(&buf);
        let line = mb_ir::strip_bom(text.trim_end_matches(['\r', '\n'])).to_string();
        if !line.trim().is_empty() {
            out.push((n, line));
        }
    }
    Ok((out, sha.map(Sha256::finish_hex)))
}

/// One `queries.jsonl` line: a query, or a line that is not one (answered `bad_query` when it has a
/// recoverable `qid`; otherwise it has no answer).
enum Item {
    Query(Query),
    Bad { qid: Option<String>, line: usize },
}

fn recover_qid(text: &str) -> Option<String> {
    from_json::<serde_json::Value>(text).ok()?.get("qid")?.as_str().map(str::to_string)
}

/// Reads queries line by line. A line is bad when it does not parse or nests deeper than
/// `depth_cap`. Duplicate qids among queries make the workload invalid (exit 2); bad lines are
/// exempt from that check, since their qid is only a recovery. With `hash` set, also returns the digest of the file.
fn read_queries(path: &str, depth_cap: u64, hash: bool) -> Res<(Vec<Item>, Option<String>)> {
    let mut items = Vec::new();
    let (lines, digest) = read_lines(path, hash)?;
    for (line, text) in lines {
        items.push(match Query::from_line(&text) {
            Ok(q) if q.max_depth() <= depth_cap => Item::Query(q),
            _ => Item::Bad {
                qid: recover_qid(&text),
                line,
            },
        });
    }
    let mut seen = HashSet::new();
    for it in &items {
        if let Item::Query(q) = it {
            if !seen.insert(q.qid.as_str()) {
                return Err(Stop::Usage(format!("duplicate qid {} in {path}", q.qid)));
            }
        }
    }
    Ok((items, digest))
}

/// Reads answers for `check`. A malformed line scores its qid as `answer_error`; a qid given twice
/// is `answer_error` too (no last-wins). Lines without a recoverable qid are ignored.
fn read_answers(path: &str) -> Res<HashMap<String, AnswerIn>> {
    let mut out: HashMap<String, AnswerIn> = HashMap::new();
    for (_, line) in read_lines(path, false)?.0 {
        let Ok(v) = from_json::<serde_json::Value>(&line) else {
            continue;
        };
        let Some(qid) = v.get("qid").and_then(|q| q.as_str()).map(str::to_string) else {
            continue;
        };
        let a = if out.contains_key(&qid) {
            AnswerIn {
                qid: qid.clone(),
                error: Some("duplicate answer".into()),
                ..Default::default()
            }
        } else {
            from_json(&line).unwrap_or_else(|_| AnswerIn {
                qid: qid.clone(),
                error: Some("malformed answer".into()),
                ..Default::default()
            })
        };
        out.insert(qid, a);
    }
    Ok(out)
}

fn create(path: &str) -> Res<BufWriter<File>> {
    Ok(File::create(path).map(BufWriter::new).map_err(|e| format!("{path}: {e}"))?)
}

/// Writes through to `inner` and hashes the bytes actually written.
struct HashWriter<W: Write> {
    inner: W,
    sha: Sha256,
}

impl<W: Write> Write for HashWriter<W> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let n = self.inner.write(buf)?;
        self.sha.update(&buf[..n]);
        Ok(n)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

fn cmd_validate(args: &[&str]) -> Res<()> {
    let (dir, profile) = match args {
        [dir] => (*dir, None),
        [dir, "--profile", p] => (*dir, Some(*p)),
        _ => return Err(Stop::Usage(USAGE.into())),
    };
    let errs = match load(dir, profile, false)? {
        Ok(_) => vec![],
        Err(errs) => errs,
    };
    println!("{}", json(&errs)?);
    if errs.is_empty() {
        Ok(())
    } else {
        Err(Stop::Invalid)
    }
}

fn cmd_eval(args: &[&str]) -> Res<()> {
    let [dir, qpath, apath] = args else {
        return Err(Stop::Usage(USAGE.into()));
    };
    let (sub, profile, mut digests) = load(dir, None, true)?.map_err(|errs| report(&errs))?;
    let t0 = std::time::Instant::now();
    let (items, queries_dig) = read_queries(qpath, expr_depth_cap(profile.as_ref()), true)?;
    stage("read_queries", t0.elapsed());
    // Closure/selection answers need the per-component base classification; compute it up front.
    let needs_base = items.iter().any(|i| {
        matches!(i, Item::Query(q) if matches!(q.body, QueryBody::Closure { .. } | QueryBody::Selection { .. }))
    });
    let monolith = std::env::var_os("MB_MONOLITH").is_some();
    let t0 = std::time::Instant::now();
    let mut world = World::new(&sub);
    world.set_monolith(monolith);
    world.set_max_certificate_refs(profile.as_ref().and_then(|p| p.max_certificate_refs));
    let mut mono = if monolith { Some(Engine::new(&sub)?) } else { None };
    stage("world_new", t0.elapsed());
    let t0 = std::time::Instant::now();
    let bad = match &mut mono {
        Some(e) => e.inconsistent_interpretation()?,
        None => world.inconsistent_interpretation(needs_base)?,
    };
    stage("base", t0.elapsed());
    if let Some(name) = bad {
        return Err(inconsistent_base(&format!("base theory of interpretation {name} is unsatisfiable")));
    }
    let timeout = query_timeout(profile.as_ref());
    world.set_query_timeout(timeout);
    let mut out = HashWriter {
        inner: create(apath)?,
        sha: Sha256::new(),
    };
    let timing = std::env::var_os("MB_TIMING").is_some();
    let (mut t_answer, mut t_certify) = (Duration::ZERO, Duration::ZERO);
    let t_queries = std::time::Instant::now();
    for item in &items {
        let q = match item {
            Item::Query(q) => q,
            Item::Bad { qid: Some(qid), .. } => {
                writeln!(out, "{}", json(&AnswerOut::Error { qid: qid.clone(), error: "bad_query".into() })?)
                    .map_err(|e| e.to_string())?;
                continue;
            }
            Item::Bad { qid: None, line } => {
                eprintln!("mb-engine: line {line} is not a query and has no qid; no answer written");
                continue;
            }
        };
        let t0 = std::time::Instant::now();
        // Queries with evidence always take the scoped path (`World::answer`), never the full engine.
        // One deadline per query covers the answer and its certificate.
        set_deadline(timeout.map(|t| std::time::Instant::now() + t));
        let ans = match &mut mono {
            Some(e) if q.evidence.is_none() => e.answer(q),
            _ => world.answer_in_deadline(q),
        };
        let t_cert = std::time::Instant::now();
        let ans = certify(&mut world, q, ans);
        set_deadline(None);
        t_answer += t_cert - t0;
        t_certify += t_cert.elapsed();
        if timing {
            eprintln!("{}	{:.3}s", q.qid, t0.elapsed().as_secs_f64());
        }
        writeln!(out, "{}", json(&ans)?).map_err(|e| e.to_string())?;
    }
    out.flush().map_err(|e| e.to_string())?;
    stage("queries", t_queries.elapsed());
    stage("answer", t_answer);
    stage("certify", t_certify);
    digests.push(("queries", queries_dig.expect("eval hashes its queries")));
    digests.push(("answers", out.sha.finish_hex()));
    // One line on stdout, after the answers are flushed: SHA-256 of each input and output file as read or written.
    let files: Vec<String> = digests.iter().map(|(name, hex)| format!("\"{name}\":\"{hex}\"")).collect();
    println!("{{\"mb_engine_digests\":{{{}}},\"version\":\"{}\"}}", files.join(","), env!("CARGO_PKG_VERSION"));
    Ok(())
}

fn cmd_check(args: &[&str]) -> Res<()> {
    let (gold, qpath, apath, vpath, flag) = match args {
        [g, q, a, v] => (g, q, a, v, None),
        [g, q, a, v, "--max-certificate-refs", n] => (g, q, a, v, Some(*n)),
        _ => return Err(Stop::Usage(USAGE.into())),
    };
    // The flag overrides the gold profile's cap (the evaluator passes the pinned profile's value).
    let flag_cap = match flag {
        None => None,
        Some(n) => Some(
            n.parse::<u64>()
                .ok()
                .filter(|&m| m >= 1)
                .ok_or_else(|| Stop::Usage(format!("--max-certificate-refs must be an integer >= 1, got {n}")))?,
        ),
    };
    let (sub, profile, _) = load(gold, None, false)?.map_err(|errs| report_codes(&errs))?;
    let mut world = World::new(&sub);
    if world.inconsistent_interpretation(false)?.is_some() {
        // Detail stays out of the output: gold interpretation names are not disclosed.
        return Err(inconsistent_base("gold base theory is unsatisfiable in an interpretation"));
    }
    world.set_query_timeout(query_timeout(profile.as_ref()));
    world.set_max_certificate_refs(flag_cap.or(profile.as_ref().and_then(|p| p.max_certificate_refs)));
    let (items, _) = read_queries(qpath, expr_depth_cap(profile.as_ref()), false)?;
    let answers = read_answers(apath)?;
    let mut out = create(vpath)?;
    for item in &items {
        let v = match item {
            Item::Query(q) => check(&mut world, std::slice::from_ref(q), &answers).remove(0),
            Item::Bad { qid: Some(qid), .. } => Verdict {
                qid: qid.clone(),
                cert_valid: false,
                reason: "query_invalid".into(),
            },
            Item::Bad { qid: None, line } => {
                eprintln!("mb-engine: line {line} is not a query and has no qid; no verdict written");
                continue;
            }
        };
        writeln!(out, "{}", json(&v)?).map_err(|e| e.to_string())?;
    }
    out.flush().map_err(|e| e.to_string())?;
    Ok(())
}
