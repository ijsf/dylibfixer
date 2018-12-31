# dylibfixer

Python replacement for dylibbundler.

```
usage: dylibfixer.py [-h] -b PATH -d PATH -r LDPATH [-l PATH]

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

optional arguments:
  -h, --help            show this help message and exit
  -b PATH, --bundle PATH
                        path to app bundle binary
  -d PATH, --dest-dir PATH
                        path to dependency destination directory
  -r LDPATH, --dest-ldpath LDPATH
                        ldpath to dependency destination directory
  -l PATH, --library-dir PATH
                        include library directory to search for missing dependencies
```

