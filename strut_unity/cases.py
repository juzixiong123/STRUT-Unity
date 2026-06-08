from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .analyzer import FunctionContext, Parameter, TypeField


@dataclass(frozen=True)
class InputValue:
    expr: str
    c_type: str
    value: str


@dataclass(frozen=True)
class ArgumentBinding:
    parameter: str
    c_type: str
    argument: str
    declarations: tuple[str, ...]
    inputs: tuple[InputValue, ...]


@dataclass(frozen=True)
class TestCase:
    desc: str
    bindings: tuple[ArgumentBinding, ...]
    expected: Any | None = None

    @property
    def args(self) -> tuple[str, ...]:
        return tuple(binding.argument for binding in self.bindings)

    @property
    def input_values(self) -> tuple[InputValue, ...]:
        return tuple(value for binding in self.bindings for value in binding.inputs)

    def identity(self) -> tuple[tuple[str, str], ...]:
        return tuple((value.expr, value.value) for value in self.input_values)


def default_ptr_entries(context: FunctionContext) -> list[dict[str, str]]:
    return [
        {
            "expr": parameter.name,
            "userVar": f"{_safe_name(parameter.name)}_PTRTO",
        }
        for parameter in context.parameters
        if parameter.type_kind == "pointer"
    ]


def to_original_seed_case(context: FunctionContext, case: TestCase, backend: bool = False) -> dict[str, Any]:
    inputs = [
        {
            "expr": value.expr,
            "type": value.c_type,
            "value": value.value,
        }
        for value in case.input_values
    ]
    if backend:
        inputs = convert_inputs_with_default_ptr(inputs, default_ptr_entries(context))

    outputs = []
    if case.expected is not None:
        outputs.append(
            {
                "expr": f"{context.name}({', '.join(parameter.name for parameter in context.parameters)})",
                "type": context.return_type,
                "value": case.expected,
            }
        )
    return {
        "desc": case.desc,
        "inputs": inputs,
        "stubins": [],
        "outputs": outputs,
        "doBoundary": 0,
        "ioins": [],
    }


def convert_inputs_with_default_ptr(inputs: list[dict[str, Any]], default_ptr: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [{**item, "expr": _convert_expr_with_default_ptr(str(item.get("expr", "")), default_ptr)} for item in inputs]


def generate_seed_cases(context: FunctionContext) -> list[TestCase]:
    defaults = [_binding_for_parameter(parameter, _default_value(parameter)) for parameter in context.parameters]
    cases: list[TestCase] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    for index, parameter in enumerate(context.parameters):
        if parameter.type_kind == "pointer" and _is_composite_type(parameter.pointee_type or "", parameter.fields or []):
            for field_path, field, value in _interesting_field_values(parameter, context.branch_conditions):
                bindings = list(defaults)
                bindings[index] = _binding_for_parameter(parameter, _default_value(parameter), overrides={field_path: value})
                case = TestCase(desc=f"{parameter.name}->{field.name}={value}", bindings=tuple(bindings))
                _append_case(cases, seen, case)

        for value in _interesting_values(parameter, context.branch_conditions):
            bindings = list(defaults)
            bindings[index] = _binding_for_parameter(parameter, value)
            case = TestCase(desc=f"{parameter.name}={value}", bindings=tuple(bindings))
            _append_case(cases, seen, case)

        if parameter.type_kind == "pointer" and _condition_mentions_null(parameter.name, context.branch_conditions):
            bindings = list(defaults)
            bindings[index] = _null_pointer_binding(parameter)
            case = TestCase(desc=f"{parameter.name}=NULL", bindings=tuple(bindings))
            _append_case(cases, seen, case)

    if not cases:
        case = TestCase(desc="default inputs", bindings=tuple(defaults))
        _append_case(cases, seen, case)
    return cases


def case_from_structured_inputs(context: FunctionContext, desc: str, values_by_expr: dict[str, object]) -> TestCase:
    normalized_values = {_normalize_expr(str(expr)): value for expr, value in values_by_expr.items()}
    bindings = []
    for parameter in context.parameters:
        value = _value_for_parameter(parameter, normalized_values)
        if parameter.type_kind == "pointer" and _format_value(value) == "NULL":
            bindings.append(_null_pointer_binding(parameter))
            continue
        bindings.append(_binding_for_parameter(parameter, _format_value(value), overrides=_format_overrides(normalized_values)))
    return TestCase(desc=desc, bindings=tuple(bindings))


def case_from_args(context: FunctionContext, desc: str, args: list[object]) -> TestCase:
    bindings = []
    for index, parameter in enumerate(context.parameters):
        value = args[index] if index < len(args) else _default_value(parameter)
        bindings.append(_binding_for_parameter(parameter, _format_value(value)))
    return TestCase(desc=desc, bindings=tuple(bindings))


def with_expected(case: TestCase, expected: Any) -> TestCase:
    return TestCase(desc=case.desc, bindings=case.bindings, expected=expected)


def _append_case(cases: list[TestCase], seen: set[tuple[tuple[str, str], ...]], case: TestCase) -> None:
    identity = case.identity()
    if identity in seen:
        return
    seen.add(identity)
    cases.append(case)


def _binding_for_parameter(
    parameter: Parameter,
    value: str,
    overrides: dict[str, str] | None = None,
) -> ArgumentBinding:
    name = _safe_name(parameter.name or "arg")
    if parameter.type_kind == "array":
        return _array_binding(parameter, name, value)
    if parameter.type_kind == "pointer":
        return _pointer_binding(parameter, name, value, overrides or {})
    return _basic_binding(parameter, value)


def _basic_binding(parameter: Parameter, value: str) -> ArgumentBinding:
    return ArgumentBinding(
        parameter=parameter.name,
        c_type=parameter.c_type,
        argument=parameter.name,
        declarations=(f"{parameter.c_type} {parameter.name} = {_literal_for_type(parameter.c_type, value)};",),
        inputs=(InputValue(expr=parameter.name, c_type=parameter.c_type, value=value),),
    )


def _array_binding(parameter: Parameter, safe_name: str, value: str) -> ArgumentBinding:
    element_type = parameter.element_type or parameter.pointee_type or "int"
    variable = f"{safe_name}_array"
    initializer = _literal_for_type(element_type, value)
    return ArgumentBinding(
        parameter=parameter.name,
        c_type=parameter.c_type,
        argument=variable,
        declarations=(f"{element_type} {variable}[1] = {{{initializer}}};",),
        inputs=(InputValue(expr=f"{parameter.name}[0]", c_type=element_type, value=value),),
    )


def _pointer_binding(
    parameter: Parameter,
    safe_name: str,
    value: str,
    overrides: dict[str, str],
) -> ArgumentBinding:
    pointee_type = parameter.pointee_type or _strip_pointer(parameter.c_type) or "int"
    if _is_composite_type(pointee_type, parameter.fields or []):
        return _composite_pointer_binding(parameter, safe_name, pointee_type, overrides)

    target = f"{safe_name}_PTRTO"
    literal = _literal_for_type(pointee_type, value)
    return ArgumentBinding(
        parameter=parameter.name,
        c_type=parameter.c_type,
        argument=target,
        declarations=(f"{pointee_type} {target}[1] = {{{literal}}};",),
        inputs=(InputValue(expr=f"{parameter.name}[0]", c_type=pointee_type, value=value),),
    )


def _null_pointer_binding(parameter: Parameter) -> ArgumentBinding:
    return ArgumentBinding(
        parameter=parameter.name,
        c_type=parameter.c_type,
        argument="NULL",
        declarations=(),
        inputs=(InputValue(expr=parameter.name, c_type=parameter.c_type, value="NULL"),),
    )


def _composite_pointer_binding(
    parameter: Parameter,
    safe_name: str,
    pointee_type: str,
    overrides: dict[str, str],
) -> ArgumentBinding:
    variable = f"{safe_name}_PTRTO"
    declarations = [f"{pointee_type} {variable}[1] = {{{{0}}}};"]
    inputs: list[InputValue] = []
    for field in parameter.fields or []:
        field_decls, field_inputs = _field_initialization(
            parameter.name,
            f"{variable}[0]",
            field,
            max_depth=2,
            overrides=overrides,
        )
        declarations.extend(field_decls)
        inputs.extend(field_inputs)
    return ArgumentBinding(
        parameter=parameter.name,
        c_type=parameter.c_type,
        argument=variable,
        declarations=tuple(declarations),
        inputs=tuple(inputs) or (InputValue(expr=f"*{parameter.name}", c_type=pointee_type, value="{0}"),),
    )


def _field_initialization(
    public_base: str,
    variable_base: str,
    field: TypeField,
    max_depth: int,
    overrides: dict[str, str],
) -> tuple[list[str], list[InputValue]]:
    access = f"{variable_base}.{field.name}"
    expr = f"{public_base}->{field.name}"
    normalized_expr = _normalize_expr(expr)
    if field.type_kind == "array":
        value = overrides.get(_normalize_expr(f"{expr}[0]"), _default_value_for_type(field.element_type or field.c_type))
        return [f"{access}[0] = {_literal_for_type(field.element_type or field.c_type, value)};"], [
            InputValue(expr=f"{expr}[0]", c_type=field.element_type or field.c_type, value=value)
        ]

    if field.type_kind == "pointer" and max_depth > 0:
        pointee_type = field.pointee_type or _strip_pointer(field.c_type) or "int"
        if _is_composite_type(pointee_type, field.fields):
            target = f"{_safe_name(variable_base)}_{field.name}_target"
            declarations = [f"{pointee_type} {target} = {{0}};", f"{access} = &{target};"]
            inputs: list[InputValue] = []
            for nested in field.fields or []:
                nested_decls, nested_inputs = _field_initialization(
                    f"{expr}", target, nested, max_depth=max_depth - 1, overrides=overrides
                )
                declarations.extend(nested_decls)
                inputs.extend(nested_inputs)
            if not inputs:
                inputs.append(InputValue(expr=expr, c_type=field.c_type, value=f"&{target}"))
            return declarations, inputs
        target = f"{_safe_name(variable_base)}_{field.name}_target"
        value = overrides.get(
            _normalize_expr(f"{expr}[0]"),
            overrides.get(_normalize_expr(f"*{expr}"), _default_value_for_type(pointee_type)),
        )
        return [f"{pointee_type} {target} = {_literal_for_type(pointee_type, value)};", f"{access} = &{target};"], [
            InputValue(expr=f"{expr}[0]", c_type=pointee_type, value=value)
        ]

    value = overrides.get(normalized_expr, _default_value_for_type(field.c_type))
    return [f"{access} = {_literal_for_type(field.c_type, value)};"], [
        InputValue(expr=expr, c_type=field.c_type, value=value)
    ]


def _interesting_values(parameter: Parameter, conditions: list[str]) -> list[str]:
    if parameter.type_kind not in {"basic", "pointer", "array"}:
        return [_default_value(parameter)]

    value_type = parameter.c_type
    if parameter.type_kind == "pointer":
        value_type = parameter.pointee_type or _strip_pointer(parameter.c_type) or "int"
    elif parameter.type_kind == "array":
        value_type = parameter.element_type or parameter.pointee_type or "int"

    defaults = _default_values_for_type(value_type)
    values = set(defaults)
    if _is_numeric_type(value_type):
        for condition in conditions:
            for name, op, raw_value in re.findall(r"\b([A-Za-z_]\w*)\b\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)", condition):
                if name == parameter.name:
                    values.update(_boundary_values(op, raw_value, value_type))
            for raw_value, op, name in re.findall(r"(-?\d+(?:\.\d+)?)\s*(<=|>=|==|!=|<|>)\s*\b([A-Za-z_]\w*)\b", condition):
                if name == parameter.name:
                    values.update(_boundary_values(_flip(op), raw_value, value_type))
    return sorted(values, key=_sort_key)


def _interesting_field_values(
    parameter: Parameter,
    conditions: list[str],
) -> list[tuple[str, TypeField, str]]:
    values: list[tuple[str, TypeField, str]] = []
    for field in parameter.fields or []:
        expr = _normalize_expr(f"{parameter.name}->{field.name}")
        field_values = set()
        for condition in conditions:
            normalized_condition = _normalize_expr(condition)
            pattern = rf"{re.escape(expr)}\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)(?:[fFuUlL]*)"
            for op, raw_value in re.findall(pattern, normalized_condition):
                field_values.update(_boundary_values(op, raw_value, field.c_type))
            reverse = rf"(-?\d+(?:\.\d+)?)(?:[fFuUlL]*)\s*(<=|>=|==|!=|<|>)\s*{re.escape(expr)}"
            for raw_value, op in re.findall(reverse, normalized_condition):
                field_values.update(_boundary_values(_flip(op), raw_value, field.c_type))
            variable_to_field = rf"\b[A-Za-z_]\w*\s*(<=|>=|<|>)\s*{re.escape(expr)}"
            if re.search(variable_to_field, normalized_condition):
                field_values.update(_default_values_for_type(field.c_type))
            field_to_variable = rf"{re.escape(expr)}\s*(<=|>=|<|>)\s*\b[A-Za-z_]\w*"
            if re.search(field_to_variable, normalized_condition):
                field_values.update(_default_values_for_type(field.c_type))
        for value in sorted(field_values, key=_sort_key):
            values.append((expr, field, value))
    return values


def _default_value(parameter: Parameter) -> str:
    if parameter.type_kind == "pointer":
        return _default_value_for_type(parameter.pointee_type or _strip_pointer(parameter.c_type) or "int")
    if parameter.type_kind == "array":
        return _default_value_for_type(parameter.element_type or parameter.pointee_type or "int")
    return _default_value_for_type(parameter.c_type)


def _value_for_parameter(parameter: Parameter, values_by_expr: dict[str, object]) -> object:
    for expr in _parameter_expr_candidates(parameter):
        if expr in values_by_expr:
            value = values_by_expr[expr]
            if isinstance(value, list):
                return value[0] if value else _default_value(parameter)
            return value
    return _default_value(parameter)


def _parameter_expr_candidates(parameter: Parameter) -> list[str]:
    candidates = [_normalize_expr(parameter.name)]
    if parameter.type_kind == "array":
        candidates.append(_normalize_expr(f"{parameter.name}[0]"))
    elif parameter.type_kind == "pointer":
        candidates.extend(
            [
                _normalize_expr(f"{parameter.name}[0]"),
                _normalize_expr(f"*{parameter.name}"),
            ]
        )
    return candidates


def _format_overrides(values_by_expr: dict[str, object]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for expr, value in values_by_expr.items():
        if isinstance(value, list):
            if value:
                overrides[expr] = _format_value(value[0])
            continue
        overrides[expr] = _format_value(value)
    return overrides


def _default_values_for_type(c_type: str) -> set[str]:
    if _is_bool_type(c_type):
        return {"0", "1"}
    if _is_float_type(c_type):
        return {"-1.0", "0.0", "1.0"}
    if _is_char_type(c_type):
        return {"0", "1"}
    if _is_int_like(c_type):
        return {"-1", "0", "1"}
    return {"0"}


def _default_value_for_type(c_type: str) -> str:
    if _is_float_type(c_type):
        return "0.0"
    return "0"


def _literal_for_type(c_type: str, value: str) -> str:
    if value == "NULL":
        return "NULL"
    if _is_float_type(c_type):
        return value if "." in value else f"{value}.0"
    if _is_bool_type(c_type):
        return "1" if str(value).lower() in {"1", "true"} else "0"
    if _is_char_type(c_type):
        return str(int(float(value))) if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", str(value)) else "'\\0'"
    return str(value)


def _boundary_values(op: str, raw_value: str, c_type: str) -> set[str]:
    if _is_float_type(c_type):
        value = float(raw_value)
        epsilon = 1.0
        values = {
            "<": {value - epsilon, value},
            ">=": {value - epsilon, value},
            "!=": {value - epsilon, value},
            ">": {value, value + epsilon},
            "<=": {value, value + epsilon},
        }.get(op, {value, value - epsilon, value + epsilon})
        return {f"{item:.6g}" for item in values}

    value = int(float(raw_value))
    values = {
        "<": {value - 1, value},
        ">=": {value - 1, value},
        "!=": {value - 1, value},
        ">": {value, value + 1},
        "<=": {value, value + 1},
    }.get(op, {value, value - 1, value + 1})
    return {str(item) for item in values}


def _condition_mentions_null(name: str, conditions: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b\s*(?:==|!=)\s*NULL|NULL\s*(?:==|!=)\s*\b{re.escape(name)}\b", c) for c in conditions)


def _is_composite_type(c_type: str, fields: list[TypeField]) -> bool:
    return bool(fields) or _normalize_type(c_type).startswith("struct ")


def _is_numeric_type(c_type: str) -> bool:
    return _is_int_like(c_type) or _is_float_type(c_type) or _is_bool_type(c_type) or _is_char_type(c_type)


def _is_int_like(c_type: str) -> bool:
    normalized = _normalize_type(c_type)
    return normalized in {
        "int",
        "signed int",
        "short",
        "short int",
        "long",
        "long int",
        "long long",
        "long long int",
        "unsigned",
        "unsigned int",
        "unsigned short",
        "unsigned short int",
        "unsigned long",
        "unsigned long int",
        "unsigned long long",
        "unsigned long long int",
        "size_t",
    }


def _is_float_type(c_type: str) -> bool:
    return _normalize_type(c_type) in {"float", "double"}


def _is_bool_type(c_type: str) -> bool:
    return _normalize_type(c_type) in {"bool", "_Bool"}


def _is_char_type(c_type: str) -> bool:
    return _normalize_type(c_type) in {"char", "signed char", "unsigned char"}


def _strip_pointer(c_type: str) -> str:
    return c_type.replace("*", "").strip()


def _normalize_type(c_type: str) -> str:
    return " ".join(c_type.replace("const ", "").split())


def _normalize_expr(expr: str) -> str:
    return re.sub(r"\s+", "", expr)


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def _sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def _safe_name(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "value"


def _convert_expr_with_default_ptr(expr: str, default_ptr: list[dict[str, str]]) -> str:
    pointer_names = {item["expr"] for item in default_ptr if "expr" in item}
    leading_deref = expr.startswith("*")
    expr_without_deref = expr[1:] if leading_deref else expr
    index = expr_without_deref.find("->")
    if index != -1:
        converted = expr_without_deref.replace("->", ".")
        pointer_param = converted[:index]
        square = "[0]"
        if "[" in pointer_param and "]" in pointer_param:
            start = pointer_param.find("[")
            end = pointer_param.find("]")
            name = pointer_param[:start]
            square = pointer_param[start : end + 1]
        else:
            name = pointer_param
        if name in pointer_names:
            converted_expr = f"({name}_PTRTO{square}){converted[index:]}"
            return f"*{converted_expr}" if leading_deref else converted_expr
        return converted

    if expr.startswith("*") and expr[1:] in pointer_names:
        return f"{expr[1:]}_PTRTO[0]"

    end = expr.find("[")
    if end != -1:
        name = expr[:end]
        suffix = expr[end:]
        if name in pointer_names:
            return f"{name}_PTRTO{suffix}"
    return expr


def _flip(op: str) -> str:
    return {
        "<": ">",
        ">": "<",
        "<=": ">=",
        ">=": "<=",
        "==": "==",
        "!=": "!=",
    }[op]
