# venvjail

Create Python virtualenvs (venv) in OBS.

This tool helps with the creation and management of Python virtual
environments in OBS.  Is initially designed to deal with the creation
of venvs for OpenStack services, but can be generalized for other
cases.

## Basic usage

To build a venv in OBS we can use the sub-command `create`.  This
command will expect a place where all the RPMs are stored.  By default
OBS will generate a directory where all the RPMs that belong the
dependency graph closure are stored.  You can change this directory
with the `--repo` parameter.

The command `create` will create a venv and store the RPM contents
inside the venv directory.  To decide the RPM that belong to the venv,
two new files are used: `include-rpm` and `exclude-rpm`.

Both files are using Python regular expressions to indicate name of
packages that will be included or excluded from the venv.  The file
`include-rpm` will list the RPM package names that can be included in
the venv, and `exclude-rpm` will list the names of the packages that
we do not expect for find in the venv.  `venvjail` will iterate over
the list of packages in the repository, and if the package is in
`include-rpm` but not in `exclude-rpm`, will be installed inside the
venv directory.

We can use both files to separate the packages that belongs to the
operating system, from the one that belong to the venv.  So for
example, if a package that comes from the operating system is not
listed in `include-rpm`, or is explicitly listed in `exclude-rpm`, it
will not be installed in the venv.  Because by default the venv is
build with a system-site package access, a `zypper up` will update the
package from outside the venv and a restart of the service that live
inside the venv will see now the new version of the package.

This venv is later reallocated to an specific directory (parameter
`--relocate`).  This will fix the Python shebangs from the binaries,
the venv activators and the systemd services.

## Automatic generation of the files

Both files `include-rpm` and `exclude-rpm` can be automatically
regenerated from `venvjail`.  By default the program will check what
packages belong to a repository in OBS, and this list will be
considered to build `include-rpm`.  So all the required packages that
are living in a different repository will not be installed in the
venv.

By default `exclude-rpm` is generated to explicitly exclude the
Python2 and Python3 binaries, devel, test and doc packages.  We can
extend this list manually (or adjust the source code to generate a
more informed list of packages)

# Automatic maintenance of the spec file

A spec file require to list the `BuildRequires` and the Requires of
the packages.  The script can help us via the `binary` and `requires`
commands.  The first one will list the binary packages from a source
package.  If we add this list as a `BuildRequires` in the spec file,
OBS will resolve all the dependencies that will build the minimal
graph of packages that are needed for the creation of the venv.

Because there are packages that are needed in the venv, but are living
outside it (because are excluded or not included in the initial list
of RPMs), in order to install the new venv, those packages needs to be
co-installed together.  The command `requres` can help us to make this
list.  This command will download the source spec file from the
package what we want to build the venv for, and get the list of
requirements.  Later will get the sublist of packages that are missing
form the venv.
