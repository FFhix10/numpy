""" Modified version of build_ext that handles fortran source files.
"""

import os
import string
import sys
from glob import glob
from types import *

from distutils.dep_util import newer_group, newer
from distutils.command.build_ext import build_ext as old_build_ext

from scipy_distutils.command.build_clib import get_headers,get_directories
from scipy_distutils import misc_util, log
from scipy_distutils.misc_util import filter_sources, has_f_sources, has_cxx_sources

class build_ext (old_build_ext):

    description = "build C/C++/F extensions (compile/link to build directory)"

    user_options = old_build_ext.user_options + [
        ('fcompiler=', None,
         "specify the Fortran compiler type"),
        ]

    def initialize_options(self):
        old_build_ext.initialize_options(self)
        self.fcompiler = None
        return

    def run(self):
        if not self.extensions:
            return

        if self.distribution.has_c_libraries():
            build_clib = self.get_finalized_command('build_clib')
            self.library_dirs.append(build_clib.build_clib)
        else:
            build_clib = None

        # Not including C libraries to the list of
        # extension libraries automatically to prevent
        # bogus linking commands. Extensions must
        # explicitly specify the C libraries that they use.

        save_mth = self.distribution.has_c_libraries
        self.distribution.has_c_libraries = self.distribution.return_false
        old_build_ext.run(self)   # sets self.compiler
        self.distribution.has_c_libraries = save_mth
        
        # Determine if Fortran compiler is needed.
        if build_clib and build_clib.fcompiler is not None:
            need_f_compiler = 1
        else:
            need_f_compiler = 0
            for ext in self.extensions:
                if has_f_sources(ext.sources):
                    need_f_compiler = 1
                    break

        # Determine if C++ compiler is needed.
        need_cxx_compiler = 0
        for ext in self.extensions:
            if has_cxx_sources(ext.sources):
                need_cxx_compiler = 1
                break

        # Initialize Fortran/C++ compilers if needed.
        if need_f_compiler:
            from scipy_distutils.fcompiler import new_fcompiler
            self.fcompiler = new_fcompiler(compiler=self.fcompiler,
                                           verbose=self.verbose,
                                           dry_run=self.dry_run,
                                           force=self.force)
            self.fcompiler.customize(self.distribution)
            self.fcompiler.customize_cmd(self)
        if need_cxx_compiler:
            c = self.compiler
            if c.compiler[0].find('gcc')>=0:
                if sys.version[:3]>='2.3':
                    if not c.compiler_cxx:
                        c.compiler_cxx = [c.compiler[0].replace('gcc','g++')]\
                                         + c.compiler[1:]
                else:
                    c.compiler_cxx = [c.compiler[0].replace('gcc','g++')]\
                                     + c.compiler[1:]
            else:
                print 'XXX: Fix compiler_cxx for',c.__class__.__name__

        # Build extensions
        self.build_extensions2()
        return

    def build_extensions(self):
        # Hold on building extensions in old_build_ext.run()
        # until Fortran/C++ compilers are set. Building will be
        # carried out in build_extensions2()
        return

    def build_extensions2(self):
        old_build_ext.build_extensions(self)
        return

    def swig_sources(self, sources):
        # Do nothing. Swig sources have beed handled in build_src command.
        return sources

    def build_extension(self, ext):
        sources = ext.sources
        if sources is None or type(sources) not in (ListType, TupleType):
            raise DistutilsSetupError, \
                  ("in 'ext_modules' option (extension '%s'), " +
                   "'sources' must be present and must be " +
                   "a list of source filenames") % ext.name
        sources = list(sources)

        fullname = self.get_ext_fullname(ext.name)
        if self.inplace:
            modpath = string.split(fullname, '.')
            package = string.join(modpath[0:-1], '.')
            base = modpath[-1]

            build_py = self.get_finalized_command('build_py')
            package_dir = build_py.get_package_dir(package)
            ext_filename = os.path.join(package_dir,
                                        self.get_ext_filename(base))
        else:
            ext_filename = os.path.join(self.build_lib,
                                        self.get_ext_filename(fullname))
        depends = sources + ext.depends

        if not (self.force or newer_group(depends, ext_filename, 'newer')):
            log.debug("skipping '%s' extension (up-to-date)", ext.name)
            return
        else:
            log.info("building '%s' extension", ext.name)

        extra_args = ext.extra_compile_args or []
        macros = ext.define_macros[:]
        for undef in ext.undef_macros:
            macros.append((undef,))

        c_sources, cxx_sources, f_sources, fmodule_sources = filter_sources(ext.sources)

        if sys.version[:3]>='2.3':
            kws = {'depends':ext.depends}
        else:
            kws = {}

        c_objects = self.compiler.compile(c_sources,
                                          output_dir=self.build_temp,
                                          macros=macros,
                                          include_dirs=ext.include_dirs,
                                          debug=self.debug,
                                          extra_postargs=extra_args,
                                          **kws)
        if cxx_sources:
            old_compiler = self.compiler.compiler_so[0]
            self.compiler.compiler_so[0] = self.compiler.compiler_cxx[0]
            c_objects += self.compiler.compile(cxx_sources,
                                              output_dir=self.build_temp,
                                              macros=macros,
                                              include_dirs=ext.include_dirs,
                                              debug=self.debug,
                                              extra_postargs=extra_args,
                                              **kws)
            self.compiler.compiler_so[0] = old_compiler

        check_for_f90_modules = not not fmodule_sources

        if f_sources or fmodule_sources:
            extra_postargs = []
            include_dirs = ext.include_dirs[:]
            module_dirs = [] # XXX Figure out how users could change this?

            if check_for_f90_modules:
                module_build_dir = os.path.join(\
                    self.build_temp,os.path.dirname(\
                    self.get_ext_filename(fullname)))

                self.mkpath(module_build_dir)
                if self.fcompiler.module_dir_switch is None:
                    existing_modules = glob('*.mod')
                extra_postargs += self.fcompiler.module_options(\
                    module_dirs,module_build_dir)

            f_objects = self.fcompiler.compile(fmodule_sources,
                                               output_dir=self.build_temp,
                                               macros=macros,
                                               include_dirs=include_dirs,
                                               debug=self.debug,
                                               extra_postargs=extra_postargs,
                                               depends=ext.depends)

            if check_for_f90_modules \
                   and self.fcompiler.module_dir_switch is None:
                for f in glob('*.mod'):
                    if f in existing_modules:
                        continue
                    self.move_file(f, module_build_dir)

            f_objects += self.fcompiler.compile(f_sources,
                                                output_dir=self.build_temp,
                                                macros=macros,
                                                include_dirs=include_dirs,
                                                debug=self.debug,
                                                extra_postargs=extra_postargs,
                                                depends=ext.depends)
        else:
            f_objects = []

        objects = c_objects + f_objects

        if ext.extra_objects:
            objects.extend(ext.extra_objects)
        extra_args = ext.extra_link_args or []

        old_linker_so_0 = self.compiler.linker_so[0]

        use_fortran_linker = 0
        c_libraries = []
        c_library_dirs = []
        if f_sources:
            use_fortran_linker = 1
        elif self.distribution.has_c_libraries():            
            build_clib = self.get_finalized_command('build_clib')
            f_libs = []
            for (lib_name, build_info) in build_clib.libraries:
                if has_f_sources(build_info.get('sources',[])):
                    f_libs.append(lib_name)
                if lib_name in ext.libraries:
                    # XXX: how to determine if c_libraries contain
                    # fortran compiled sources?
                    c_libraries.extend(build_info.get('libraries',[]))
                    c_library_dirs.extend(build_info.get('library_dirs',[]))
            for l in ext.libraries:
                if l in f_libs:
                    use_fortran_linker = 1
                    break

        if use_fortran_linker:
            if cxx_sources:
                # XXX: Which linker should be used, Fortran or C++?
                log.warn('mixing Fortran and C++ is untested')
            link = self.fcompiler.link_shared_object
            language = ext.language or self.fcompiler.detect_language(f_sources)
        else:
            link = self.compiler.link_shared_object
            if sys.version[:3]>='2.3':
                language = ext.language or self.compiler.detect_language(sources)
            else:
                language = ext.language
            if cxx_sources:
                self.compiler.linker_so[0] = self.compiler.compiler_cxx[0]

        if sys.version[:3]>='2.3':
            kws = {'target_lang':language}
        else:
            kws = {}

        link(objects, ext_filename,
             libraries=self.get_libraries(ext) + c_libraries,
             library_dirs=ext.library_dirs + c_library_dirs,
             runtime_library_dirs=ext.runtime_library_dirs,
             extra_postargs=extra_args,
             export_symbols=self.get_export_symbols(ext),
             debug=self.debug,
             build_temp=self.build_temp,**kws)

        self.compiler.linker_so[0] = old_linker_so_0
        return

    def get_source_files (self):
        self.check_extensions_list(self.extensions)
        filenames = []
        def visit_func(filenames,dirname,names):
            if os.path.basename(dirname)=='CVS':
                return
            for name in names:
                fullname = os.path.join(dirname,name)
                if os.path.isfile(fullname):
                    filenames.append(fullname)
        # Get sources and any include files in the same directory.
        for ext in self.extensions:
            sources = filter(lambda s:type(s) is StringType,ext.sources)
            filenames.extend(sources)
            filenames.extend(get_headers(get_directories(sources)))
            for d in ext.depends:
                if is_local_src_dir(d):
                    os.path.walk(d,visit_func,filenames)
                elif os.path.isfile(d):
                    filenames.append(d)
        return filenames

def is_local_src_dir(directory):
    """ Return true if directory is local directory.
    """
    abs_dir = os.path.abspath(directory)
    c = os.path.commonprefix([os.getcwd(),abs_dir])
    new_dir = abs_dir[len(c):].split(os.sep)
    if new_dir and not new_dir[0]:
        new_dir = new_dir[1:]
    if new_dir and new_dir[0]=='build':
        return 0
    new_dir = os.sep.join(new_dir)
    return os.path.isdir(new_dir)
