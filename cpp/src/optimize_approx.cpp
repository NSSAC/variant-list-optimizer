// Compute optimal taxon list using bicriteria approximation

#include <omp.h>

#include <algorithm>
#include <taxonomic_tree.hpp>
#include <panic.hpp>

using Set = phmap::parallel_flat_hash_set<std::int64_t>;
using Collection = std::vector<Set>;

struct IndexCount {
    std::int64_t i;
    std::int64_t c;
};

IndexCount argmax(IndexCount& a, IndexCount& b) {
    if (a.c >= b.c) {
        return a;
    } else {
        return b;
    }
}

#pragma omp declare \
    reduction(ArgMax: IndexCount: omp_out=argmax(omp_in, omp_out)) \
    initializer(omp_priv = IndexCount{-1, std::numeric_limits<int>::min()})

OptimizeOutput optimize_approx(const Tree& tree, const Graph& agraph, const double cost_perc) {
    // assume 1 <= lsize <= tree.n

    OptimizeOutput out;
    out.encode_duration = 0;
    out.decode_duration = 0;

    auto run_start = omp_get_wtime();
    std::vector<double> costs;
    for (std::int64_t u = 0; u < tree.n; u++) {
        for (const auto& [a, d] : agraph.edge_weight[u]) {
            auto cost = std::exp(tree.node_weight[u]) * std::exp(d);
            costs.push_back(cost);
        }
    }
    std::sort(costs.begin(), costs.end());
    double cost_thres = costs[int(cost_perc * costs.size() / 100.0)];

    Collection coll(tree.n);
    Set elems;
    Set cover;

    for (std::int64_t u = 0; u < tree.n; u++) {
        for (const auto& [a, d] : agraph.edge_weight[u]) {
            auto cost = std::exp(tree.node_weight[u]) * std::exp(d);
            if (cost <= cost_thres) {
                coll[a].insert(u);
            }
        }
        elems.insert(u);
    }

    // special case for root
    {

        // add i to cover
        cover.insert(tree.root);

        // remove covered elements
        for (const auto elem : coll[tree.root]) {
            auto it = elems.find(elem);
            elems.erase(it);
        }

        // Keep only the uncovered items in each colleciton
        #pragma omp parallel for
        for (std::int64_t i = 0; i < std::ssize(coll); i++) {
            Set nset;
            for (const auto elem : coll[i]) {
                if (elems.contains(elem)) {
                    nset.insert(elem);
                }
            }
            coll[i] = std::move(nset);
        }
    }

    while (!elems.empty()) {
        // select set with maximum cardinality
        IndexCount amax{-1, std::numeric_limits<int>::min()};
        #pragma omp parallel for reduction(ArgMax: amax)
        for (std::int64_t i = 0; i < std::ssize(coll); i++) {
            if (!cover.contains(i)) {
                IndexCount a{i, std::ssize(coll[i])};
                amax = argmax(a, amax);
            }
        }

        // add i to cover
        cover.insert(amax.i);

        // remove covered elements
        for (const auto elem : coll[amax.i]) {
            auto it = elems.find(elem);
            elems.erase(it);
        }

        // Keep only the uncovered items in each colleciton
        #pragma omp parallel for
        for (std::int64_t i = 0; i < std::ssize(coll); i++) {
            Set nset;
            for (const auto elem : coll[i]) {
                if (elems.contains(elem)) {
                    nset.insert(elem);
                }
            }
            coll[i] = std::move(nset);
        }
    }
    out.nset.insert(cover.begin(), cover.end());
    out.run_duration = omp_get_wtime() - run_start;

    return out;
}
