#!/usr/bin/env python

import os, sys, re, time, pprint, fnmatch
from clang.cindex import *
from optparse import OptionParser, OptionGroup
from pathlib import Path
from enum import Enum

default_discarded_cursor_kind_list = [ CursorKind.UNEXPOSED_DECL, CursorKind.UNEXPOSED_EXPR, CursorKind.NAMESPACE ]
default_glob_patterns = ['*.c', '*.cpp']
default_allow_list = [ "m c:@F@main" ]
default_c_header_extensions = [ '.h', '.hpp', '.inl' ]
default_clang_options = [ '-std=c++17' ] # see https://clang.llvm.org/docs/CommandGuide/clang.html



def dict_resolve(s, d=None):
	for k,v in d.items():
		idx = s.lower().find(k.lower())
		if idx >= 0:
			r = s[:idx] + v + s[idx+len(k):]
			return dict_resolve(r, d=d)
	return s


def env_resolve(s, d={}):
	if not d:
		# build $(k)=v dictionary especially to solve msvc attributes
		for k,v in os.environ.items():
			d[ f'$({k})' ] = v
	return dict_resolve(s, d=d)


def lpath(p):
	r = os.path.normpath(p).lower()
	while True:
		rr = r.replace('//','/').replace('\\','/').replace(':','/')
		if rr==r:
			break
		r = rr
	r = rr.split('/')
	if ':' in p or p.startswith('\\'):
		return r
	elif p.startswith('/'):
		return r[1:]
	else:
		return [""] + r

def wpath(p):
	lp = lpath(p)
	if len(lp[0]) > 0:
		return os.sep.join([lp[0]+':']+lp[1:])
	elif len(lp) > 1:
		return os.sep.join(lp[1:])
	else:
		return os.sep.join(lp)

"""
def test_path(p):
	print( p, " => ", lpath(p), " ", wpath(p) )

print( test_path("/") )
print( test_path("/d") )
print( test_path("/d/a") )
print( test_path("d") )
print( test_path("d:") )
print( test_path("d:/e") )
print( test_path("d:\\fe") )
print( test_path("d/e") )
print( test_path("d:\\p4\\wwe2k\\main") )
print( test_path("/d/p4/wwe2k/main") )

exit(1)
"""


class MSVC_Project:

	def __init__(self, path, platform, configuration):
		self.path = wpath(path)
		self.hfiles = []
		self.cfiles = []
		self.attribs = {}

		def add_source_file(l, L):
			f = l.strip().split('Include=')[1].split('"')[1]
			p = self.getPath(f)
			if os.path.exists(p) and not p in L:
				L.append(p)

		prefix = None
		item_def_group = f"<ItemDefinitionGroup Condition=\"'$(Configuration)|$(Platform)'=='{configuration}|{platform}'\">"

		for l in open(self.path,'r').readlines():
			if prefix is None:
				if l.strip().startswith('<ClInclude Include='):
					add_source_file(l, self.hfiles)
				elif l.strip().startswith('<ClCompile Include='):
					add_source_file(l, self.cfiles)
				elif l.strip().startswith(item_def_group):
					prefix = []
			elif l.strip() in ['<ClCompile>', '<Link>', '<PostBuildEvent>']:
				prefix.append( l.strip()[1:-1] )
			elif l.strip() in ['</ClCompile>', '</Link>', '</PostBuildEvent>']:
				prefix = prefix[:-1]
			elif l.strip().startswith('</ItemDefinitionGroup>'):
				prefix = None
			elif not l.strip().startswith('</'):
				an = l.strip().split('>')[0].split('<')[1]
				av = l.strip().split('>')[1].split('<')[0]
				ak = '.'.join(prefix+[an])
				self.attribs[ak] = av

	def getAttrib(self, n, default=None):
		if n in self.attribs:
			return self.attribs[n]
		else:
			return default

	def resolve(self, s):
		d = {'$(solutiondir)': os.path.dirname(self.path),
			 '$(projectdir)': os.path.dirname(self.path)}
		return dict_resolve( env_resolve(s), d=d )

	def getPath(self, p):
		s = self.resolve(p)
		if os.path.isabs(s):
			return wpath(s)
		else:
			return wpath(os.path.dirname(self.path)+os.sep+s)


class MSVC_Solution:

	def __init__(self, path):
		self.path = wpath(path)
		self.projects = []

		for l in open(self.path,'r').readlines():
			if l.startswith('Project('):
				f = l.split('=')[1].split(',')[1].strip()[1:-1]
				if f.lower().endswith('.vcxproj'):
					p = wpath(os.path.dirname(self.path)+os.sep+f)
					if os.path.exists(p):
						self.projects.append(p)


class Parsing_TU:

	def __init__(self, path):
		self.path = wpath(path)
		self.additional_directories = []
		self.precompile_header = ''


def Collect_Parsing_TUs(path, platform=None, configuration=None, TU={}):
	p = wpath(path)

	if p.lower().endswith('.sln'):
		sln = MSVC_Solution(p)
		for proj in sln.projects:
			Collect_Parsing_TUs(proj, platform=platform, configuration=configuration, TU=TU)
	
	elif p.lower().endswith('.vcxproj'):
		proj = MSVC_Project(p, platform=platform, configuration=configuration)
		files = proj.hfiles + proj.cfiles
		base = Parsing_TU(p)

		for d in proj.getAttrib('ClCompile.AdditionalIncludeDirectories','').split(';'):
			f = proj.getPath( d )
			base.additional_directories.append( f )

		if proj.getAttrib('ClCompile.PrecompiledHeader','') == 'Use':
			f = proj.getPath( proj.getAttrib('ClCompile.PrecompiledHeaderFile','') )
			base.precompile_header = f

		for f in files:
			tu = Parsing_TU(f)
			tu.additional_directories = base.additional_directories
			tu.precompile_header = base.precompile_header
			TU[f] = tu

	else:
		tu = Parsing_TU(p)
		TU[p] = tu

	return TU



def dbg_node_info(n, label=''):
	print(label, cursor_id(n), cursor_tu_id(n), cursor_doi_id(n), n.kind, node_kind_mask(n), n.get_usr(), n.spelling, node_location_file(n), node_location_line_range(n), node_location_file(n.translation_unit.cursor))

def diag_location_file(d):
	if d.location and d.location.file:
		return wpath('%s'%d.location.file)
	else:
		return None

def diag_location_line(d):
	if d.location and d.location.line:
		return d.location.line
	else:
		return None

def node_location_file(n):
	# TU don't have location! Use extend instead.
	if n.location and n.location.file:
		return wpath('%s'%n.location.file)
	elif n.extent and n.extent.start and n.extent.start.file:
		return wpath('%s'%n.extent.start.file.name)
	else:
		return None

def node_location_line_range(n):
	if n.extent and n.extent.start and n.extent.end:
		return (n.extent.start.line, n.extent.end.line)
	else:
		return None

def node_location_line_count(n):
	if n.extent and n.extent.start and n.extent.end:
		return n.extent.end.line - n.extent.start.line
	else:
		return None

def node_kind_mask(node):
	m = []
	if node.is_definition(): m.append('def')
	if node.kind.is_declaration(): m.append('decl')
	if node.kind.is_reference(): m.append('ref')
	if node.kind.is_expression(): m.append('expr')
	if node.kind.is_statement(): m.append('sttm')
	if node.kind.is_attribute(): m.append('attr')
	if node.kind.is_invalid(): m.append('inv')
	if node.kind.is_translation_unit(): m.append('tu')
	if node.kind.is_preprocessing(): m.append('ppc')
	if node.kind.is_unexposed(): m.append('?')
	return '-'.join(m)

def is_path_in_project(_wp):
	wp = wpath(_wp)
	return wp.startswith(g_opts.root)

def is_node_in_project(node):
	wp = node_location_file(node)
	return wp is not None and is_path_in_project(wp)

def is_included_node(node):
	n = node_location_file(node)
	tu = node_location_file(node.translation_unit.cursor)
	return n != tu

def filter_included_nodes_duplication(nodes):
	d = {}
	for n in nodes:
		k = f"{n.kind} {node_location_file(n)} {node_location_line_range(n)} {n.extent.start.column}"
		d.setdefault(k,n)
	return list(d.values())


def cursor_id(cursor, _map={}):
	if cursor is None:
		return -1
	if not is_node_in_project(cursor):
		return -1
	return _map.setdefault(cursor.hash, len(_map))

def cursor_doi(cursor, doi=None, _map={}):
	if cursor is None:
		return None
	if doi:
		return _map.setdefault(cursor.hash, doi)
	else:
		return _map.get(cursor.hash, None)

def cursor_doi_id(cursor):
	if cursor is None:
		return None
	doi = cursor_doi(cursor)
	return cursor_id(doi.node) if doi else -1

def cursor_tu_id(cursor):
	if cursor is None:
		return None
	return cursor_id(cursor.translation_unit.cursor)



class DefinitionOfInterest:
	def __init__(self, node):
		self.node = node
		self.id = cursor_id(node)
		self.usr = node.get_usr()
		self.allow_usr = get_allow_usr(node) # special usr for segmentation purpose (see allow lists)
		self.allowance = get_node_allowance(node) # see UsrAllowance
		self.tu = node.translation_unit
		self.externals = [] # external declarations (forwards, type alias, ...) and definitions (methods, ...)
		self.in_refs = set() # DOIs referencing this DOI
		self.out_refs = set() # DOIs referenced by this DOI
		self.tag_rec(node)

	def tag_rec(self, n):
		cursor_doi(n, self)
		for c in n.get_children():
			self.tag_rec(c)

	def attach_external(self, n):
		if n != self.node:
			self.externals.append(n)
			self.tag_rec(n)



class UsrAllowance(Enum):
	Living = 1
	Dead = 2
	Zombi = 3
	Mutant = 4


def init_allow_list(allow_list):
	global allow_Llist
	global allow_Dlist
	global allow_Mlist

	allow_Llist = []
	allow_Dlist = []
	allow_Mlist = []

	for l in allow_list:
		if l.startswith('l'):
			allow_Llist.append( l[1:].strip() )
		elif l.startswith('d'):
			allow_Dlist.append( l[1:].strip() )
		elif l.startswith('m'):
			allow_Mlist.append( l[1:].strip() )


def get_usr_allowance(usr):
	if not usr:
		return None
	for m in allow_Mlist:
		if m in usr:
			return UsrAllowance.Mutant
	for l in allow_Llist:
		if l in usr:
			return UsrAllowance.Living
	for d in allow_Dlist:
		if d in usr:
			return UsrAllowance.Dead
	return UsrAllowance.Zombi


def get_node_allowance(node):
	usr = node.get_usr()
	return get_usr_allowance(usr)


def get_allow_usr(node):
	usr = node.get_usr()
	alw = get_usr_allowance(usr)
	if alw == UsrAllowance.Mutant:
		# prefix 'mutant' node with tu path id to be unique!
		# this is typically required to dissociate the different main functions for instance.
		assert not is_included_node(node)
		tu_id = cursor_tu_id(node)
		return "tu%d-%s" % (tu_id, usr)
	else:
		return usr


def collect_top_declarations(top_decls, node):
	if is_node_in_project(node):
		usr = get_allow_usr(node)
		if node.kind.is_declaration() and usr and node.kind not in default_discarded_cursor_kind_list:
			if usr in top_decls:
				top_decls[usr].append(node)
			else:
				top_decls[usr] = [node]
		else:
			for c in node.get_children():
				collect_top_declarations(top_decls, c)


def dois_collect(dois, top_decls, orphan_decls):

	def tag_rec(node, doi):
		cursor_doi(node, doi)
		for c in node.get_children():
			tag_rec(c, doi)

	def collect_pass(in_top_decls):
		pending_decls = {}

		for usr,decls in in_top_decls.items():
			doi = None

			# Find first definition among same USR decls.
			# Same identical definitions included from .h files in different TUs are disconnected (no canonical links).
			# They are duplicated but share the same USRs.
			# I noted some USRs from included definitions could have a '#' postfix for some obscure reasons to me atm (example: c:@F@simpleProcess1 instead of c:@F@simpleProcess1#)
			defs = [d for d in decls if d.is_definition()]

			if not defs or (defs[0].canonical not in decls):
				# top level decls have no definitions?
				# or the definition is non canonical (i.e. a non top level declaration exists!)
				# try to find an associated DOI
				for d in decls:
					doi = cursor_doi(d.canonical) if not doi else doi
				if doi is None:
					# wait for another round maybe?
					pending_decls[usr] = decls
			else:
				# create a new DOI from this clean top-level definition
				doi = dois.setdefault(usr, DefinitionOfInterest(defs[0]))

			# assigned to the leading DOI
			if doi:
				for d in decls:
					doi.attach_external(d)

			if g_opts.trace and g_opts.trace in usr:
				for d in decls:
					dbg_node_info(d)

		return pending_decls

	# we do multiple passes to give a chance to non-canonical decl to be associated
	# to an existing DOI created

	pending_decls = top_decls

	while pending_decls:
		pending_cnt = len(pending_decls)
		pending_decls = collect_pass( pending_decls )
		if len(pending_decls) == pending_cnt:
			break

	orphan_decls.update(pending_decls)


def dois_connect(dois):

	def connect(doi, target_node):
		target_doi = cursor_doi(target_node)
		if target_doi and target_doi != doi:
			doi.out_refs.add(target_doi)
			target_doi.in_refs.add(doi)

	def connect_rec(doi, node):
		if node.referenced and node.referenced!=node:
			connect(doi, node.get_definition())
			connect(doi, node.referenced)
		for c in node.get_children():
			connect_rec(doi, c)

	if g_opts.verbose > 0:
		print( "Connecting ..." )

	for usr,doi in dois.items():
		connect_rec(doi, doi.node)
		for x in doi.externals:
			connect_rec(doi, x)


def dois_track_unused(dois, output):

	livings = []
	deads = []
	zombies = []

	if g_opts.verbose > 0:
		print( "Segmentation initiating ..." )

	for usr,doi in dois.items():
		if doi.allowance == UsrAllowance.Living or doi.allowance == UsrAllowance.Mutant:
			livings.append( doi )
		elif doi.allowance == UsrAllowance.Dead or len(doi.in_refs)==0:
			deads.append( doi )
		else:
			zombies.append( doi )

	if g_opts.verbose > 1:
		print( "Segmentation seeded with:" )
		for doi in livings:
			print( f"L: {fmt_oneline_node(doi.node)}" )
		if g_opts.verbose > 2:
			for doi in deads:
				print( f"D: {fmt_oneline_node(doi.node)}" )

	if g_opts.verbose > 0:
		print( "Segmentation in progress ..." )

	cured = [] + livings

	while len(cured) > 0:
		_cured = cured
		cured = []
		for doi in _cured:
			for out_doi in doi.out_refs:
				try:
					i = zombies.index( out_doi )
					del zombies[i]
					cured.append( out_doi )
					livings.append( out_doi )
				except ValueError:
					pass

	# zombies become deads only if livings had failed
	# at least it needs one living doi

	if len(livings) > 0:
		deads.extend( zombies )
		zombies = []

	# output dead DOIs

	dead_nodes = []
	dead_line_counter = 0

	for doi in deads:
		nodes = filter_included_nodes_duplication( [doi.node] + doi.externals )
		lines_cnt = sum( node_location_line_count(n) for n in nodes )
		dead_nodes.append( (lines_cnt, nodes) )
		dead_line_counter += lines_cnt

	for i,k in enumerate(sorted(dead_nodes, key=lambda v: v[0], reverse=True)):
		lines_cnt, nodes = k
		for n in nodes:
			output.write( f"{i}| {lines_cnt} lines| {fmt_oneline_node(n)}\n" )
		output.write( "\n" )

	output.write( f"\n{dead_line_counter} lines were found unused.\n" )


def fmt_oneline_node(node):
	cursor_id(node)
	usr = node.get_usr()
	c_id = cursor_id(node)
	tu_id = cursor_tu_id(node)
	k = str(node.kind).split('.')[1]
	a = str(get_usr_allowance(usr)).split('.')[1]
	locf = node_location_file(node)
	loclines = node_location_line_range(node)
	return f"id {c_id}: tu {tu_id}: alw {a}: usr {usr}: loc {locf}{loclines}: kind {k}: {node.spelling}"


def fmt_node(node, children=None):
	cursor_id(node)
	return { 'id' : cursor_id(node),
			 'doi-id' : cursor_doi_id(node),
			 'kind' : f"{str(node.kind).split('.')[1]} {{{node_kind_mask(node)}}}",
			 'usr' : node.get_usr(),
			 'allow-usr' : get_allow_usr(node),
			 'spelling' : node.spelling,
			 'location' : f"{node.location.file} [{node.location.line}]",
			 'extent' : f"{node.extent.start.file} [{node.extent.start.line}-{node.extent.end.line}]",
			 'is-definition' : node.is_definition(),
			 'canonical-id' : cursor_id(node.canonical),
			 'definition-id' : cursor_id(node.get_definition()),
			 'referenced-id' : cursor_id(node.referenced),
			 'children' : children }


def fmt_node_rec(node, filtering_off=False, depth=0):
	if not node.kind.is_unexposed():
		cursor_id(node)
		children = []
		if (g_opts.ast_max_depth <= 0) or (depth < g_opts.ast_max_depth):
			for c in node.get_children():
				if filtering_off or is_node_in_project(c):
					children.append( fmt_node_rec(c, filtering_off, depth+1) )
		return fmt_node(node, children)


def main():
	global g_opts

	def glob_from_dir(in_root, in_patterns):
		files = []
		for root, dirnames, filenames in os.walk(in_root):
			for pattern in in_patterns:
				for filename in fnmatch.filter(filenames, pattern):
					files.append(wpath(os.path.join(root,filename)))
		return files

	def path_opt(opt, opt_str, value, parser):
		p = wpath(os.path.abspath(value))
		setattr(parser.values, opt.dest, p)

	def file_opt(opt, opt_str, value, parser):
		p = wpath(os.path.abspath(value))
		l = getattr(parser.values, opt.dest)
		Collect_Parsing_TUs(p, 'x64', 'Release', l)

	def glob_opt(opt, opt_str, value, parser):
		r = getattr(parser.values, 'root')
		l = getattr(parser.values, opt.dest)
		patterns = [p for p in value.split()]
		for f in glob_from_dir(r, patterns):
			l[f] = Parsing_TU(f)

	def tu_opt(opt, opt_str, value, parser):
		l = getattr(parser.values, opt.dest)
		l.extend( [p for p in value.split()] )

	def clang_opt(opt, opt_str, value, parser):
		print(opt_str, value)
		if opt_str=='-I' or opt_str=='--inc-dir':
			parser.values.clang_args.append( f"-I{wpath(value)}" )

	def clang_opt(opt, opt_str, value, parser):
		assert value is None
		args = []
		for arg in parser.rargs:
			if arg[:2] == "--" and len(arg) > 2:
				break
			args.append(arg)
		del parser.rargs[:len(args)]
		l = getattr(parser.values, opt.dest)
		l.extend(args)

	parser = OptionParser("usage: %prog [options] [clang-args*]")

	parser.add_option("-v", "--verbose", dest="verbose",
					  help="Increase verbose level.",
					  action="count", default=0)

	parser.add_option("-i", "--show-diags", dest="show_diags",
					  help="Show diagnostics.",
					  action="store_true", default=False)

	parser.add_option("-w", "--show-warnings", dest="show_warnings",
					  help="Show parsing warnings.",
					  action="store_true", default=False)

	parser.add_option("-s", "--stop-on-diags", dest="stop_on_diags",
					  help="Stop if any diagnotics if found.",
					  action="store_true", default=False)

	parser.add_option("-r", "--root", dest="root",
					  help="Define the source code root directory.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("-e", "--ref", dest="ref_file",
					  help="Output the reference nodes to the given file.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("-d", "--decl", dest="decl_file",
					  help="Output the declaration nodes to the given file.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("-f", "--file", dest="files",
					  help="Source file(s) to parse. This option could be used more than one time.",
					  type="string", action="callback", callback=file_opt, default={})

	parser.add_option("-x", "--no-file", dest="no_files",
					  help="Source file(s) to not parse. This option could be used more than one time.",
					  type="string", action="callback", callback=file_opt, default={})

	parser.add_option("-g", "--glob", dest="files",
					  help="Recursively glob source file(s) to parse. Example: -g \"*.c\" or -g \"*.c, *.h\". This option could be used more than one time.",
					  type="string", action="callback", callback=glob_opt, default={})

	parser.add_option("", "--no-headers", dest="no_headers",
					  help="Do not process c/c++ header files.",
					  action="store_true", default=False)

	parser.add_option("-u", "--unused", dest="unused",
					  help="Search for unused dead code entities.",
					  action="store_true", default=False)

	parser.add_option("", "--unused-output", dest="unused_file",
					  help="Output unused section of code into the given file.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("", "--allow", dest="allow_file",
					  help="Provide a file containing a white and black lists of code USRs used to seed analyzing processing.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("-a", "--ast", dest="ast_file",
					  help="Output the AST to the given file.",
					  type="string", action="callback", callback=path_opt, default=None)

	parser.add_option("", "--ast-max-depth", dest="ast_max_depth",
					  help="Maximum depth of the outputed AST. A value <= 0 stands for an unlimited depth.",
					  type="int", action="store", default=0)

	parser.add_option("", "--ast-tu", dest="ast_tus",
					  help="Restrict the AST outout to the given TU file(s). This option could be used more than one time.",
					  type="string", action="callback", callback=tu_opt, default=[])

	parser.add_option("", "--full-ast", dest="full_ast",
					  help="Output the full AST w/o any filtering.",
					  action="store_true", default=False)

	parser.add_option("-t", "--trace", dest="trace",
					  help="Trace a USR.",
					  type="string", default=None)

	parser.add_option("-c", "--clang", dest="clang_args",
					  help="Pass arbitrary arguments to clang processing. See https://clang.llvm.org/docs/CommandGuide/clang.html",
					  action="callback", callback=clang_opt, default=[])

	parser.disable_interspersed_args()
	(g_opts, args) = parser.parse_args()

	if args:
		parser.error( f"Unexpected args {args}" )

	if not g_opts.root:
		parser.error("Must specified a root folder. Use --help to see options.")

	# filter out excluded source files
	if g_opts.no_files and g_opts.files:
		for f in g_opts.no_files.keys():
			if f in g_opts.files:
				del g_opts.files[f]

	# filter out source files not in specified root folder
	if g_opts.files:
		files = [f for f in g_opts.files.keys()]
		for f in files:
			if not is_path_in_project(f):
				del g_opts.files[f]

	if not g_opts.files:
		parser.error("No source file(s)! Use --help to see options.")

	if g_opts.no_headers:
		input_files = g_opts.files
		g_opts.files = {}
		for f,tu in input_files.items():
			r,ext = os.path.splitext(f)
			if not ext or not ext in default_c_header_extensions:
				g_opts.files[f] = tu

	if g_opts.allow_file:
		init_allow_list( open(g_opts.allow_file,"r").readlines() )
	else:
		init_allow_list( default_allow_list )

	clang_args = g_opts.clang_args or default_clang_options

	if g_opts.verbose > 0:
		print( f"root: {g_opts.root}" )
		print( f"allow-L-list: {allow_Llist}")
		print( f"allow-D-list: {allow_Dlist}")
		print( f"allow-M-list: {allow_Mlist}")
		print( f"ast-file: {g_opts.ast_file}" )
		print( f"ast-max-depth: {g_opts.ast_max_depth}" )
		print( f"ast-tus: {g_opts.ast_tus}" )
		print( f"ref-file: {g_opts.ref_file}" )
		print( f"decl-file: {g_opts.decl_file}" )
		print( f"unused-file: {g_opts.unused_file}" )
		print( f"clang-args: {clang_args}" )
		print( f"input-files ({len(g_opts.files)}):" )
		for f in g_opts.files:
			print( f"\t\"{f}\"" )


	tus = {}
	top_decls = {}
	orphan_decls = {}
	errors = []
	dois = {}

	start_tm = time.time()

	index = Index.create()

	for f,ftu in g_opts.files.items():
		print( f"@@ Parsing \"{f}\" ...")

		try:
			tu_clang_args = [i for i in clang_args]
			for hdir in ftu.additional_directories:
				tu_clang_args += ['-I', hdir]
			if ftu.precompile_header:
				tu_clang_args += ['-include', ftu.precompile_header]
			if g_opts.verbose > 1:
				print( f"@@ Args {tu_clang_args}")
			tu = index.parse(f, tu_clang_args)
		except TranslationUnitLoadError:
			print( f"cindex.TranslationUnitLoadError received while parsing input \"{f}\"" )
			print( "Fatal parsing error. Aborted." )
			exit(1)

		if not tu:
			print( f"Unable to load input \"{f}\"" )
			print( "Fatal parsing error. Aborted." )
			exit(1)

		# check diags
		# see https://clang.llvm.org/docs/DiagnosticsReference.html
		for d in tu.diagnostics:
			def print_diag_info(diag):
				print( f"{str(diag.format(Diagnostic._FormatOptionsMask))}")

			if d.severity==Diagnostic.Fatal:
				print_diag_info(d)
				print( "Fatal parsing error. Aborted." )
				exit(1)

			if g_opts.show_diags:
				if d.severity > Diagnostic.Warning:
					errors.append(d)
				if d.severity > Diagnostic.Warning or g_opts.show_warnings:
					print_diag_info(d)

		if len(tu.diagnostics) > 0 and g_opts.stop_on_diags:
			print(f"({len(tu.diagnostics)}) diags found so far... Stopped.")
			exit(1)

		tus[f] = tu
		collect_top_declarations(top_decls, tu.cursor)


	if len(top_decls) == 0:
		print("No top declarations. Exit")
		exit(1)

	if g_opts.verbose > 0:
		print( f"#top-decls: {len(top_decls)}")

	dois_collect(dois, top_decls, orphan_decls)

	if g_opts.verbose > 0:
		print( f"#clang-errors: {len(errors)}")
		print( f"#dois: {len(dois)}")
		print( f"#orphans: {len(orphan_decls)}")

	dois_connect(dois)

	if g_opts.ast_file:
		pp_ast = pprint.PrettyPrinter(indent=4, width=99, compact=False, sort_dicts=False, stream=open(g_opts.ast_file,"w"))
		for f,tu in tus.items():
			if not g_opts.ast_tus or any(t in node_location_file(tu.cursor) for t in g_opts.ast_tus):
				pp_ast.pprint(fmt_node_rec(tu.cursor, filtering_off=g_opts.full_ast))

	if g_opts.decl_file:
		with open(g_opts.decl_file, "w") as output:
			for usr,doi in dois.items():
				output.write( f"DOI: doi-usr {usr}: {fmt_oneline_node(doi.node)}: in/out {len(doi.in_refs)}/{len(doi.out_refs)}\n" )
			for usr,decls in orphan_decls.items():
				for d in decls:
					output.write( f"ORPHAN: usr {usr}: {fmt_oneline_node(d)}\n" )

	if g_opts.ref_file:
		with open(g_opts.ref_file, "w") as output:
			for usr,doi in dois.items():
				in_ids  = [cursor_id(r.node) for r in doi.in_refs]
				out_ids = [cursor_id(r.node) for r in doi.out_refs]		
				output.write( f"DOI: doi-usr {usr}: id {cursor_id(doi.node)}: in-refs {sorted(in_ids)}: out-refs {sorted(out_ids)}\n" )

	if g_opts.unused or g_opts.unused_file:
		unused_output = open(g_opts.unused_file,"w") if g_opts.unused_file else sys.stdout
		dois_track_unused(dois, unused_output)

	end_tm = time.time()
	print( f"Completed in {round(end_tm-start_tm,1)}s" )


if __name__ == '__main__':
	main()

