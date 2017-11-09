#!/usr/bin/env python3

# Copyright (c) 2017 SUSE LINUX GmbH, Nuernberg, Germany.
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via http://bugs.opensuse.org/
#

# Script to help the creation of a virtual environment based of binary
# and Python RPMs.  Used to jail OpenStack services.

import argparse
import glob
import os
import os.path
import re
import subprocess
import xml.etree.ElementTree as ET


# Sane default for excludeirpm file
EXCLUDE_RPM = r"""# List of packages to ignore (use Python regex)

# Note that `exclude` takes precedence over `include`.  So if a
# package match both constrains, it will be excluded.

# Exclude irrelevan sub-packages
.*-debuginfo
.*-debugsource
.*-devel
.*-devel-.*
.*-doc
.*-docs
.*-test

# Python base breaks the venv
pyhon-base
pyhon3-base

# Exclude all Python 3 packages
python3.*
"""


class FileList():
    """File list with comments and regular expressions."""
    def __init__(self, filename):
        try:
            self.items = [
                re.compile(line.strip()) for line in open(filename)
                if line.strip() and not line.strip().startswith('#')
            ]
        except IOError:
            self.items = []

    def is_populated(self):
        return self.items

    def contains(self, item):
        return any(i.match(item) for i in self.items)

    def __contains__(self, item):
        return self.contains(item)


def _replace(filename, original, line):
    """Replace a line in a file using regular expressions."""
    lines = re.sub(original, line, open(filename).read())
    open(filename, 'w').writelines(lines)


def _insert(filename, after, line):
    """Insert a line after the `after` line."""
    lines = open(filename).readlines()
    # If the line is not found, will produce a ValueError exception
    # and will end the script.  We do not want to capture the
    # exception, so we can see in OBS that the precondition was not
    # meet
    index = lines.index(after + '\n')
    lines.insert(index + 1, line + '\n')
    open(filename, 'w').writelines(lines)


def _fix_virtualenv(dest_dir, relocated):
    """Fix virtualenv activators."""
    # New path where the venv will live at the end
    virtual_env = os.path.join(relocated, dest_dir)

    _fix_alternatives(dest_dir, relocated)
    _fix_relocation(dest_dir, virtual_env)
    _fix_activators(dest_dir, virtual_env)
    _fix_systemd_services(dest_dir, virtual_env)


def _fix_alternatives(dest_dir, relocated):
    """Fix alternative links."""
    # os.scandir() was implemented in 3.5, but for now we are in 3.4
    for dirpath, dirnames, filenames in os.walk(dest_dir):
        for name in filenames:
            rel_name = os.path.join(dirpath, name)
            if (os.path.islink(rel_name)
               and 'alternatives' in os.readlink(rel_name)):
                # We assume that the Python 2.7 alternative is living
                # in the same directory, but we create the link the
                # the place were it will live at the end
                alt_name = os.path.join(relocated, dirpath, name + '-2.7')
                alt_rel_name = rel_name + '-2.7'
                if os.path.exists(alt_rel_name):
                    os.unlink(rel_name)
                    os.symlink(alt_name, rel_name)
                else:
                    print('ERROR: alternative link for %s not found' % name)


def _fix_relocation(dest_dir, virtual_env):
    """Fix relocation shebang from python scripts"""
    shebang = '#!' + os.path.join(virtual_env, 'bin', 'python2')
    for dirpath, dirnames, filenames in os.walk(dest_dir):
        for name in filenames:
            rel_name = os.path.join(dirpath, name)
            if os.path.isfile(rel_name):
                try:
                    line = open(rel_name).readline().strip()
                    if line.startswith('#!') and 'python' in line:
                        _replace(rel_name, line, shebang)
                except Exception:
                    pass


def _fix_activators(dest_dir, virtual_env):
    """Fix virtualenv activators."""
    ld_library_path = os.path.join(virtual_env, 'lib')
    activators = {
        'activate': {
            'replace': (
                r'VIRTUAL_ENV=".*"',
                'VIRTUAL_ENV="%s"' % virtual_env
            ),
            'insert': (
                'deactivate nondestructive',
                'export LD_LIBRARY_PATH="%s"' % ld_library_path
            ),
        },
        'activate.csh': {
            'replace': (
                r'setenv VIRTUAL_ENV ".*"',
                'setenv VIRTUAL_ENV "%s"' % virtual_env
            ),
            'insert': (
                'deactivate nondestructive',
                'setenv LD_LIBRARY_PATH "%s"' % ld_library_path
            ),
        },
        'activate.fish': {
            'replace': (
                r'set -gx VIRTUAL_ENV ".*"',
                'set -gx VIRTUAL_ENV "%s"' % virtual_env
            ),
            'insert': (
                'deactivate nondestructive',
                'set -gx LD_LIBRARY_PATH "%s"' % ld_library_path
            ),
        },
    }

    for activator, action in activators.items():
        filename = os.path.join(dest_dir, 'bin', activator)
        # Fix the VIRTUAL_ENV directory
        original, line = action['replace']
        _replace(filename, original, line)

        # Add the new LD_LIBRARY_PATH.  We use the `lib` instead of
        # `lib64` in the assumption that this will remain invariant
        # for different architectures
        after, line = action['insert']
        _insert(filename, after, line)


def _fix_systemd_services(dest_dir, virtual_env):
    """Fix OpenStack systemd services."""
    services = os.path.join(dest_dir, 'usr/lib/systemd/system')
    for service in glob.glob(os.path.join(services, '*.service')):
        # Service files are read only
        os.chmod(service, 0o644)
        _replace(service, r'ExecStart=(.*)',
                 r'ExecStart=%s\1' % virtual_env)
        _replace(service, r'ExecStartPre=-(.*)',
                 r'ExecStartPre=-%s\1' % virtual_env)
        os.chmod(service, 0o444)
        # For convenience, rename the service
        os.rename(service, os.path.join(services, 'venv-' +
                  os.path.basename(service)))


def create(args):
    """Function called for the `create` command."""
    # Create the virtual environment
    options = []
    if args.system_site_packages:
        options.append('--system-site-packages')
    options = ' '.join(options)
    subprocess.call('virtualenv %s %s' % (options, args.dest_dir),
                    shell=True)

    # Prepare the links for /usr/bin and /usr/lib[64]
    usr = os.path.join(args.dest_dir, 'usr')
    os.mkdir(usr)
    os.symlink('../bin', os.path.join(usr, 'bin'))
    os.symlink('../lib', os.path.join(usr, 'lib'))
    os.symlink('../lib', os.path.join(usr, 'lib64'))

    # If both are populated, the algorithm will take precedence over
    # the `exclude` list
    include = FileList(args.include)
    exclude = FileList(args.exclude)

    # Install the packages and maintain a log
    included = []
    excluded = []
    for package in glob.glob(os.path.join(args.repo, '*.rpm')):
        rpm = os.path.basename(package)
        if rpm in exclude:
            excluded.append(rpm)
            continue
        if include.is_populated() and rpm not in include:
            excluded.append(rpm)
            continue
        included.append(rpm)

        package = os.path.abspath(package)
        subprocess.call(
            'cd %s; rpm2cpio %s | cpio --extract --unconditional '
            '--preserve-modification-time --make-directories '
            '--extract-over-symlinks' % (args.dest_dir, package),
            stdout=subprocess.DEVNULL,
            shell=True)

    _fix_virtualenv(args.dest_dir, args.relocate)

    # Write the log file, useful to better taylor the inclusion /
    # exclusion of packages.
    with open(os.path.join(args.dest_dir, 'packages.log'), 'w') as f:
        print('# Included packages', file=f)
        for rpm in sorted(included):
            print(rpm, file=f)
        print('\n\n# Excluded packages', file=f)
        for rpm in sorted(excluded):
            print(rpm, file=f)


def _filter_binary_xml(root):
    """Filter a XML tree of binary elements"""
    elements = []
    for binary in root.findall('binary'):
        rpm = binary.get('filename')
        if rpm.startswith('_'):
            continue
        if rpm.endswith('.log'):
            continue
        if rpm.endswith('src.rpm'):
            continue
        elements.append(rpm)
    return elements


def _filter_binary_name(names, args):
    """Filter a list of RPM names"""
    exclude = FileList(args.exclude)
    if args.all:
        return names
    else:
        return [rpm for rpm in names if rpm not in exclude]


def _repository(args):
    """List binary packages from a repository"""
    api = '/build/%s/%s/%s/_repository' % (args.project, args.repo,
                                           args.arch)
    output = subprocess.check_output(
        'osc --apiurl %s api %s' % (args.apiurl, api), shell=True)
    root = ET.fromstring(output)
    elements = _filter_binary_xml(ET.fromstring(output))
    # Unversioned name, so we remove the file extension
    elements = [rpm.replace('.rpm', '') for rpm in elements]
    return _filter_binary_name(elements, args)


def include(args):
    """Generate initial include-rpm file"""
    print('# List of packages to include (use Python regex)')
    print()
    print('# Packages from the repository')
    for name in _repository(args):
        print('%s.*' % name)


def exclude(args):
    """Generate initial exclude-rpm file"""
    print(EXCLUDE_RPM)


def binary(args):
    """List binary packages from a source package"""

    # OBS generate a full RPM package name, including the version and
    # architecture.  To generate include-rpm and exclude-rpm, we will
    # need only the name of the package
    rpm_re = re.compile(r'(.*)-([^-]+)-([^-]+)\.([^-\.]+)\.rpm')

    api = '/build/%s/%s/%s/%s' % (args.project, args.repo,
                                  args.arch, args.package)
    output = subprocess.check_output(
        'osc --apiurl %s api %s' % (args.apiurl, api), shell=True)
    elements = _filter_binary_xml(ET.fromstring(output))
    # Take only the name of the package
    elements = [rpm_re.match(rpm).groups()[0] for rpm in elements]
    elements = _filter_binary_name(elements, args)
    for rpm in elements:
        print(rpm)


def _filter_requires_spec(spec):
    """Recover the Requires elements from a spec file"""
    requires = re.findall(r'^Requires:\s*([-\.\w]+)(.*)?$', spec,
                          re.MULTILINE)
    return dict(requires)


def requires(args):
    """List requirements for a source package"""

    api = '/source/%s/%s/%s.spec' % (args.project, args.package,
                                     args.package)
    output = subprocess.check_output(
        'osc --apiurl %s api %s' % (args.apiurl, api), shell=True)
    requires_and_version = _filter_requires_spec(output.decode('utf-8'))
    requires = requires_and_version.keys()

    # Remove the packages included in the venv
    include = FileList(args.include)
    exclude = FileList(args.exclude)
    in_venv = []
    for rpm in requires:
        if rpm in exclude:
            continue
        if include.is_populated() and rpm not in include:
            continue
        in_venv.append(rpm)
    requires = set(requires) - set(in_venv)

    for rpm in sorted(requires):
        print(rpm, requires_and_version[rpm].strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Utility to help venvs creation for OpenStack services')
    subparsers = parser.add_subparsers(help='Sub-commands for venvjail')

    # Parser for `create` command
    subparser = subparsers.add_parser('create', help='Create a virtualenv')
    subparser.add_argument('dest_dir', metavar='DEST_DIR',
                           help='Virtual environment directory')
    subparser.add_argument('-s', '--system-site-packages',
                           action='store_false',
                           help='Allows access to the global site-packages')
    subparser.add_argument('-l', '--relocate',
                           default='/opt/ardana/venvs',
                           help='Relocated virtual environment directory')
    subparser.add_argument('-r', '--repo',
                           default='/.build.binaries',
                           help='Repository directory')
    subparser.add_argument('-i', '--include',
                           default='include-rpm',
                           help='File with the list of packages to install')
    subparser.add_argument('-x', '--exclude',
                           default='exclude-rpm',
                           help='File with packages to exclude')
    subparser.set_defaults(func=create)

    # Parser for `include` command
    subparser = subparsers.add_parser(
        'include', help='Generate initial include-rpm file')
    subparser.add_argument('-A', '--apiurl',
                           default='https://api.opensuse.org',
                           help='API address')
    subparser.add_argument('-p', '--project',
                           default='Cloud:OpenStack:Master',
                           help='Project name')
    subparser.add_argument('-r', '--repo', default='SLE_12_SP3',
                           help='Repository name')
    subparser.add_argument('-a', '--arch', default='x86_64',
                           help='Architecture')
    subparser.add_argument('--all', action='store_true',
                           help='Include all packages')
    subparser.add_argument('-x', '--exclude',
                           default='exclude-rpm',
                           help='File with packages to exclude')
    subparser.set_defaults(func=include)

    # Parser for `exclude` command
    subparser = subparsers.add_parser(
        'exclude', help='Generate initial exclude-rpm file')
    subparser.set_defaults(func=exclude)

    # Parser for `binary` command
    subparser = subparsers.add_parser(
        'binary', help='List the binary packages')
    subparser.add_argument('package', metavar='PACKAGE',
                           help='Source package name')
    subparser.add_argument('-A', '--apiurl',
                           default='https://api.opensuse.org',
                           help='API address')
    subparser.add_argument('-p', '--project',
                           default='Cloud:OpenStack:Master',
                           help='Project name')
    subparser.add_argument('-r', '--repo', default='SLE_12_SP3',
                           help='Repository name')
    subparser.add_argument('-a', '--arch', default='x86_64',
                           help='Architecture')
    subparser.add_argument('--all', action='store_true',
                           help='Include all packages')
    subparser.add_argument('-x', '--exclude',
                           default='exclude-rpm',
                           help='File with packages to exclude')
    subparser.set_defaults(func=binary)

    # Parser for `requires` command
    subparser = subparsers.add_parser(
        'requires', help='List requirements for a package')
    subparser.add_argument('package', metavar='PACKAGE',
                           help='Source package name')
    subparser.add_argument('-A', '--apiurl',
                           default='https://api.opensuse.org',
                           help='API address')
    subparser.add_argument('-p', '--project',
                           default='Cloud:OpenStack:Master',
                           help='Project name')
    subparser.add_argument('-i', '--include',
                           default='include-rpm',
                           help='File with the list of packages to install')
    subparser.add_argument('-x', '--exclude',
                           default='exclude-rpm',
                           help='File with packages to exclude')
    subparser.set_defaults(func=requires)

    args = parser.parse_args()
    args.func(args)
