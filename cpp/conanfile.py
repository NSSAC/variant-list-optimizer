# type: ignore
from conan import ConanFile
from conan.tools.cmake import CMakeDeps, CMakeToolchain, cmake_layout, CMake


class VlrsRecipe(ConanFile):
    settings = "os", "compiler", "build_type", "arch"

    def layout(self):
        cmake_layout(self)

    def requirements(self):
        self.requires("fmt/12.1.0")
        self.requires("argparse/3.2")
        self.requires("parallel-hashmap/2.0.0")

        self.requires("random123/1.14.0")
        self.requires("hdf5/1.14.6")
        self.requires("nlohmann_json/3.12.0")

        self.requires("gurobi/13.0.0")
        self.requires("highs/1.12.0")
        self.requires("ortools/9.15.pci")

    def generate(self):
        deps = CMakeDeps(self)
        deps.generate()

        toolchain = CMakeToolchain(self)
        toolchain.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

