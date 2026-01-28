cmake_minimum_required(VERSION 4.0)

project(
  tprs-benchmark
  VERSION 0.0.1
  LANGUAGES CXX)

find_package(OpenMP REQUIRED)

find_package(fmt REQUIRED)
find_package(argparse REQUIRED)
find_package(phmap REQUIRED)

find_package(random123 REQUIRED)
find_package(HDF5 REQUIRED)
find_package(nlohmann_json REQUIRED)

find_package(gurobi REQUIRED)
find_package(highs REQUIRED)
find_package(ortools REQUIRED)

add_library(_tprs src/taxonomic_tree.cpp)
target_sources(_tprs
PUBLIC
    FILE_SET HEADERS
    BASE_DIRS include
)
target_link_libraries(_tprs
PUBLIC
    OpenMP::OpenMP_CXX
    fmt::fmt
    argparse::argparse
    phmap

    random123::random123
    hdf5::hdf5
    hdf5::hdf5_cpp
    nlohmann_json::nlohmann_json
)
target_compile_features(_tprs
PUBLIC
    cxx_std_23
)

add_executable(compute-optimal-tlist
    src/compute_optimal_tlist.cpp
    src/optimize_greedy.cpp
    src/optimize_gurobi.cpp
    src/optimize_highs.cpp
    src/optimize_cpsat.cpp
    src/optimize_approx.cpp
    src/highs_helper.cpp
)
target_link_libraries(compute-optimal-tlist
PRIVATE 
    gurobi::gurobi
    highs::highs
    ortools::ortools
    _tprs)
