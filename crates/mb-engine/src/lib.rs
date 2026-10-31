//! SAT-backed evaluation of Mentat Bench queries over a submission (see docs/ir.md).

pub mod check;
pub mod engine;
pub mod families;
pub mod world;

#[cfg(test)]
mod tests;
