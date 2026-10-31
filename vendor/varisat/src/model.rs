//! Global model reconstruction

use partial_ref::{partial, PartialRef};

use varisat_formula::Lit;
use varisat_internal_proof::ProofStep;

use crate::{
    context::{parts::*, Context},
    proof,
    state::SatState,
};

/// Global model reconstruction
#[derive(Default)]
pub struct Model {
    /// Assignment of the global model.
    ///
    /// Whenever the solver state is SAT this must be up to date.
    assignment: Vec<Option<bool>>,
}

impl Model {
    /// Assignment of the global model.
    ///
    /// Only valid if the solver state is SAT.
    pub fn assignment(&self) -> &[Option<bool>] {
        &self.assignment
    }

    /// Whether a given global literal is true in the model assignment.
    ///
    /// Only valid if the solver state is SAT.
    pub fn lit_is_true(&self, global_lit: Lit) -> bool {
        self.assignment[global_lit.index()] == Some(global_lit.is_positive())
    }
}

pub fn reconstruct_global_model<'a>(
    mut ctx: partial!(
        Context<'a>,
        mut ModelP,
        mut ProofP<'a>,
        mut SolverStateP,
        mut TmpDataP,
        AssignmentP,
        VariablesP
    ),
) {
    let (variables, mut ctx) = ctx.split_part(VariablesP);
    let (_model, mut ctx) = ctx.split_part_mut(ModelP);
    let (tmp, mut ctx) = ctx.split_part_mut(TmpDataP);

    let models_in_proof = ctx.part(ProofP).models_in_proof();

    // mentat-bench patch: the global model is no longer materialised (O(nvars) per SAT answer);
    // `Solver::model_value` reads the live assignment instead. Only proofs need the full list.
    if models_in_proof {
        tmp.lits.clear();
        for global_var in variables.global_var_iter() {
            let value = if let Some(solver_var) = variables.solver_from_global().get(global_var) {
                ctx.part(AssignmentP).var_value(solver_var)
            } else {
                Some(variables.var_data_global(global_var).unit.unwrap_or(false))
            };
            if let Some(value) = value {
                tmp.lits.push(global_var.lit(value))
            }
        }
        proof::add_step(
            ctx.borrow(),
            false,
            &ProofStep::Model {
                assignment: &tmp.lits,
            },
        );
    }
    ctx.part_mut(SolverStateP).sat_state = SatState::Sat;
}
