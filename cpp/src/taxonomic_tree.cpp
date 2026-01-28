#include <cstdint>
#include <fmt/base.h>
#include <limits>

#include <utility>
#include <vector>

#include <fmt/ranges.h>

#include <taxonomic_tree.hpp>
#include <panic.hpp>
#include <hdf5_utils.hpp>

using namespace hpc_utils;

Tree::Tree(std::int64_t n_)
    : n(n_)
    , root(-1)
    , parent(n_)
    , node_weight(n_)
    , edge_weight(n_) {}

void Tree::print() const {
    fmt::println("n={}", n);
    fmt::println("root={}", root);
    for (std::int64_t u = 0; u < n; u++) {
        fmt::println("u={}, parent={}, node_weight={}, edge_weight={}", u, parent[u], node_weight[u], edge_weight[u]);
    }
}

Graph::Graph(std::int64_t n_)
    : n(n_)
    , edge_weight(n) {}

void save_tree(const Tree& tree, std::int64_t sample, H5::H5File& file) {
    auto group = file.createGroup(fmt::format("{}", sample));

    write_attribute(tree.n, group, "n");
    write_attribute(tree.root, group, "root");
    write_dataset(tree.parent, group, "parent");
    write_dataset(tree.node_weight, group, "node_weight");
    write_dataset(tree.edge_weight, group, "edge_weight");
}

Tree load_tree(H5::H5File& file, std::int64_t sample) {
    auto group = file.openGroup(fmt::format("{}", sample));

    std::int64_t n = read_attribute<std::int64_t>(group, "n");
    Tree tree(n);

    tree.root = read_attribute<std::int64_t>(group, "root");
    read_dataset(group, "parent", tree.parent);
    read_dataset(group, "node_weight", tree.node_weight);
    read_dataset(group, "edge_weight", tree.edge_weight);

    return tree;
}

void do_collect_ancestors(std::int64_t v, std::int64_t a, double weight, Graph& agraph, const Tree& tree) {
    if (tree.parent[a] != -1) {
        weight += tree.edge_weight[a];
        agraph.edge_weight[v][tree.parent[a]] = weight;
        do_collect_ancestors(v, tree.parent[a], weight, agraph, tree);
    }
}

Graph make_ancestor_graph(const Tree& tree) {
    Graph agraph(tree.n);

    for (std::int64_t v = 0; v < tree.n; v++) {
        agraph.edge_weight[v][v] = 0.0;
        do_collect_ancestors(v, v, 0.0, agraph, tree);
    }

    return agraph;
}

Graph make_decendent_graph(const Graph& agraph) {
    Graph dgraph(agraph.n);

    for (std::int64_t v = 0; v < agraph.n; v++) {
        for (const auto& [a, d] : agraph.edge_weight[v]) {
            dgraph.edge_weight[a][v] = d;
        }
    }

    return dgraph;
}

double max_distance(const Graph& agraph) {
    double distance = std::numeric_limits<double>::lowest();

    for (const auto& vds : agraph.edge_weight) {
        for (const auto& [_, d] : vds) {
            distance = std::max<double>(distance, d);
        }
    }

    return distance;
}

std::pair<std::int64_t, double> nearest_included_ancestor(const Graph& agraph, const NodeSet& nset,
                                                          const std::int64_t u) {
    std::int64_t a = -1;
    double distance = std::numeric_limits<double>::max();

    for (const auto& [v, d] : agraph.edge_weight[u]) {
        if (nset.contains(v) && d < distance) {
            a = v;
            distance = d;
        }
    }

    return std::make_pair(a, distance);
}

double score_node_set(const Tree& tree, const Graph& agraph, const NodeSet& nset) {
    double score = 0;

    for (std::int64_t u = 0; u < agraph.n; u++) {
        const auto& [a, d] = nearest_included_ancestor(agraph, nset, u);
        if (a == -1) [[unlikely]] {
            // tree.print();
            // panic("Ancestor not found; u={}\nnset={}", u, nset);
            panic("Ancestor not found; u={}", u);
        } else {
            score += std::exp(tree.node_weight[u]) * std::exp(d);
        }
    }

    return score;
}
