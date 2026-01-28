// Compute optimal taxon list using greedy heuristic

#include <omp.h>

#include <taxonomic_tree.hpp>
#include <panic.hpp>

struct IndexScore {
    std::int64_t i;
    double s;
};

IndexScore argmin(IndexScore& a, IndexScore& b) {
    if (a.s <= b.s) {
        return a;
    } else {
        return b;
    }
}

#pragma omp declare \
    reduction(ArgMin: IndexScore: omp_out=argmin(omp_in, omp_out)) \
    initializer(omp_priv = IndexScore{-1, std::numeric_limits<double>::max()})

OptimizeOutput optimize_greedy(const Tree& tree, const Graph& agraph, const std::int64_t lsize) {
    // assume 1 <= lsize <= tree.n

    OptimizeOutput out;
    out.encode_duration = 0;
    out.decode_duration = 0;

    auto run_start = omp_get_wtime();
    auto& selected = out.nset;
    selected.insert(tree.root);

    std::vector<double> score(tree.n);
    while (std::ssize(selected) < lsize) {
        #pragma omp parallel for
        for (std::int64_t i = 0; i < tree.n; i++) {
            if (selected.contains(i)) {
                score[i] = std::numeric_limits<double>::max();
            } else {
                NodeSet candidate(selected);
                candidate.insert(i);
                score[i] = score_node_set(tree, agraph, candidate);
            }
        }

        IndexScore ismin = IndexScore{-1, std::numeric_limits<double>::max()};
        #pragma omp parallel for reduction(ArgMin: ismin)
        for (std::int64_t i = 0; i < tree.n; i++) {
            IndexScore is = IndexScore{i, score[i]};
            ismin = argmin(is, ismin);
        }

        selected.insert(ismin.i);
    }
    out.run_duration = omp_get_wtime() - run_start;

    return out;
}
