// Compute optimal taxon list using HiGHS

#include <cmath>

#include <omp.h>

#include <highs_helper.hpp>
#include <taxonomic_tree.hpp>
#include <panic.hpp>

OptimizeOutput optimize_highs(const Tree& tree, const Graph& agraph, const std::int64_t lsize, const double timelimit) {
    // assume 1 <= lsize <= tree.n

    OptimizeOutput out;

    auto encode_start = omp_get_wtime();
    highs_helper::HighsModelBuilder builder;

    // Variables we keep track of
    std::vector<HighsInt> selected_v = builder.add_vars(tree.n, 0.0, 1.0, true);

    // Constraint expressions
    HighsInt selection_c = builder.add_constr();
    std::vector<HighsInt> u_ancestor_c = builder.add_constrs(tree.n);

    for (std::int64_t u = 0; u < tree.n; u++) {
        builder.var_constr_coeff[selected_v[u]][selection_c] = 1.0;
    }

    for (std::int64_t u = 0; u < tree.n; u++) {
        for (const auto& [a, d] : agraph.edge_weight[u]) {
            HighsInt ua_var = builder.add_var(0.0, 1.0, true);
            builder.var_constr_coeff[ua_var][u_ancestor_c[u]] = 1.0;

            HighsInt constr = builder.add_constr();
            builder.var_constr_coeff[ua_var][constr] = 1.0;
            builder.var_constr_coeff[selected_v[a]][constr] = -1.0;
            builder.constr_upper_bound[constr] = 0.0;

            builder.obj_coeff[ua_var] = std::exp(tree.node_weight[u]) * std::exp(d);
        }
        builder.constr_lower_bound[u_ancestor_c[u]] = 1;
        builder.constr_upper_bound[u_ancestor_c[u]] = 1;
    }
    builder.constr_upper_bound[selection_c] = lsize;
    builder.constr_lower_bound[selection_c] = lsize;

    HighsModel model = builder.make_highs_model(true);
    HighsOptions options;
    options.time_limit = timelimit; // seconds
    options.log_dev_level = 2;      // verbose
    options.presolve = "on";

    Highs highs;
    if (highs.passModel(model) != HighsStatus::kOk) {
        panic("Failed to pass model");
    }
    if (highs.passOptions(options) != HighsStatus::kOk) {
        panic("Failed to pass options");
    }
    out.encode_duration = omp_get_wtime() - encode_start;

    auto run_start = omp_get_wtime();
    highs.run();
    out.run_duration = omp_get_wtime() - run_start;

    auto decode_start = omp_get_wtime();
    const HighsModelStatus& model_status = highs.getModelStatus();
    const HighsInfo& info = highs.getInfo();

    if (model_status == HighsModelStatus::kInfeasible) {
        panic("Model is not feasible");
    } else if (info.primal_solution_status != SolutionStatus::kSolutionStatusFeasible) {
        panic("Primal solution not feasible");
    }

    const HighsSolution& solution = highs.getSolution();

    for (HighsInt x : selected_v) {
        if (solution.col_value[x]) {
            out.nset.insert(x);
        }
    }
    out.decode_duration = omp_get_wtime() - decode_start;

    return out;
}
