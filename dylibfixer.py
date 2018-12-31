#!/usr/bin/python
#
# Python replacement for dylibbundler.
#
# Copyright (c) C.E. Etheredge, ijsf, 2018
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# 
import os
import sys
import re
import subprocess
from argparse import ArgumentParser, RawTextHelpFormatter
from shutil import copyfile
from collections import namedtuple

DESCRIPTION="""
dylibfixer recursively processes the dylib dependency tree of a Mach-O binary,
producing a single bundle directory containing the entire dependency tree.

It does so by copying all nested dependencies to a given directory, changing
all references accordingly, so that a self-containing bundle can be produced
that can be used across multiple macOS systems without relying on any
non-standard system installed libraries.

To produce a self-contained app bundle, make sure to:

  1. Specify a destination directory that is part of the app bundle directory
     structure.
  2. Specify a ldpath directory that references (1) without using any absolute
     paths. The use of @executable_path or @loader_path is recommended, and
	 will typically refer to your bundle executable directory:
	   * Use @executable_path for standard (standalone) applications.
	   * Use @loader_path if @executable_path doesn't work, e.g. when your
	   	 binary itself is dynamically loaded by another executable.
	 This path will be used at run-time to dynamically load the dylibs!
  3. Optionally specify any library directories (-l) in order to find your
     dylibs (e.g. /usr/local/lib).

EXAMPLE USE CASE

  Bundle root:        ./com.mybundle.app
  Bundle executable:  ./com.mybundle.app/Contents/MacOS/com.mybundle
  Destination path:   ./com.mybundle.app/Contents/libs (new directory)
  Destination ldpath: @loader_path/../libs

  dylibfixer.py -b ./com.mybundle.app/Contents/MacOS/com.mybundle
                -d ./com.mybundle.app/Contents/libs
				-r @loader_path/../libs
				-l ./SomeBuildDirectoryContainingLibFiles
				-l /usr/local/lib

  The above should produce a self-contained com.mybundle.app, with
  all dylibs contained in directory ./com.mybundle.app/Contents/libs.
"""

#
# NOTES
#
# * No support for spaces in otool paths.
# * Only first defined rpath is ever used.
#

### Configuration

# Path to otool
OTOOL = "/usr/bin/otool"

# Path to install_name_tool
INSTALL_NAME_TOOL = "/usr/bin/install_name_tool"

# Regular expressions for dylibs that should be excluded from parsing
DYLIB_EXCLUSIONS = [ '^/System/.*', '\.framework', '^/usr/lib/.*' ]

### Definitions

Dep = namedtuple('Dep', 'src resolved')

### Functions

def otool_list(path_binary):
	return subprocess.check_output([OTOOL, "-l", path_binary]).split("\n")

# Changes the path of a dylib in a binary using install_name_tool
def install_name_tool_change(path_binary, path_dylib_source, path_dylib_dest):
	return subprocess.check_output([INSTALL_NAME_TOOL, "-change", path_dylib_source, path_dylib_dest, path_binary])

# Changes the id of a dylib using install_name_tool
def install_name_tool_id(path_binary, dylib_id):
	return subprocess.check_output([INSTALL_NAME_TOOL, "-id", dylib_id, path_binary])

def otool_get_rpaths(path_binary):
	rpath = []
	token_parse = False
	for line in otool_list(path_binary):
		# Look for cmd LC_RPATH
		if re.search(r"cmd\s+LC_RPATH", line):
			token_parse = True
		elif token_parse:
			# Look for path associated with LC_RPATH
			match = re.search(r"path\s+(\S+?)\s+", line)
			if match:
				rpath.append(match.group(1))
				token_parse = False
	return rpath

# Get dylib dependencies
def otool_get_deps(path_binary):
	deps = []
	token_parse = False
	for line in otool_list(path_binary):
		# Look for cmd LC_LOAD_DYLIB
		if re.search(r"cmd\s+LC_LOAD_DYLIB", line):
			token_parse = True
		elif token_parse:
			# Look for path associated with LC_LOAD_DYLIB
			match = re.search(r"name\s+(\S+?)\s+", line)
			if match:
				deps.append(match.group(1))
				token_parse = False
	return deps

# Get dependencies with @rpath, resolved to absolute paths according to resolve map
def otool_resolve_deps(path_binary, path_dest, map_resolve):
	return [Dep(p, os.path.abspath(path_resolve_otool(p, path_dest, map_resolve))) for p in otool_get_deps(path_binary)]

# Check if dylib path should be excluded
def path_dylib_exclude(path_dylib):
	for exclude in DYLIB_EXCLUSIONS:
		if re.search(exclude, path_dylib):
			return True
	return False

# Check if dylib exists in any of the specified directories
def path_dylib_find(basename, dirs):
	for dir in dirs:
		path = os.path.abspath(os.path.join(dir, basename))
		if os.path.exists(path):
			# Found in library
			return path
	# Not found
	return False

# Resolve otool path according to resolve map
def path_resolve_otool(path_otool, path_dest, map_resolve):
	done = False
	while not done:
		done = True
		for key in map_resolve:
			# Check for match with @key
			if map_resolve[key] and path_otool.find("@" + key) >= 0:
				# Replace @key with actual path value
				path_otool = path_otool.replace("@" + key, map_resolve[key])
				# Restart exhaustive matching
				done = False
				break
		# All possible matches exhausted
	# All rpath variables should have been replaced, and the path should be absolute
	# In case the path is relative (it shouldn't be), assume it is already present in the destination directory, and resolve to there
	if not os.path.isabs(path_otool):
		path_otool = os.path.abspath(os.path.join(path_dest, path_otool))
	return path_otool

# Resolve otool deps recursively
def otool_resolve(path_binary, path_binary_original, path_dest, ldpath_dest, map_resolve, paths_library, visited = [], level = 0):
	# Resolve to absolute path and basename
	path_binary = os.path.abspath(path_binary)
	path_basename = os.path.basename(path_binary)
	# Add to visited paths
	visited.append(path_basename)
	# Get rpaths for binary
	rpaths = otool_get_rpaths(path_binary)
	# Adjust map_resolve with the binary's own rpath and loader_path
	m = map_resolve.copy()
	m["rpath"] = rpaths[0] if rpaths else None
	m["loader_path"] = os.path.dirname(path_binary_original)
	# Resolve deps using original absolute path
	deps = otool_resolve_deps(os.path.abspath(path_binary_original), path_dest, m)
	print "-" * level + "+", path_binary
	# For each of the deps, resolve recursively
	for dep in deps:
		# Determine (resolved) path
		dep_path = dep.resolved
		# Get basename
		dep_basename = os.path.basename(dep_path)
		# Determine destination path
		dep_dest = os.path.join(path_dest, dep_basename)
		# If this dep is not excluded from further processing
		if not path_dylib_exclude(dep_path):
			# In any case, change the dep's path in the binary to the destination dir
			install_name_tool_change(path_binary, dep.src, os.path.join(ldpath_dest, dep_basename))
			# Skip itself for further processing
			if not dep_basename == path_basename:
				# Check if path to binary exists
				if not os.path.exists(dep_path):
					# Could not be found, try and see if it exists in the destination directory already
					if os.path.exists(dep_dest):
						# Adjust the resolved path accordingly
						dep_path = dep_dest
					else:
						# Try and check if it exists in any of the specified library directories
						dep_path_library = path_dylib_find(dep_basename, paths_library)
						if dep_path_library:
							# Adjust the resolved path accordingly
							dep_path = dep_path_library
						else:
							# Could not be found at all
							raise Exception("Dependency '" + dep_path + "' ('" + dep.src + "') does not exist")
				# If this dep has not already been visited, copy it to the destination and process it recursively
				if dep_basename not in visited:
					# Copy this dependency to destination dir, if necessary
					if not dep_path == dep_dest:
						copyfile(dep_path, dep_dest)
					# Change id of the newly copied dependency
					install_name_tool_id(dep_dest, dep_basename)
					# Resolve newly copied dependency recursively
					otool_resolve(dep_dest, dep_path, path_dest, ldpath_dest, m, paths_library, visited, level + 1)

### Main

parser = ArgumentParser(description=DESCRIPTION, formatter_class=RawTextHelpFormatter)
parser.add_argument("-b", "--bundle", dest="bundle", help="path to app bundle binary", metavar="PATH", required=True)
parser.add_argument("-d", "--dest-dir", dest="path_dest", help="path to dependency destination directory", metavar="PATH", required=True)
parser.add_argument("-r", "--dest-ldpath", dest="ldpath_dest", help="ldpath to dependency destination directory", metavar="LDPATH", required=True)
parser.add_argument("-l", "--library-dir", dest="paths_library", help="include library directory to search for missing dependencies", metavar="PATH", action="append")
args = parser.parse_args()

# Path checks
if not os.path.exists(args.bundle):
	sys.exit("Bundle binary '" + args.bundle + "' not found")
if not os.path.exists(args.path_dest):
	sys.exit("Destination directory '" + args.path_dest + "' not found")

# Figure out executable_path
executable_path = os.path.dirname(args.bundle)
# Figure out rpath (only first rpath used)
rpath = otool_get_rpaths(args.bundle)[0]
# Create initial resolve map
map_resolve = {
	'executable_path': executable_path,
	'rpath': args.path_dest #rpath
}
otool_resolve(args.bundle, args.bundle, args.path_dest, args.ldpath_dest, map_resolve, args.paths_library)
