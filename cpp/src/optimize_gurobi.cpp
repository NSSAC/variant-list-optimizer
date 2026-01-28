// Compute optimal taxon list using Gurobi

#include <omp.h>

#include <gurobi_c++.h>
#include <taxonomic_tree.hpp>
#include <panic.hpp>

OptimizeOutput optimize_gurobi(const Tree& tree, const Graph& agraph, std::int64_t lsize, double timelimit) {
    // assume 1 <= lsize <= tree.n

    OptimizeOutput out;

    auto encode_start = omp_get_wtime();
    GRBEnv env = GRBEnv(true);
    env.start();

    GRBModel model = GRBModel(env);

    // Variables we keep track of
    std::vector<GRBVar> selected(tree.n);

    // Constraint expressions
    GRBLinExpr selection;
    std::vector<GRBLinExpr> u_ancestor(tree.n);

    // Objective expression
    GRBLinExpr penalty;

    for (std::int64_t u = 0; u < tree.n; u++) {
        selected[u] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY);
        selection += selected[u];
    }

    for (std::int64_t u = 0; u < tree.n; u++) {
        for (const auto& [a, d] : agraph.edge_weight[u]) {
            GRBVar ua_var = model.addVar(0.0, 1.0, 0.0, GRB_BINARY);
            u_ancestor[u] += ua_var;

            model.addConstr(ua_var <= selected[a]);
            penalty += ua_var * std::exp(tree.node_weight[u]) * std::exp(d);
        }
        model.addConstr(u_ancestor[u] == 1);
    }
    model.addConstr(selection == lsize);

    model.setObjective(penalty, GRB_MINIMIZE);
    model.set(GRB_DoubleParam_TimeLimit, timelimit);
    out.encode_duration = omp_get_wtime() - encode_start;

    auto run_start = omp_get_wtime();
    model.optimize();
    out.run_duration = omp_get_wtime() - run_start;

    auto decode_start = omp_get_wtime();
    for (std::int64_t u = 0; u < tree.n; u++) {
        if (selected[u].get(GRB_DoubleAttr_X)) {
            out.nset.insert(u);
        }
    }
    out.decode_duration = omp_get_wtime() - decode_start;

    return out;
}
