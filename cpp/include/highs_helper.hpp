#pragma once

#include <Highs.h>
#include <parallel_hashmap/phmap.h>

namespace highs_helper {

constexpr double default_lower_bound = -1.0e20;
constexpr double default_upper_bound = 1.0e20;

using SparseMatrix = phmap::parallel_flat_hash_map<HighsInt, phmap::flat_hash_map<HighsInt, double>>;

struct HighsModelBuilder {
    HighsInt n_vars;
    HighsInt n_constrs;

    std::vector<double> obj_coeff;

    std::vector<HighsVarType> var_type;
    std::vector<double> var_lower_bound;
    std::vector<double> var_upper_bound;

    std::vector<double> constr_lower_bound;
    std::vector<double> constr_upper_bound;

    SparseMatrix var_constr_coeff;

    HighsModelBuilder();

    HighsInt add_var(double lb = default_lower_bound, double ub = default_upper_bound, bool is_int = false);
    std::vector<HighsInt> add_vars(HighsInt n, double lb = default_lower_bound, double ub = default_upper_bound,
                                   bool is_int = false);

    HighsInt add_constr(double lb = default_lower_bound, double ub = default_upper_bound);
    std::vector<HighsInt> add_constrs(HighsInt n, double lb = default_lower_bound, double ub = default_upper_bound);

    HighsModel make_highs_model(bool minimize = true);
};

} // namespace highs_helper
