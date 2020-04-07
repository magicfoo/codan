# Codan the Barbarian
A code analyzer providing the axe you need to cut fat in large and/or challenging codebases.

## Requirements
- Python 3.8
- Windows OS
- Correct C++ project (Clang is less permissive than MSVC C++ compiler)
- Little knowledge on lib-Clang API and compilation process in general
- libClang for Windows (my build included. You can try another revision)

## How to use?
1. List all the options of the tool
```
python parse.py --help
```

2. Output the full AST of the C++ source file `myprojfolder/main.cpp`. The generated AST could be huge, very huge especially for more than one input file with large amount of inclusions. To restrict the generation of the AST to a specific TU (translation unit aka *source file*), see the `--ast-tu` option to name it. The `--ast-max-depth` can also be used to limit the depth of the generated AST. Note the `--root myprojfolder` option used to filter out any input file (.cpp or .h) not belonging to the `myprojfolder` folder. The `--show_diags` option helps us to identify Clang's parsing issues. **To guarantee the complete semantic coverage of the input files, you must fix at least all the parsing errors reported by Clang!**.
```

python parse.py \
    --root myprojfolder \
    --show_diags \
    --file myprojfolder/main.cpp \
    --ast myproj.ast
   
python parse.py \
    --root myprojfolder \
    --show_diags \
    --file myprojfolder/myproj.sln \
    --ast myproj.ast \
    --ast-tu main.cpp
```

3. Output the list of *elected top-declarations* (see below) and references from a C++ project. Observe the second command line considers all the source files from the solution while excluding those from the specified project. It could be very convenient in some cases to combine inclusions and exclusions in order to expose the dependencies or the collection of semantic items of an isolated component of the codebase. Finally in the 3rd command line, by using the `--no-headers` option we don't want to grab semantic information from header files directly (.h, .hpp) as a valid TU (translation unit) and they will be dropped early. But don't worry, any .cpp file including those header files will inject the contained declarations into the tool pipeline.
```
python parse.py \
    --root myprojfolder \
    --vvi \
    --file myprojfolder/myproj.sln \
    --ref myproj.refs \
    --decl myproj.decls

python parse.py \
    --root myprojfolder \
    --vvi \
    --file myprojfolder/myproj.sln \
    --ref myproj.refs \
    --decl myproj.decls \
    --no-file myprojfolder/myproj_lib.vcproj

python parse.py \
    --root myprojfolder \
    --vvi \
    --file myprojfolder/myproj.sln \
    --ref myproj.refs \
    --decl myproj.decls \
    --no-headers
```

4. Output a sorted list of dead code sections into the `myproj.unused` file.
```
python parse.py \
    --root myprojfolder \
    --file myprojfolder/myproj.sln \
    --no-headers \
    --show_diags \
    --unused-output myproj.unused
```

## How does it work?
The tool goes over the following steps:

1. Build a collection of all the translation units to parse: TUs can be specified on the command line directly and/or extracted from MSVC C++ solutions and projects recursively. Providing a MSVC solution or project has the benefit to also provide a place for the tool to extract other critical information like additional include folders, symbols, precompile headers per project, etc. The tool parametrizes the parsing of each TU based on a per project meta-information. The tool also injects a white-listed main() entry point per project, allowing the inferring of alive or dead parts of the code.

2. libclang is called for each TUs, generating a local AST injected into the global map of definition the tool will process later.

3. The tool elects a collection of **top-level declarations**: The tool is interested into processing declarations. But not all of them are useful and a moderate granularity should be decided. For example, the declarations in the scope of a class could be just represented by the declaration of the top class itself. This step drastically reduces the amount of items to process and consists in building clusters.

4. Associate the top-level declarations together. This step is about to link related top-declarations together in order to reduce further the amount of top-declarations or clusters. For example, the forward declaration of a class could be associated to the definition declaration of the class. Another example is the case of a method defined outside of the class could be associated to the definition declaration the the class.

5. The reduced collection of top-declarations is now minimal and is called the collect of **DOIs** (declarations of interest).

6. Build the overall graph of incoming and outgoing dependencies between the DOIs. The tool traverses the deeper elements of the AST to identify references in and out of the associated DOIs, pointing in or out another DOI.

7. Apply the allowing list. The allowing list consists in USRs entries prefixed with a l, d, or m character. The l (resp. d) character prefix identifies an USR to add to the white-list of Living (resp. the black-list of Dead) declarations. The m prefix refers to a mutant declaration used to uniquely extend a living USR with the TU of its source. Tricky but we need this to dissociate for example the different main() functions from different projects in order to properly seed the next step. From there, the DOIs are flagged to be alive, dead, mutant (alive and unique) or zombi (not decided to be alive or dead yet). FYI the default allowing list consists in the single line "m c:@F@main". It means all main() functions (c:@F@main is the USR of main global function) will be by default tagged mutant DOI declarations and will recursively flag referenced DOIs as alive.

8. The tool iterates over the living DOIs, curing zombi DOIs connected as outgoing references (i.e. living DOI A gets use of zombi DOI B due to a code reference of B in A).

9. Go to 8 until no new DOI has been cured.

10. Processes different scoring on the results and generates output files.

## Status of this tool
It works, at least for me. There's probably few bugs and lot of improvements (code clarity) that must be done. It is also very easy to imagine lot of new features from this base tool overlaping or complementing perhaps on the role of a DSL if you use one, extracting data from your C++ codebase in a robust way.
A problem to mention is the usage of Python: This lang is neat for prototyping as I did so far but the excessive amount of consumed memory in general, the slowliness of the execution and the lack of robustness and organization inherent to the language itself (imho) quickly becomes scalability hard limits when the tool has to deal on a large codebase.


