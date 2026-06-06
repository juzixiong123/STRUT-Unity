import sys
import clang.cindex
from clang import cindex
from clang.cindex import CursorKind, TokenKind

from extract_function_info import extract_function_info
from util import write_content

clang.cindex.Config.set_library_path(r'path')


def collect_top_level_definitions(file_path):
    """
    Collects all macro definitions, structure definitions, variable declarations, etc.,
    outside of functions in the file and obtains their row and column ranges.
    :param file_path: source file path
    :return: definitions list，element is {'name': name, 'range': ((start_line, start_col), (end_line, end_col)), 'kind': define type}
    """
    definitions = []
    index = cindex.Index.create()
    translation_unit = index.parse(file_path, args=['-Xclang', '-detailed-preprocessing-record'])

    for cursor in translation_unit.cursor.walk_preorder():
        if cursor.location.file and cursor.location.file.name == file_path:
            if cursor.kind == CursorKind.MACRO_DEFINITION:
                name = cursor.spelling
                start_line = cursor.extent.start.line
                start_col = cursor.extent.start.column
                end_line = cursor.extent.end.line
                end_col = cursor.extent.end.column
                definitions.append({
                    'name': name,
                    'range': ((start_line, start_col), (end_line, end_col)),
                    'kind': 'macro'
                })
            elif cursor.kind in [
                CursorKind.STRUCT_DECL, CursorKind.VAR_DECL, CursorKind.TYPEDEF_DECL,
                CursorKind.ENUM_DECL, CursorKind.UNION_DECL
            ] and cursor.semantic_parent.kind == CursorKind.TRANSLATION_UNIT:
                if cursor.kind == CursorKind.STRUCT_DECL:
                    prefix = 'struct '
                elif cursor.kind == CursorKind.UNION_DECL:
                    prefix = 'union '
                elif cursor.kind == CursorKind.ENUM_DECL:
                    prefix = 'enum '
                else:
                    prefix = ''

                name = prefix + cursor.spelling
                start_line = cursor.extent.start.line
                start_col = cursor.extent.start.column
                end_line = cursor.extent.end.line
                end_col = cursor.extent.end.column
                definitions.append({
                    'name': name,
                    'range': ((start_line, start_col), (end_line, end_col)),
                    'kind': cursor.kind
                })
    return definitions


def simplify_file(file_path):
    """
    Simplify source files by removing unused macro definitions, structure definitions, variable declarations, etc.
    :param file_path: source file path
    """
    variable_types, global_variables = extract_function_info(file_path)
    definitions = collect_top_level_definitions(file_path)
    with open(file_path, 'r', encoding='utf-8') as f:
        source_code = f.readlines()
    used_names = set()
    used_names.update(variable_types)
    used_names.update(global_variables)
    unused_definitions = []
    for definition in definitions:
        if definition['name'] not in used_names:
            unused_definitions.append(definition)
    new_source_code = remove_unused_definitions(source_code, unused_definitions)
    output_file = file_path
    write_content(output_file, ''.join(new_source_code))


def remove_unused_definitions(source_lines, unused_definitions):
    """
    Remove unused definitions from the source code, directly using row and column ranges for deletion.
    :param source_lines: line List of Source Code
    :param unused_definitions: list of unused definitions, each element contains 'name' and 'range' information
    :return: deleting the list of source code lines after unused definitions
    """
    sorted_definitions = sorted(unused_definitions, key=lambda d: (d['range'][0][0], d['range'][0][1]), reverse=True)

    for definition in sorted_definitions:
        start_line, start_col = definition['range'][0]
        end_line, end_col = definition['range'][1]
        end_col = end_col + 1
        start_line_idx = start_line - 1
        end_line_idx = end_line - 1

        if start_line_idx == end_line_idx:
            line = source_lines[start_line_idx]
            new_line = line[:start_col - 1] + line[end_col:]
            source_lines[start_line_idx] = new_line
        else:
            start_line_content = source_lines[start_line_idx][:start_col - 1]
            end_line_content = source_lines[end_line_idx][end_col:]
            for i in range(start_line_idx + 1, end_line_idx):
                source_lines[i] = ''
            source_lines[start_line_idx] = start_line_content + '\n'
            source_lines[end_line_idx] = end_line_content
            if start_line_idx + 1 == end_line_idx:
                source_lines[start_line_idx] = start_line_content + end_line_content

    return source_lines
