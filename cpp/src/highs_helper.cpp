#include <compare>
#include <algorithm>

#include <highs_helper.hpp>

using namespace highs_helper;

HighsModelBuilder::HighsModelBuilder()
    : n_vars(0)
    , n_constrs(0) {}

HighsInt HighsModelBuilder::add_var(double lb, double ub, bool is_int) {
    n_vars++;

    var_lower_bound.push_back(lb);
    var_upper_bound.push_back(ub);
    if (is_int) {
        var_type.push_back(HighsVarType::kInteger);
    } else {
        var_type.push_back(HighsVarType::kContinuous);
    }
    obj_coeff.push_back(0.0);

    return n_vars - 1;
}

std::vector<HighsInt> HighsModelBuilder::add_vars(HighsInt n, double lb, double ub, bool is_int) {
    std::vector<HighsInt> vars;

    for (HighsInt i = 0; i < n; i++) {
        vars.push_back(add_var(lb, ub, is_int));
    }

    return vars;
}

HighsInt HighsModelBuilder::add_constr(double lb, double ub) {
    n_constrs++;

    constr_lower_bound.push_back(lb);
    constr_upper_bound.push_back(ub);

    return n_constrs - 1;
}

std::vector<HighsInt> HighsModelBuilder::add_constrs(HighsInt n, double lb, double ub) {
    std::vector<HighsInt> constrs;

    for (HighsInt i = 0; i < n; i++) {
        constrs.push_back(add_constr(lb, ub));
    }

    return constrs;
}

struct ConstrCoeff {
    HighsInt c;
    double d;

    std::strong_ordering operator<=>(const ConstrCoeff& other) {
        if (c < other.c) {
            return std::strong_ordering::less;
        } else if (c > other.c) {
            return std::strong_ordering::greater;
        } else {
            return std::strong_ordering::equal;
        }
    }
};

HighsModel HighsModelBuilder::make_highs_model(bool minimize) {
    HighsModel model;

    model.lp_.num_col_ = n_vars;
    model.lp_.num_row_ = n_constrs;

    model.lp_.sense_ = minimize ? ObjSense::kMinimize : ObjSense::kMaximize;
    model.lp_.offset_ = 0;

    model.lp_.col_cost_ = std::move(obj_coeff);
    model.lp_.col_lower_ = std::move(var_lower_bound);
    model.lp_.col_upper_ = std::move(var_upper_bound);
    model.lp_.integrality_ = std::move(var_type);

    model.lp_.row_lower_ = std::move(constr_lower_bound);
    model.lp_.row_upper_ = std::move(constr_upper_bound);

    model.lp_.a_matrix_.format_ = MatrixFormat::kColwise;

    std::vector<HighsInt>& start = model.lp_.a_matrix_.start_;
    std::vector<HighsInt>& index = model.lp_.a_matrix_.index_;
    std::vector<double>& value = model.lp_.a_matrix_.value_;

    for (std::int64_t v = 0; v < n_vars; v++) {
        std::vector<ConstrCoeff> constr_coeff;
        for (const auto& [c, d] : var_constr_coeff[v]) {
            constr_coeff.emplace_back(c, d);
        }

        std::sort(constr_coeff.begin(), constr_coeff.end());

        for (const auto& [c, d] : constr_coeff) {
            index.push_back(c);
            value.push_back(d);
        }

        start.push_back(index.size());
    }

    n_vars = 0;
    n_constrs = 0;

    obj_coeff.clear();
    var_type.clear();
    var_lower_bound.clear();
    var_upper_bound.clear();

    constr_lower_bound.clear();
    constr_upper_bound.clear();
    var_constr_coeff.clear();

    return model;
}
