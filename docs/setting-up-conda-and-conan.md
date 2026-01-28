# Setting Up Conda and Conan on Rivanna

## Setting up Conda using Miniforge

We use Conda for managing Python dependencies.

We expect that the user of the pipeline will install Miniforge
in their home directory on every compute cluster.
Installation instructions for Miniforge can be found
[here](https://github.com/conda-forge/miniforge).

Once setup is done, please ensure that your conda config contains the following:

```sh
# ~/.condarc

channels:
- conda-forge
anaconda_upload: false
auto_activate_base: false
```

Please ensure you have the latest version of conda.

```sh
conda update -n base conda
```

## Setting up Conan

We use Conan for managing C++ dependencies.
Conan itself is Python package, which we shall use conda to install.

Modify your bash config to ensure it contains the following:

```sh
export CONAN_HOME="/project/bii_nssac/people/$USER/conan-home"

export XDG_CACHE_HOME="/scratch/$USER/var/cache"
export XDG_DATA_HOME="/scratch/$USER/var/data"
export XDG_STATE_HOME="/scratch/$USER/var/state"
export XDG_RUNTIME_DIR="/scratch/$USER/var/runtime"

PATH="$HOME/bin:$PATH"
```

Now logout and login back to Rivanna to ensure these changes have been made.

Next ensure the folders provide above exist.

```sh
mkdir -p "$CONAN_HOME"

mkdir -p "$XDG_CACHE_HOME"
mkdir -p "$XDG_DATA_HOME"
mkdir -p "$XDG_STATE_HOME"
mkdir -p "$XDG_RUNTIME_DIR"

mkdir -p "$HOME/bin"
```

Now install conan and cmake in the "base" conda environment
and create a soft links in "$HOME/bin"
so that they are always available.

```sh
conda activate base
conda install cmake
pip install -U conan
cd "$HOME/bin"
ln -s $(which conan)
ln -s $(which cmake)
```

Next inform conan of the default compiler to use when building dependencies.

```sh
module load gcc/14.2.0
conan profile detect --force
```

This should create the default profile for conan at "$CONAN_HOME/profiles/default".
Ensure that the file contains the following setting.

```yaml
[settings]
arch=x86_64
build_type=Release
compiler=gcc
compiler.cppstd=17
compiler.libcxx=libstdc++11
compiler.version=14
os=Linux
```

Conan center index is the default repositiory of dependencies.
We add PB's conan index as an additional source.

```sh
cd $HOME
git clone https://github.com/parantapa/pb-conan-index.git
conan remote add pb-conan-index $HOME/pb-conan-index
```
