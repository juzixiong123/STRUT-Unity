import sys
import clang.cindex
from clang import cindex
from clang.cindex import CursorKind, TokenKind

clang.cindex.Config.set_library_path(r'C:/Users/bondc/.conda/envs/basketball/Lib/site-packages/clang/native')


def is_function_pointer(var_type):
    """
    Determines if the type is a function pointer type.
    :param var_type: variable type
    :return: True if it is a function pointer type, otherwise False
    """
    return var_type.kind == cindex.TypeKind.POINTER and var_type.get_pointee().kind == cindex.TypeKind.FUNCTIONPROTO


def process_file(file_path):
    """
    Processes the source file, finds the variables in the function, resolves the variable types
    and stores the variable types and global variable names.
    :param file_path: source file path
    :return: (variable_types, global_variables)
             variable_types: Set with elements (type name, type location)
             global_variables: Set with elements as global variable names
    """
    index = cindex.Index.create()
    translation_unit = index.parse(file_path)
    variable_types = set()
    global_variables = set()
    process_node(translation_unit.cursor, variable_types, global_variables)
    return variable_types, global_variables


def process_node(node, variable_types, global_variables):
    """
    Recursively traverse the AST node to find the function and handle the variables inside the function.
    :param node: current AST node
    :param variable_types: Collection of variable types
    :param global_variables: Collection of global variable names
    """
    if node.kind == cindex.CursorKind.FUNCTION_DECL:
        process_function_body(node, variable_types, global_variables)
    else:
        for child in node.get_children():
            process_node(child, variable_types, global_variables)


def process_function_body(node, variable_types, global_variables):
    """
    Handles variable declarations and references inside functions.
    :param node: function node
    :param variable_types: Collection of variable types
    :param global_variables: Collection of global variable names
    """
    for param in node.get_arguments():
        process_variable_declaration(param, variable_types)
    if node.result_type.kind != cindex.TypeKind.INVALID:
        process_type(node.result_type, variable_types)
    for child in node.get_children():
        if child.kind == cindex.CursorKind.DECL_STMT:
            for decl in child.get_children():
                if decl.kind == cindex.CursorKind.VAR_DECL:
                    process_variable_declaration(decl, variable_types)
        elif child.kind == cindex.CursorKind.COMPOUND_STMT:
            for inner_child in child.get_children():
                process_function_body(inner_child, variable_types, global_variables)
        elif child.kind == cindex.CursorKind.DECL_REF_EXPR:
            process_variable_reference(child, global_variables, variable_types)
        else:
            process_function_body(child, variable_types, global_variables)


def process_variable_declaration(var_decl, variable_types):
    """
    Handles variable declarations and resolves variable types.
    :param var_decl: variable Declaration Node
    :param variable_types: Collection of variable types
    """
    var_type = var_decl.type
    process_type(var_type, variable_types)


def process_variable_reference(ref_expr, global_variables, variable_types):
    """
    Handles variable references and determines if they are global variables.
    :param ref_expr: variable reference nodes
    :param global_variables: Collection of global variable names
    """
    process_variable_declaration(ref_expr.referenced, variable_types)
    var_decl = ref_expr.referenced
    if var_decl is not None and var_decl.kind == cindex.CursorKind.VAR_DECL:
        if is_global_variable(var_decl):
            global_variables.add(var_decl.spelling)


def is_global_variable(var_decl):
    """
    Determines whether a variable is a global variable.
    :param var_decl: variable Declaration Node
    :return: True if it is a global variable, otherwise False
    """
    if var_decl.semantic_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
        return True
    else:
        return False


def get_basic_type(var_type):
    """
    Recursively get the basic types, removing modifiers such as pointers, arrays, typedefs, etc.
    :param var_type: variable type
    :return: basic type
    """
    if var_type.kind == cindex.TypeKind.POINTER:
        return get_basic_type(var_type.get_pointee())
    elif var_type.kind == cindex.TypeKind.CONSTANTARRAY or var_type.kind == cindex.TypeKind.INCOMPLETEARRAY:
        return get_basic_type(var_type.get_array_element_type())
    elif var_type.kind == cindex.TypeKind.TYPEDEF:
        return get_basic_type(var_type.get_canonical())
    else:
        return var_type


def process_type(var_type, variable_types):
    """
    Recursively resolves variable types up to the base type and stores type and location information.
    :param var_type: variable type
    :param variable_types: Collection of variable types
    """
    base_type = get_basic_type(var_type)
    type_name = base_type.spelling
    variable_types.add((type_name))
    if not is_builtin_type(var_type):
        type_decl = var_type.get_declaration()
        if type_decl is not None and type_decl.kind != cindex.CursorKind.NO_DECL_FOUND:
            if type_decl.kind == cindex.CursorKind.TYPEDEF_DECL:
                underlying_type = var_type.get_canonical()
                process_type(underlying_type, variable_types)
            elif type_decl.kind == cindex.CursorKind.STRUCT_DECL:
                for field in type_decl.get_children():
                    if field.kind == cindex.CursorKind.FIELD_DECL:
                        field_type = field.type
                        process_type(field_type, variable_types)
            else:
                pass


def is_builtin_type(var_type):
    """
    Determines if the type is a basic type.
    :param var_type: variable type
    :return: True if it is a basic type, otherwise False
    """
    kind = var_type.kind
    # Determine whether the type is a basic type
    return kind in [
        cindex.TypeKind.VOID,
        cindex.TypeKind.BOOL,
        cindex.TypeKind.CHAR_U,
        cindex.TypeKind.UCHAR,
        cindex.TypeKind.CHAR16,
        cindex.TypeKind.CHAR32,
        cindex.TypeKind.USHORT,
        cindex.TypeKind.UINT,
        cindex.TypeKind.ULONG,
        cindex.TypeKind.ULONGLONG,
        cindex.TypeKind.UINT128,
        cindex.TypeKind.CHAR_S,
        cindex.TypeKind.SCHAR,
        cindex.TypeKind.WCHAR,
        cindex.TypeKind.SHORT,
        cindex.TypeKind.INT,
        cindex.TypeKind.LONG,
        cindex.TypeKind.LONGLONG,
        cindex.TypeKind.INT128,
        cindex.TypeKind.FLOAT,
        cindex.TypeKind.DOUBLE,
        cindex.TypeKind.LONGDOUBLE,
        cindex.TypeKind.NULLPTR,
    ]


def get_type_location(var_type):
    """
    Gets the declaration location of the type.
    :param var_type: variable type
    :return: type Location Information String
    """
    type_decl = var_type.get_declaration()
    if type_decl and type_decl.location.file:
        file_name = type_decl.location.file.name
        line = type_decl.location.line
        return f"{file_name}:{line}"
    else:
        return "unknown location"


def extract_function_info(output_path):
    variable_types, global_variables = process_file(output_path)
    return variable_types, global_variables


