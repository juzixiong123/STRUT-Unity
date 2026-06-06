import clang.cindex
from clang.cindex import Index, CursorKind

from util import read_file, write_content


def remove_function_declarations(input_path, output_path):
    index = clang.cindex.Index.create()
    args = ['-I.', '-std=c11']
    code = read_file(input_path)
    unsaved_files = [(input_path, code)]
    tu = index.parse(input_path, args=args, unsaved_files=unsaved_files)

    remove_ranges = []

    for cursor in tu.cursor.walk_preorder():
        if cursor.kind == CursorKind.FUNCTION_DECL:
            if not cursor.is_definition():
                start_offset = cursor.extent.start.offset
                end_offset = cursor.extent.end.offset

                while end_offset < len(code) and code[end_offset] != ';':
                    end_offset += 1
                if end_offset < len(code):
                    end_offset += 1
                remove_ranges.append((start_offset, end_offset))

    remove_ranges.sort(reverse=True)

    modified_code = code
    for start_offset, end_offset in remove_ranges:
        length = end_offset - start_offset
        modified_code = modified_code[:start_offset] + ' ' * length + modified_code[end_offset:]
    write_content(output_path, modified_code)
    return modified_code


def remove_global_variable_assignments(input_path, output_path):
    index = Index.create()
    args = ['-I.', '-std=c11']
    code = read_file(input_path)
    translation_unit = index.parse(input_path, args=args)
    if not translation_unit:
        return ""

    lines_to_remove = []

    def visit_node(cursor, parent):
        if cursor.kind == CursorKind.VAR_DECL:
            if cursor.lexical_parent.kind == CursorKind.TRANSLATION_UNIT:
                tokens = list(cursor.get_tokens())
                equal_token = None
                for token in tokens:
                    if token.spelling == '=':
                        equal_token = token
                        break
                if equal_token:
                    start_loc = equal_token.extent.start
                    end_loc = tokens[-1].extent.start
                    lines_to_remove.append((start_loc.line, start_loc.column, end_loc.line, end_loc.column))

        for child in cursor.get_children():
            visit_node(child, cursor)

    root_cursor = translation_unit.cursor
    visit_node(root_cursor, None)

    code_lines = code.splitlines()

    for start_line, start_col, end_line, end_col in sorted(lines_to_remove, reverse=True):
        if start_line == end_line:
            code_lines[start_line - 1] = (code_lines[start_line - 1][:start_col - 1] +
                                          code_lines[start_line - 1][end_col:])
        else:
            code_lines[start_line - 1] = code_lines[start_line - 1][:start_col - 1]
            for line in range(start_line, end_line - 1):
                code_lines[line] = ""
            code_lines[end_line - 1] = code_lines[end_line - 1][end_col + 1:]

    new_code = "\n".join(code_lines)
    write_content(output_path, new_code)
    return new_code

