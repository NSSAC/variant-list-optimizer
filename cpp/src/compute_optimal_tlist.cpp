// Compute optimal taxon list

#include <fstream>
#include <omp.h>

#include <fmt/base.h>
#include <argparse/argparse.hpp>
#include <nlohmann/json.hpp>

#include <panic.hpp>
#include <taxonomic_tree.hpp>

using json = nlohmann::json;

OptimizeOutput optimize_greedy(const Tree& tree, const Graph& agraph, const std::int64_t lsize);
OptimizeOutput optimize_approx(const Tree& tree, const Graph& agraph, const double cost_perc);

OptimizeOutput optimize_gurobi(const Tree& tree, const Graph& agraph, const std::int64_t lsize, const double timelimit);
OptimizeOutput optimize_highs(const Tree& tree, const Graph& agraph, const std::int64_t lsize, const double timelimit);
OptimizeOutput optimize_cpsat(const Tree& tree, const Graph& agraph, const std::int64_t lsize, const double timelimit);

int main(int argc, char* argv[]) {
    argparse::ArgumentParser program(argv[0]);
    program.add_description("Compute optimal taxon list.");

    std::int64_t sample = 0;
    std::int64_t lsize = 0;
    double cost_perc = 0.0;
    std::string input_file = "";
    std::string output_file = "";
    std::string method = "";
    double timelimit = 0.0;

    // clang-format off
    program.add_argument("-i", "--input-file")
	.required()
	.help("Input file name (HDF5).")
	.store_into(input_file);

    program.add_argument("-s", "--sample")
	.required()
	.help("Tree sample to optimize for.")
	.store_into(sample);

    program.add_argument("-n", "--size")
	.required()
	.help("Taxon list size.")
	.store_into(lsize);

    program.add_argument("-p", "--cost-threshold-percentile")
	.help("Cost threshold percentille.")
	.default_value(50.0)
	.store_into(cost_perc);

    program.add_argument("-o", "--output-file")
	.required()
	.help("Output file name (JSON).")
	.store_into(output_file);

    program.add_argument("-m", "--method")
	.required()
	.help("Optimization method.")
	.choices("greedy", "gurobi", "highs", "cpsat", "approx")
	.store_into(method);

    program.add_argument("-t", "--timelimit")
	.help("Timelimit for optimziers (in seconds).")
	.default_value(300.0)
	.store_into(timelimit);

    // clang-format on

    try {
        program.parse_args(argc, argv);
    } catch (const std::exception& err) {
        std::cerr << err.what() << std::endl;
        std::cerr << program;
        std::exit(1);
    }

    fmt::println("input_file = {}", input_file);
    fmt::println("sample = {}", sample);
    fmt::println("lsize = {}", lsize);
    fmt::println("cost_perc = {}", cost_perc);
    fmt::println("output_file = {}", output_file);
    fmt::println("method = {}", method);
    fmt::println("timelimit = {}", timelimit);

    H5::H5File ifile = H5::H5File(input_file, H5F_ACC_RDONLY);
    Tree tree = load_tree(ifile, sample);
    Graph agraph = make_ancestor_graph(tree);

    if (lsize < 1) {
        panic("lsize < 1; lsize = {}", lsize);
    }
    if (lsize > tree.n) {
        panic("lsize > tree.n; lsize = {}, tree.n = {}", lsize, tree.n);
    }

    {
        json output;
        output["input_file"] = input_file;
        output["sample"] = sample;
        output["lsize"] = lsize;
        output["cost_perc"] = cost_perc;
        output["method"] = method;
        output["timelimit"] = timelimit;
        output["state"] = "running";

        std::ofstream ofile(output_file, std::ios::out | std::ios::trunc);
        ofile << output << "\n";
    }

    OptimizeOutput result;
    if (method == "greedy") {
        result = optimize_greedy(tree, agraph, lsize);
    } else if (method == "gurobi") {
        result = optimize_gurobi(tree, agraph, lsize, timelimit);
    } else if (method == "highs") {
        result = optimize_highs(tree, agraph, lsize, timelimit);
    } else if (method == "cpsat") {
        result = optimize_cpsat(tree, agraph, lsize, timelimit);
    } else if (method == "approx") {
        result = optimize_approx(tree, agraph, cost_perc);
    } else {
        panic("Unexpected method = {}", method);
    }

    double score = score_node_set(tree, agraph, result.nset);
    fmt::println("selected.size() = {}", result.nset.size());
    fmt::println("score = {}", score);

    {
        json output;
        output["input_file"] = input_file;
        output["sample"] = sample;
        output["lsize"] = lsize;
        output["cost_perc"] = cost_perc;
        output["method"] = method;
        output["timelimit"] = timelimit;
        output["state"] = "complete";

        output["selected"] = result.nset;
        output["score"] = score;
        output["encode_duration_s"] = result.encode_duration;
        output["run_duration_s"] = result.run_duration;
        output["decode_duration_s"] = result.decode_duration;

        std::ofstream ofile(output_file, std::ios::out | std::ios::trunc);
        ofile << output << "\n";
    }

    return 0;
}
