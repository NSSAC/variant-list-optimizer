#pragma once

#include <cstdint>
#include <vector>
#include <utility>

#include <fmt/format.h>
#include <parallel_hashmap/phmap.h>

#include <H5Cpp.h>

struct Tree {
    std::int64_t n;
    std::int64_t root;
    std::vector<std::int64_t> parent;
    std::vector<double> node_weight;
    std::vector<double> edge_weight;

    explicit Tree(std::int64_t n_);

    void print() const;
};

struct Graph {
    std::int64_t n;
    std::vector<phmap::flat_hash_map<std::int64_t, double>> edge_weight;

    explicit Graph(std::int64_t n_);
};

using NodeSet = phmap::flat_hash_set<std::int64_t>;

void save_tree(const Tree& tree, std::int64_t sample, H5::H5File& file);
Tree load_tree(H5::H5File& file, std::int64_t sample);

Graph make_ancestor_graph(const Tree& tree);
Graph make_decendent_graph(const Graph& agraph);

double max_distance(const Graph& agraph);

std::pair<std::int64_t, double> nearest_included_ancestor(const Graph& agraph, const NodeSet& nset,
                                                          const std::int64_t u);

double score_node_set(const Tree& tree, const Graph& agraph, const NodeSet& nset);

struct OptimizeOutput {
    NodeSet nset;

    double encode_duration;
    double run_duration;
    double decode_duration;
};
