# Variant List Optimizer

## Setting up Conda and Conan

We use Conda for managing Python dependencies
and Conan for managing C++ dependencies.
Please use [this link](docs/setting-up-conda-and-conan.md) to set up Conda and Conan on Rivanna.

## Installing variant-list-optimizer on Rivanna

Allocate a new compute node to do the setup.

```sh
srun -A bii_nssac -p bii --nodes=1 --ntasks-per-node=1 --cpus-per-task=40 --mem=0 -W 0 -t 1-00:00:00 --pty /bin/bash
```

Checkout the variant-list-optimizer code and run the setup script.

```sh
# Checkout variant-list-optimizer and install it
# along with rest of the dependencies with pip.
cd $HOME
git clone https://github.com/NSSAC/variant-list-optimizer.git
cd variant-list-optimizer
./scripts/rivanna.sh setup
```

## Running the test script

```sh
./scripts/rivanna.sh test
```
