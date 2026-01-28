// Compute optimal taxon list using OR-Tools CP-Sat

#include <cmath>

#include <omp.h>

#include <ortools/sat/cp_model.h>
#include <taxonomic_tree.hpp>
#include <panic.hpp>

using namespace operations_research;

OptimizeOutput optimize_cpsat(const Tree& tree, const Graph& agraph, const std::int64_t lsize, const double timelimit) {
    // assume 1 <= lsize <= tree.n

    OptimizeOutput out;

    auto encode_start = omp_get_wtime();
    sat::CpModelBuilder cp_model;

    std::vector<sat::BoolVar> selected;
    for (std::int64_t u = 0; u < tree.n; u++) {
        auto var = cp_model.NewBoolVar();
        selected.push_back(var);
    }

    std::vector<std::int64_t> indptr;
    std::vector<sat::BoolVar> u_ancestor;
    std::vector<double> u_ancestor_cost;

    indptr.push_back(0);
    for (std::int64_t u = 0; u < tree.n; u++) {
        for (const auto& [a, d] : agraph.edge_weight[u]) {
            const auto var = cp_model.NewBoolVar();
            u_ancestor.push_back(var);
            double cost = std::exp(tree.node_weight[u]) * std::exp(d);
            u_ancestor_cost.push_back(cost);

            cp_model.AddImplication(var, selected[a]);
        }
        indptr.push_back(u_ancestor.size());

        std::int64_t offset = indptr[indptr.size() - 2];
        std::int64_t count = indptr[indptr.size() - 1] - offset;
        cp_model.AddExactlyOne(std::span(u_ancestor.data() + offset, count));
    }
    cp_model.AddEquality(sat::LinearExpr::Sum(selected), lsize);
    cp_model.Minimize(sat::DoubleLinearExpr::WeightedSum(u_ancestor, u_ancestor_cost));

    sat::SatParameters parameters;
    parameters.set_cp_model_presolve(true);
    parameters.set_max_time_in_seconds(timelimit);
    parameters.set_log_search_progress(true);
    out.encode_duration = omp_get_wtime() - encode_start;

    auto run_start = omp_get_wtime();
    const auto response = sat::SolveWithParameters(cp_model.Build(), parameters);
    out.run_duration = omp_get_wtime() - run_start;
    fmt::println("response.status() == {}", sat::CpSolverStatus_Name(response.status()));

    if (response.status() != sat::CpSolverStatus::OPTIMAL && response.status() != sat::CpSolverStatus::FEASIBLE) {
        panic("Model not feasible.");
    }

    auto decode_start = omp_get_wtime();
    for (std::int64_t u = 0; u < tree.n; u++) {
        if (sat::SolutionBooleanValue(response, selected[u])) {
            out.nset.insert(u);
        }
    }
    out.decode_duration = omp_get_wtime() - decode_start;

    return out;
}
